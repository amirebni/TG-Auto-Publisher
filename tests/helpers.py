"""Fakes used by the tests (no network, no Telegram)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    MessageEntityBold,
    MessageEntityTextUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

OLD = datetime.now(timezone.utc) - timedelta(hours=1)


class Msg:
    def __init__(self, id, text="", media=None, grouped_id=None,
                 entities=None, date=None, action=None, noforwards=False,
                 size=None):
        self.id = id
        self.raw_text = text
        self.message = text
        self.media = media
        self.grouped_id = grouped_id
        self.entities = entities or []
        self.date = date or OLD
        self.action = action
        self.noforwards = noforwards
        self.file = SimpleNamespace(size=size) if size else None


def photo():
    return MessageMediaPhoto()


def video():
    doc = SimpleNamespace(
        attributes=[DocumentAttributeVideo(duration=1, w=1, h=1)],
        mime_type="video/mp4",
    )
    return MessageMediaDocument(document=doc)


def music():
    doc = SimpleNamespace(
        attributes=[DocumentAttributeAudio(duration=1, title="t")],
        mime_type="audio/mpeg",
    )
    return MessageMediaDocument(document=doc)


def voice():
    doc = SimpleNamespace(
        attributes=[DocumentAttributeAudio(duration=1, voice=True)],
        mime_type="audio/ogg",
    )
    return MessageMediaDocument(document=doc)


def webpage():
    return MessageMediaWebPage(webpage=None)


class FakeClient:
    """Records what would be sent."""

    def __init__(self, messages=None, fail_direct=False, fail_times=0):
        self.messages = messages or []
        self.sent = []
        self.fail_direct = fail_direct
        self.fail_times = fail_times
        self.calls = 0

    async def get_entity(self, peer):
        return SimpleNamespace(id=1, peer=peer)

    async def get_dialogs(self):
        return []

    async def get_messages(self, entity, limit=1):
        return sorted(self.messages, key=lambda m: -m.id)[:limit]

    async def iter_messages(self, entity, min_id=0, reverse=True, limit=100):
        items = sorted(
            (m for m in self.messages if m.id > min_id), key=lambda m: m.id
        )
        for m in items[:limit]:
            yield m

    async def send_message(self, entity, text, **kw):
        self._maybe_fail()
        self.sent.append(("message", entity.peer, text, kw))

    async def send_file(self, entity, file, **kw):
        self._maybe_fail()
        self.sent.append(("file", entity.peer, file, kw))

    async def download_media(self, message, file=None):
        return f"{file}file_{message.id}.bin"

    def _maybe_fail(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")


__all__ = [
    "Msg", "FakeClient", "photo", "video", "music", "voice", "webpage",
    "MessageEntityBold", "MessageEntityTextUrl", "OLD",
]
