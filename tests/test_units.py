import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

from app.config import ConfigError, load_config
from app.database import StateDB
from app.media import media_type
from app.resolver import normalize_peer, source_key
from app.router import destinations_for
from app.text_cleaner import (
    clean_html,
    clean_text,
    message_html,
    split_text,
    visible_len,
)
from tests.helpers import (
    MessageEntityBold,
    MessageEntityTextUrl,
    Msg,
    music,
    photo,
    video,
    voice,
    webpage,
)


# ---------- media ----------
def test_media_types():
    assert media_type(Msg(1, "hi")) == "text"
    assert media_type(Msg(1, "hi", media=webpage())) == "text"  # was the bug
    assert media_type(Msg(1, media=photo())) == "photo"
    assert media_type(Msg(1, media=video())) == "video"
    assert media_type(Msg(1, media=music())) == "music"
    assert media_type(Msg(1, media=voice())) == "voice"
    assert media_type(Msg(1, media=object())) == "other"


# ---------- text cleaner ----------
def test_removes_telegram_links_and_usernames():
    rules = {"remove_telegram_links": True, "remove_usernames": True}
    out = clean_text("Hello https://t.me/chan and @chan_name bye", rules)
    assert "t.me" not in out and "@chan_name" not in out
    assert "Hello" in out and "bye" in out


def test_hidden_telegram_link_removed_external_kept():
    m = Msg(
        1, "join us and read more",
        entities=[
            MessageEntityTextUrl(0, 8, "https://t.me/secret"),
            MessageEntityTextUrl(13, 9, "https://example.com/a"),
        ],
    )
    out = clean_html(message_html(m), {"remove_telegram_links": True})
    assert "t.me" not in out
    assert "example.com" in out


def test_external_links_unwrapped_when_enabled():
    m = Msg(1, "read more", entities=[
        MessageEntityTextUrl(0, 9, "https://example.com/a")])
    out = clean_html(message_html(m), {"remove_external_links": True})
    assert "example.com" not in out and "read more" in out


def test_formatting_preserved_and_balanced():
    m = Msg(1, "bold text", entities=[MessageEntityBold(0, 4)])
    out = clean_html(message_html(m), {})
    assert "<strong>bold</strong>" in out


def test_hashtags_and_entity_safe():
    out = clean_text("it's #tag and #فارسی ok", {"remove_hashtags": True})
    assert "#tag" not in out and "#فارسی" not in out
    assert out == "it's and ok"  # apostrophe not damaged by hashtag rule


def test_drop_lines_keeps_html_valid():
    markup = "<strong>keep\nSUBSCRIBE now</strong>\nend"
    out = clean_html(markup, {"drop_lines_containing": ["subscribe"]})
    assert "SUBSCRIBE" not in out
    assert out.count("<strong>") == out.count("</strong>")


def test_empty_after_cleaning():
    assert clean_text("@only_a_username", {"remove_usernames": True}) == ""


def test_visible_len_ignores_tags():
    assert visible_len("<strong>abc</strong>") == 3


def test_split_text():
    chunks = split_text(("word " * 3000).strip(), 4000)
    assert all(len(c) <= 4000 for c in chunks) and len(chunks) > 1


# ---------- router ----------
def test_router():
    cfg = {"destinations": ["@a"], "routes": {"music": ["@m", "@m"],
                                               "default": ["@d"]}}
    assert destinations_for(cfg, "music") == ["@m"]
    assert destinations_for(cfg, "photo") == ["@d"]
    assert destinations_for({"destinations": "@x"}, "text") == ["@x"]


# ---------- resolver helpers ----------
def test_peer_normalization():
    assert normalize_peer("-100123") == -100123
    assert source_key("@Chan") == "chan"


# ---------- database ----------
def test_database():
    with tempfile.TemporaryDirectory() as d:
        db = StateDB(os.path.join(d, "s.sqlite3"))
        assert not db.has_cursor("a")
        db.set_cursor("a", 5)
        db.set_cursor("a", 3)  # never goes backwards
        assert db.cursor("a") == 5 and db.has_cursor("a")
        assert not db.is_processed("a", "k")
        db.mark_processed("a", "k")
        assert db.is_processed("a", "k")
        assert db.add_failure("a", 1) == 1
        assert db.add_failure("a", 1) == 2
        db.clear_failure("a", 1)
        assert db.add_failure("a", 1) == 1
        db.prune(30)
        assert db.is_processed("a", "k")
        db.close()


# ---------- config ----------
def test_config_validation(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("sources:\n  - peer: '@a'\n    enabled: true\n")
    with pytest.raises(ConfigError):
        load_config(p)

    p.write_text(
        "sources:\n  - peer: '@a'\n    destinations: ['@b']\n"
        "    allowed_types: [music, bogus]\n"
    )
    with pytest.raises(ConfigError):
        load_config(p)

    p.write_text("sources:\n  - peer: '@a'\n    destinations: ['@b']\n")
    cfg = load_config(p)
    assert cfg["settings"]["dry_run"] is True
    assert cfg["settings"]["state_db"].endswith("state.sqlite3")


def test_dry_run_override(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("settings:\n  dry_run: true\nsources: []\n")
    monkeypatch.setenv("DRY_RUN", "false")
    assert load_config(p)["settings"]["dry_run"] is False
