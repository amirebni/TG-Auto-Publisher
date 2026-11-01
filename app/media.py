"""Detect the content type of a Telegram message."""
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)


def media_type(message):
    """Returns: text, photo, video, music, voice, document or other.

    "other" (polls, locations, contacts, stickers, ...) is never published.
    """
    media = getattr(message, "media", None)

    # a link preview is just a text message
    if media is None or isinstance(media, MessageMediaWebPage):
        return "text"

    if isinstance(media, MessageMediaPhoto):
        return "photo"

    if isinstance(media, MessageMediaDocument):
        doc = getattr(media, "document", None)

        if doc is None:
            return "other"

        attrs = getattr(doc, "attributes", None) or []

        if any(isinstance(a, DocumentAttributeSticker) for a in attrs):
            return "other"

        if any(
            isinstance(a, DocumentAttributeAudio)
            and getattr(a, "voice", False)
            for a in attrs
        ):
            return "voice"

        if any(isinstance(a, DocumentAttributeAudio) for a in attrs):
            return "music"

        if any(isinstance(a, DocumentAttributeVideo) for a in attrs):
            return "video"

        mime = (getattr(doc, "mime_type", "") or "").lower()

        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("audio/"):
            return "music"

        return "document"

    return "other"


def file_size(message):
    """File size in bytes, or None if unknown."""
    f = getattr(message, "file", None)
    return getattr(f, "size", None) if f is not None else None
