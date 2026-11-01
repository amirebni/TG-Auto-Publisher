"""Text cleaning that keeps Telegram formatting.

Messages are converted to Telegram-flavoured HTML (bold, italic, links...),
cleaned according to the source's text_rules, and sent back with
parse_mode="html".
"""
import html as html_lib
import re

from telethon.extensions import html as tg_html

_HOSTS = r"(?:t\.me|telegram\.me|telegram\.dog|telesco\.pe)"

TELEGRAM_LINK_RE = re.compile(
    rf'(?i)(?<!["\w/])(?:https?://)?(?:www\.)?{_HOSTS}/[^\s<>"]+'
)
TG_HREF_RE = re.compile(
    rf"(?i)^(?:tg://|(?:https?://)?(?:www\.)?{_HOSTS}/)"
)
URL_RE = re.compile(r'(?i)(?<!["\w/])(?:https?://|www\.)[^\s<>"]+')
USERNAME_RE = re.compile(r"(?<![\w/.:=@-])@[A-Za-z0-9_]{3,32}\b")
HASHTAG_RE = re.compile(r"(?<![\w&/=])#[\w؀-ۿ‌]+")

ANCHOR_RE = re.compile(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', re.I | re.S)
TG_EMOJI_RE = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
TAG_TOKEN_RE = re.compile(r"<(/?)([a-zA-Z][\w-]*)([^>]*)>")
EMPTY_TAG_RE = re.compile(r"<([a-zA-Z][\w-]*)(?:\s[^>]*)?>(\s*)</\1>")


def message_html(message):
    """Telegram message -> HTML string (formatting preserved)."""
    text = getattr(message, "raw_text", None)
    if text is None:
        text = getattr(message, "message", "") or ""
    entities = getattr(message, "entities", None) or []
    return tg_html.unparse(text, entities)


def plain_text(markup):
    """HTML string -> visible plain text."""
    return tg_html.parse(markup or "")[0]


def visible_len(markup):
    """Length Telegram will count (UTF-16 units of the visible text)."""
    return len(plain_text(markup).encode("utf-16-le")) // 2


def _balance(markup):
    """Remove stray closing tags and close unclosed ones."""
    stack, out, pos = [], [], 0

    for m in TAG_TOKEN_RE.finditer(markup):
        out.append(markup[pos:m.start()])
        pos = m.end()
        closing, name = m.group(1), m.group(2).lower()

        if closing:
            if name in stack:
                while stack:
                    top = stack.pop()
                    out.append(f"</{top}>")
                    if top == name:
                        break
        else:
            stack.append(name)
            out.append(m.group(0))

    out.append(markup[pos:])

    while stack:
        out.append(f"</{stack.pop()}>")

    return "".join(out)


def _drop_empty_tags(markup):
    previous = None
    while previous != markup:
        previous = markup
        markup = EMPTY_TAG_RE.sub(r"\2", markup)
    return markup


def _drop_lines(markup, needles):
    if not needles:
        return markup

    needles = [str(n).lower() for n in needles if str(n)]
    kept = []

    for line in markup.split("\n"):
        visible = html_lib.unescape(TAG_RE.sub("", line)).lower()
        if any(n in visible for n in needles):
            continue
        kept.append(line)

    return "\n".join(kept)


def _normalize_space(markup):
    markup = re.sub(r"[ \t]{2,}", " ", markup)
    markup = re.sub(r"[ \t]*\n[ \t]*", "\n", markup)
    markup = re.sub(r"\n{3,}", "\n\n", markup)
    return markup.strip()


def clean_html(markup, rules=None):
    rules = rules or {}

    if not markup:
        return ""

    out = TG_EMOJI_RE.sub(r"\1", markup)

    def handle_anchor(m):
        href, inner = m.group(1), m.group(2)

        if TG_HREF_RE.match(html_lib.unescape(href)):
            if rules.get("remove_telegram_links", True):
                return ""
            return m.group(0)

        if rules.get("remove_external_links", False):
            return inner

        return m.group(0)

    out = ANCHOR_RE.sub(handle_anchor, out)

    if rules.get("remove_telegram_links", True):
        out = TELEGRAM_LINK_RE.sub("", out)

    if rules.get("remove_mentions") or rules.get("remove_usernames"):
        out = USERNAME_RE.sub("", out)

    if rules.get("remove_hashtags"):
        out = HASHTAG_RE.sub("", out)

    if rules.get("remove_external_links"):
        out = URL_RE.sub("", out)

    out = _drop_lines(out, rules.get("drop_lines_containing"))
    out = _balance(out)
    out = _drop_empty_tags(out)

    return _normalize_space(out)


def clean_text(text, rules=None):
    """Plain-text convenience wrapper (escapes, cleans, returns HTML)."""
    if not text:
        return ""
    return clean_html(html_lib.escape(text, quote=False), rules)


def split_text(text, limit=4000):
    """Split plain text into chunks, preferring paragraph boundaries."""
    chunks = []
    text = text.strip()

    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit

        chunks.append(text[:cut].strip())
        text = text[cut:].strip()

    if text:
        chunks.append(text)

    return chunks
