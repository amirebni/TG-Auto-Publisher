"""Entry point: poll sources, publish new posts, keep state."""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from app.config import ConfigError, get_telegram_credentials, load_config
from app.database import StateDB
from app.publisher import DRY, publish_group
from app.resolver import Resolver, source_key

log = logging.getLogger("tg_auto_publisher")

OK = "ok"
STOP_SOURCE = "stop_source"   # retry later, keep order
STOP_RUN = "stop_run"         # flood wait / time budget


def setup_logging(level):
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def build_groups(messages):
    """Group album messages together, keep order."""
    groups, albums = [], {}

    for message in messages:
        gid = getattr(message, "grouped_id", None)

        if gid is None:
            groups.append([message])
        elif gid in albums:
            albums[gid].append(message)
        else:
            albums[gid] = [message]
            groups.append(albums[gid])

    return groups


def select_ready(messages, settle_seconds, hit_limit):
    """Drop posts that may still be incomplete.

    * posts newer than settle_seconds (an album may still be arriving)
    * a trailing album cut off by poll_limit
    """
    now = datetime.now(timezone.utc)
    ready = []

    for message in messages:
        date = getattr(message, "date", None)
        if date is not None:
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            if (now - date).total_seconds() < settle_seconds:
                hit_limit = False  # stopped by age, not by limit
                break
        ready.append(message)

    if hit_limit and ready:
        gid = getattr(ready[-1], "grouped_id", None)
        if gid is not None:
            trimmed = [m for m in ready if getattr(m, "grouped_id", None) != gid]
            if trimmed:
                ready = trimmed

    return ready


async def process_source(client, resolver, source, settings, db, deadline):
    peer = source["peer"]
    name = source.get("name") or str(peer)
    skey = source_key(peer)
    dry_run = bool(settings["dry_run"])

    entity = await resolver.get(peer)

    # first run: start from the newest post, do not re-publish history
    if not db.has_cursor(skey) and settings["first_run_behavior"] == "latest":
        latest = await client.get_messages(entity, limit=1)
        start_id = latest[0].id if latest else 0
        db.set_cursor(skey, start_id)
        log.info("Initialized %s at message %s", name, start_id)
        return OK

    last_id = db.cursor(skey)
    limit = int(settings["poll_limit"])

    raw = []
    async for message in client.iter_messages(
        entity, min_id=last_id, reverse=True, limit=limit
    ):
        if message is not None:
            raw.append(message)

    if not raw:
        log.info("No new posts in %s", name)
        return OK

    ready = select_ready(
        raw, int(settings["settle_seconds"]), hit_limit=len(raw) >= limit
    )

    if not ready:
        log.info("New posts in %s are too fresh, waiting for next run", name)
        return OK

    for group in build_groups(ready):
        if time.monotonic() > deadline:
            log.warning("Time budget reached, stopping run")
            return STOP_RUN

        last_group_id = max(m.id for m in group)

        # service messages (pins, joins...) are just skipped
        if getattr(group[0], "action", None):
            db.set_cursor(skey, last_group_id)
            continue

        try:
            status = await publish_group(
                client, resolver, source, group, db, settings,
                dry_run=dry_run,
            )

        except FloodWaitError as exc:
            log.warning("FloodWait %ss - stopping run", exc.seconds)
            return STOP_RUN

        except Exception:
            log.exception("Failed to publish %s / %s", name, group[0].id)

            count = db.add_failure(skey, group[0].id)

            if count >= int(settings["max_retries"]):
                log.error(
                    "Giving up on %s / %s after %d failures - skipped",
                    name, group[0].id, count
                )
                db.set_cursor(skey, last_group_id)
                db.clear_failure(skey, group[0].id)
                continue

            # keep order: retry this post on the next run
            return STOP_SOURCE

        if status != DRY:
            db.set_cursor(skey, last_group_id)
            db.clear_failure(skey, group[0].id)

    return OK


async def run():
    cfg = load_config()
    settings = cfg["settings"]

    setup_logging(settings["log_level"])

    sources = [s for s in cfg["sources"] if s.get("enabled", True)]

    if not sources:
        log.info("No enabled sources in config.yaml - nothing to do")
        return 0

    api_id, api_hash, session = get_telegram_credentials()

    log.info(
        "Starting: %d source(s), dry_run=%s",
        len(sources), settings["dry_run"]
    )

    db = StateDB(settings["state_db"])
    db.prune(settings["state_keep_days"])

    client = TelegramClient(
        StringSession(session),
        api_id,
        api_hash,
        flood_sleep_threshold=int(settings["max_flood_wait"]),
        receive_updates=False,
    )

    deadline = time.monotonic() + float(settings["max_runtime_seconds"])

    try:
        # connect() instead of start(): never asks for a phone number in CI
        await client.connect()

        if not await client.is_user_authorized():
            log.error(
                "TG_SESSION is invalid or was revoked. "
                "Run scripts/create_session.py again and update the secret."
            )
            return 1

        resolver = Resolver(client)

        for source in sources:
            try:
                result = await process_source(
                    client, resolver, source, settings, db, deadline
                )
            except Exception:
                log.exception(
                    "Source %s failed", source.get("name") or source["peer"]
                )
                continue

            if result == STOP_RUN:
                break

    finally:
        await client.disconnect()
        db.close()

    return 0


def check_config():
    cfg = load_config()
    setup_logging("INFO")
    enabled = [s for s in cfg["sources"] if s.get("enabled", True)]
    log.info(
        "Config OK: %d source(s), %d enabled, dry_run=%s",
        len(cfg["sources"]), len(enabled), cfg["settings"]["dry_run"]
    )
    return 0


def main():
    try:
        if "--check" in sys.argv:
            return check_config()
        return asyncio.run(run())
    except (ConfigError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
