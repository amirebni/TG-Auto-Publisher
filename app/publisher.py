"""Publishing engine: re-posts messages as new posts (no Forward header)."""
import asyncio
import logging
import tempfile
from pathlib import Path

from app.media import file_size, media_type
from app.resolver import source_key
from app.router import destinations_for
from app.text_cleaner import (
    clean_html,
    message_html,
    plain_text,
    split_text,
    visible_len,
)

log = logging.getLogger("tg_auto_publisher")

TEXT_LIMIT = 4096
DEFAULT_TYPES = ["text", "photo", "video", "music", "voice", "document"]

PUBLISHED = "published"
SKIPPED = "skipped"
DRY = "dry"


def _raw(message):
    return (getattr(message, "raw_text", None)
            or getattr(message, "message", "") or "")


def build_text(message, rules, for_caption=False):
    """Cleaned HTML text of a message."""
    if for_caption and rules.get("remove_caption", False):
        return ""
    return clean_html(message_html(message), rules)


def _with_footer(markup, rules):
    footer = rules.get("append_text")
    if not footer:
        return markup
    return f"{markup}\n\n{footer}" if markup else footer


def _has_blocked_word(messages, rules):
    words = [str(w).lower() for w in rules.get("skip_if_contains") or [] if w]
    if not words:
        return None
    text = " ".join(_raw(m) for m in messages).lower()
    return next((w for w in words if w in text), None)


async def _send_text(client, entity, markup, link_preview):
    if visible_len(markup) <= TEXT_LIMIT:
        await client.send_message(
            entity, markup, parse_mode="html", link_preview=link_preview
        )
        return

    # too long: fall back to plain text chunks
    for chunk in split_text(plain_text(markup)):
        await client.send_message(
            entity, chunk, parse_mode=None, link_preview=False
        )


def _single_upload_options(message, kind):
    """Keep filename / audio tags / video info when re-uploading a file."""
    opts = {}
    doc = getattr(getattr(message, "media", None), "document", None)
    attrs = getattr(doc, "attributes", None)

    if attrs:
        opts["attributes"] = list(attrs)

    if kind == "voice":
        opts["voice_note"] = True
    elif kind == "video":
        opts["supports_streaming"] = True
    elif kind == "document":
        opts["force_document"] = True

    return opts


async def _download_and_send(client, entity, messages, captions, kind):
    with tempfile.TemporaryDirectory(prefix="tg-auto-publisher-") as tmp:
        paths = []

        for i, message in enumerate(messages):
            folder = Path(tmp) / str(i)
            folder.mkdir()
            # a directory keeps the original file name
            path = await client.download_media(message, file=f"{folder}/")
            if not path:
                raise RuntimeError(f"Could not download media {message.id}")
            paths.append(path)

        if len(paths) > 1:
            await client.send_file(
                entity, paths, caption=captions, parse_mode="html"
            )
        else:
            await client.send_file(
                entity,
                paths[0],
                caption=captions[0],
                parse_mode="html",
                **_single_upload_options(messages[0], kind),
            )


async def _send_media(client, entity, messages, captions, kind, settings):
    limit = int(settings.get("caption_limit", 1024))

    media_captions, extra_texts = [], []
    for caption in captions:
        if caption and visible_len(caption) > limit:
            media_captions.append("")
            extra_texts.append(caption)
        else:
            media_captions.append(caption)

    sent = False

    if settings.get("media_mode", "auto") == "auto":
        try:
            files = [m.media for m in messages]
            if len(files) > 1:
                await client.send_file(
                    entity, files, caption=media_captions, parse_mode="html"
                )
            else:
                await client.send_file(
                    entity, files[0], caption=media_captions[0],
                    parse_mode="html",
                )
            sent = True
        except Exception as exc:
            # FloodWait must stop the run, not trigger a re-upload
            if type(exc).__name__ == "FloodWaitError":
                raise
            log.warning(
                "Direct media send failed (%s: %s); falling back to "
                "download + upload", type(exc).__name__, exc
            )

    if not sent:
        await _download_and_send(
            client, entity, messages, media_captions, kind
        )

    for text in extra_texts:
        await _send_text(client, entity, text, False)


async def publish_group(
    client, resolver, source_cfg, messages, db, settings, dry_run=False
):
    """Publish one post (single message or a whole album).

    Returns PUBLISHED, SKIPPED (intentionally filtered) or DRY.
    Raises on errors so the caller can retry.
    """
    first = messages[0]
    kind = media_type(first)
    name = source_cfg.get("name") or str(source_cfg.get("peer"))
    skey = source_key(source_cfg["peer"])
    rules = source_cfg.get("text_rules") or {}

    allowed = source_cfg.get("allowed_types") or DEFAULT_TYPES

    if kind not in allowed:
        log.info("Filtered %s (%s) from %s", first.id, kind, name)
        return SKIPPED

    if source_cfg.get("respect_noforwards", True) and any(
        getattr(m, "noforwards", False) for m in messages
    ):
        log.warning(
            "Skipped %s from %s: channel has content protection enabled",
            first.id, name
        )
        return SKIPPED

    blocked = _has_blocked_word(messages, rules)
    if blocked:
        log.info("Skipped %s from %s: contains %r", first.id, name, blocked)
        return SKIPPED

    routes = destinations_for(source_cfg, kind)
    if not routes:
        log.info("No destination for %s (%s)", kind, name)
        return SKIPPED

    group_id = getattr(first, "grouped_id", None)
    key = f"group:{group_id}" if group_id else f"message:{first.id}"

    if db.is_processed(skey, key):
        return SKIPPED

    max_bytes = int(settings.get("max_file_mb", 200)) * 1024 * 1024

    if kind == "text":
        body = _with_footer(build_text(first, rules), rules)
        captions = []
        if not plain_text(body).strip():
            log.info("Skipped empty text %s from %s", first.id, name)
            return SKIPPED
    else:
        for m in messages:
            size = file_size(m)
            if size and size > max_bytes:
                log.warning(
                    "Skipped %s from %s: file is %.0f MB (limit %s MB)",
                    first.id, name, size / 1024 / 1024,
                    settings.get("max_file_mb", 200)
                )
                return SKIPPED

        captions = [build_text(m, rules, for_caption=True) for m in messages]
        captions[0] = _with_footer(captions[0], rules)
        body = None

    if dry_run:
        log.info(
            "[DRY RUN] source=%s type=%s items=%d destinations=%s text=%r",
            name, kind, len(messages), routes,
            body if kind == "text" else captions,
        )
        return DRY

    delay = float(settings.get("send_delay", 1.0))

    for destination in routes:
        dkey = f"{key}|{destination}"

        if db.is_processed(skey, dkey):
            continue

        entity = await resolver.get(destination)

        if kind == "text":
            await _send_text(
                client, entity, body,
                bool(source_cfg.get("link_preview", False)),
            )
        else:
            await _send_media(
                client, entity, messages, captions, kind, settings
            )

        db.mark_processed(skey, dkey)
        log.info(
            "Published %s (%s, %d item(s)) -> %s",
            first.id, kind, len(messages), destination
        )

        if delay > 0:
            await asyncio.sleep(delay)

    db.mark_processed(skey, key)
    return PUBLISHED
