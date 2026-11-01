"""Resolve channel/group references to Telegram entities.

A StringSession has no entity cache, so numeric IDs (private channels)
cannot be resolved until the dialog list has been loaded once.
"""
import re


def normalize_peer(peer):
    if isinstance(peer, int):
        return peer

    text = str(peer).strip()

    if re.fullmatch(r"-?\d+", text):
        return int(text)

    return text


def source_key(peer):
    """Stable key used in the state database."""
    peer = normalize_peer(peer)
    if isinstance(peer, str):
        return peer.lstrip("@").lower()
    return str(peer)


class Resolver:

    def __init__(self, client):
        self.client = client
        self._cache = {}
        self._dialogs_loaded = False

    async def get(self, peer):
        key = normalize_peer(peer)

        if key in self._cache:
            return self._cache[key]

        try:
            entity = await self.client.get_entity(key)
        except ValueError:
            if self._dialogs_loaded:
                raise
            await self.client.get_dialogs()
            self._dialogs_loaded = True
            entity = await self.client.get_entity(key)

        self._cache[key] = entity
        return entity
