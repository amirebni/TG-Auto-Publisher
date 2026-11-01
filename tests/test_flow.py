"""End-to-end tests of the polling loop with a fake Telegram client."""
import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import DEFAULT_SETTINGS
from app.database import StateDB
from app.main import (
    OK, STOP_SOURCE, build_groups, process_source, select_ready,
)
from app.resolver import Resolver
from tests.helpers import FakeClient, Msg, music, photo, video, voice, webpage


def make_env(messages, source=None, **settings_over):
    settings = dict(DEFAULT_SETTINGS)
    settings.update({"dry_run": False, "send_delay": 0})
    settings.update(settings_over)
    client = FakeClient(messages)
    tmp = tempfile.mkdtemp()
    db = StateDB(os.path.join(tmp, "s.sqlite3"))
    src = {
        "name": "src", "peer": "@src", "destinations": ["@dst"],
        "text_rules": {"remove_telegram_links": True, "remove_usernames": True},
    }
    src.update(source or {})
    return client, Resolver(client), src, settings, db


def run(client, resolver, src, settings, db, deadline=None):
    return asyncio.run(process_source(
        client, resolver, src, settings, db,
        deadline or time.monotonic() + 100,
    ))


def test_first_run_initializes_without_publishing():
    client, res, src, st, db = make_env([Msg(1, "old"), Msg(2, "older")])
    assert run(client, res, src, st, db) == OK
    assert client.sent == [] and db.cursor("src") == 2


def test_publishes_new_posts_and_cleans_text():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages += [
        Msg(2, "Hello @spam https://t.me/x"),
        Msg(3, "page", media=webpage()),          # link preview = text
        Msg(4, "song", media=music()),
    ]
    run(client, res, src, st, db)

    kinds = [s[0] for s in client.sent]
    assert kinds == ["message", "message", "file"]
    assert client.sent[0][2] == "Hello"
    assert db.cursor("src") == 4

    # second run: nothing duplicated
    n = len(client.sent)
    run(client, res, src, st, db)
    assert len(client.sent) == n


def test_album_published_once_as_one_album():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages += [
        Msg(2, "caption", media=photo(), grouped_id=77),
        Msg(3, "", media=photo(), grouped_id=77),
        Msg(4, "", media=photo(), grouped_id=77),
    ]
    run(client, res, src, st, db)
    assert len(client.sent) == 1
    kind, dest, files, kw = client.sent[0]
    assert len(files) == 3 and kw["caption"][0] == "caption"


def test_fresh_posts_wait_for_next_run():
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "new", date=fresh))
    run(client, res, src, st, db)
    assert client.sent == [] and db.cursor("src") == 1


def test_trailing_album_cut_by_limit_is_deferred():
    msgs = [Msg(1, "a"), Msg(2, "b", media=photo(), grouped_id=5),
            Msg(3, "", media=photo(), grouped_id=5)]
    ready = select_ready(msgs, 0, hit_limit=True)
    assert [m.id for m in ready] == [1]
    assert [len(g) for g in build_groups(msgs)] == [1, 2]


def test_failed_post_is_retried_then_skipped():
    client, res, src, st, db = make_env([Msg(1, "old")], max_retries=2)
    run(client, res, src, st, db)
    client.messages += [Msg(2, "will fail"), Msg(3, "next")]
    client.fail_times = 100  # every send fails

    assert run(client, res, src, st, db) == STOP_SOURCE
    assert db.cursor("src") == 1            # not lost, not skipped yet

    run(client, res, src, st, db)           # 2nd failure -> give up on #2
    assert db.cursor("src") >= 2


def test_recovers_after_temporary_error():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "hello"))
    client.fail_times = 1
    run(client, res, src, st, db)
    assert client.sent == [] and db.cursor("src") == 1
    run(client, res, src, st, db)
    assert len(client.sent) == 1 and db.cursor("src") == 2


def test_dry_run_publishes_nothing_and_keeps_cursor():
    client, res, src, st, db = make_env([Msg(1, "old")], dry_run=True)
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "hello"))
    run(client, res, src, st, db)
    assert client.sent == [] and db.cursor("src") == 1


def test_filters_and_routes():
    src = {
        "allowed_types": ["music", "text"],
        "routes": {"music": ["@music"], "default": ["@other"]},
        "text_rules": {"skip_if_contains": ["ad"], "append_text": "via me"},
    }
    client, res, st_src, st, db = make_env([Msg(1, "old")], source=src)
    run(client, res, st_src, st, db)
    client.messages += [
        Msg(2, media=photo()),                 # filtered type
        Msg(3, "buy this ad now"),             # blocked word
        Msg(4, "plain post"),                  # routed to default
        Msg(5, media=music()),                 # routed to music
        Msg(6, media=voice()),                 # filtered type
    ]
    run(client, res, st_src, st, db)
    sent = [(s[0], s[1]) for s in client.sent]
    assert sent == [("message", "@other"), ("file", "@music")]
    assert client.sent[0][2].endswith("via me")
    assert db.cursor("src") == 6


def test_content_protection_respected():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "secret", noforwards=True))
    run(client, res, src, st, db)
    assert client.sent == []


def test_long_caption_split_and_big_file_skipped():
    client, res, src, st, db = make_env(
        [Msg(1, "old")], caption_limit=50, max_file_mb=1
    )
    run(client, res, src, st, db)
    client.messages += [
        Msg(2, "x " * 100, media=photo()),
        Msg(3, "huge", media=video(), size=5 * 1024 * 1024),
    ]
    run(client, res, src, st, db)
    assert [s[0] for s in client.sent] == ["file", "message"]
    assert client.sent[0][3]["caption"] == ""


def test_media_download_fallback_keeps_going():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "v", media=video()))
    client.fail_times = 1   # direct send fails once -> download + upload
    run(client, res, src, st, db)
    assert len(client.sent) == 1
    kw = client.sent[0][3]
    assert client.sent[0][2].endswith("file_2.bin") and kw["supports_streaming"]


def test_service_messages_skipped():
    client, res, src, st, db = make_env([Msg(1, "old")])
    run(client, res, src, st, db)
    client.messages.append(Msg(2, "", action=object()))
    run(client, res, src, st, db)
    assert client.sent == [] and db.cursor("src") == 2
