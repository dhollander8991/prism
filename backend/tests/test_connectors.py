from __future__ import annotations

from datetime import datetime, timezone

import httpx
import respx

from connectors.app_store import AppStoreConnector
from connectors.google_play import GooglePlayConnector
from connectors.hackernews import HackerNewsConnector
from connectors.reddit import RedditConnector


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None


class FakeDB:
    """Stand-in AsyncSession. `existing` = source_ids already 'in the DB'.
    _store filters by membership, so returning the full existing set is equivalent to IN()."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.added: list = []
        self.commits = 0

    async def execute(self, _stmt):
        return FakeResult([(s,) for s in self.existing])

    def add_all(self, objs):
        self.added.extend(objs)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _rss_entry(review_id: str, title: str, body: str, stars: int) -> dict:
    return {
        "id": {"label": review_id},
        "title": {"label": title},
        "content": {"label": body},
        "im:rating": {"label": str(stars)},
        "updated": {"label": "2026-01-02T03:04:05-07:00"},
    }


# --- App Store ---------------------------------------------------------------

@respx.mock
async def test_app_store_maps_and_dedups():
    app_id = "389801252"
    page1 = {
        "feed": {
            "entry": [
                {"im:name": {"label": "Instagram"}},  # leading app-metadata element
                _rss_entry("r1", "Great", "love it", 5),
                _rss_entry("r2", "Bad", "crashes", 1),
                _rss_entry("r2", "Bad", "crashes", 1),  # in-batch duplicate
            ]
        }
    }
    empty = {"feed": {"updated": {"label": "x"}}}  # no entry key -> stops paging

    def responder(request):
        return httpx.Response(200, json=page1 if "page=1" in str(request.url) else empty)

    respx.get(url__regex=r"itunes\.apple\.com").mock(side_effect=responder)

    conn = AppStoreConnector(app_id=app_id, country="us")

    db = FakeDB()
    stored = await conn.fetch_and_store(db, count=100)
    assert stored == 2  # metadata dropped, duplicate collapsed
    assert {i.source for i in db.added} == {"app_store"}
    assert any("love it" in i.text for i in db.added)

    # Re-syncing with those source_ids already present stores nothing.
    already = {i.source_id for i in db.added}
    db2 = FakeDB(existing=already)
    assert await conn.fetch_and_store(db2, count=100) == 0


@respx.mock
async def test_app_store_retries_empty_entry():
    """First fetch returns a feed with no `entry`; retry yields reviews."""
    calls = {"n": 0}

    def responder(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"feed": {"updated": {"label": "x"}}})
        return httpx.Response(200, json={"feed": {"entry": [_rss_entry("r9", "T", "body", 4)]}})

    respx.get(url__regex=r"itunes\.apple\.com").mock(side_effect=responder)

    db = FakeDB()
    stored = await AppStoreConnector("1", "us").fetch_and_store(db, count=50)
    assert stored == 1
    assert calls["n"] >= 2  # proves the retry fired


# --- Google Play -------------------------------------------------------------

async def test_google_play_maps(monkeypatch):
    fixture = [
        {"reviewId": "gp1", "content": "works well", "score": 5,
         "at": datetime(2026, 1, 1, tzinfo=timezone.utc), "reviewCreatedVersion": "1.2"},
        {"reviewId": "gp2", "content": "", "score": 3, "at": datetime(2026, 1, 1)},  # empty -> dropped
    ]
    monkeypatch.setattr(
        "connectors.google_play.reviews", lambda *a, **k: (fixture, None)
    )

    db = FakeDB()
    stored = await GooglePlayConnector("com.x.y").fetch_and_store(db, count=100)
    assert stored == 1
    row = db.added[0]
    assert row.source == "google_play"
    assert row.text == "works well"
    assert row.item_metadata["stars"] == 5


# --- Hacker News -------------------------------------------------------------

@respx.mock
async def test_hackernews_maps_and_dedups():
    payload = {
        "hits": [
            {"objectID": "1", "comment_text": "notion is slow", "author": "a",
             "created_at_i": 1700000000, "points": 4, "story_title": "Notion"},
            {"objectID": "2", "title": "Show HN: thing", "author": "b",
             "created_at_i": 1700000100},
            {"objectID": "3", "comment_text": "", "author": "c"},  # empty -> dropped
        ]
    }
    respx.get(url__regex=r"hn\.algolia\.com").mock(
        return_value=httpx.Response(200, json=payload)
    )

    conn = HackerNewsConnector("notion")
    db = FakeDB()
    stored = await conn.fetch_and_store(db, count=100)
    assert stored == 2
    assert any("notion is slow" in i.text for i in db.added)

    already = {i.source_id for i in db.added}
    db2 = FakeDB(existing=already)
    assert await conn.fetch_and_store(db2, count=100) == 0


# --- Reddit ------------------------------------------------------------------

@respx.mock
async def test_reddit_maps_posts_and_comments():
    listing = {
        "data": {
            "children": [
                {"kind": "t3", "data": {
                    "id": "p1", "title": "Bug report", "selftext": "app crashes",
                    "created_utc": 1700000000, "author": "u1", "score": 10,
                    "permalink": "/r/x/p1"}},
            ]
        }
    }
    comments = [
        {},  # post listing (ignored)
        {"data": {"children": [
            {"kind": "t1", "data": {"id": "c1", "body": "same here",
                                    "created_utc": 1700000050, "author": "u2", "score": 3}},
            {"kind": "more", "data": {}},  # ignored
        ]}},
    ]

    def responder(request):
        if "/comments/" in str(request.url):
            return httpx.Response(200, json=comments)
        return httpx.Response(200, json=listing)

    respx.get(url__regex=r"reddit\.com").mock(side_effect=responder)

    db = FakeDB()
    stored = await RedditConnector("x").fetch_and_store(db, count=100)
    assert stored == 2  # 1 post + 1 comment
    texts = " ".join(i.text for i in db.added)
    assert "app crashes" in texts and "same here" in texts
    kinds = {i.item_metadata["kind"] for i in db.added}
    assert kinds == {"post", "comment"}
