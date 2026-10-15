"""Unit tests for `screener.analysis.news.fetch_news`. No network access --
`yfinance.Ticker` is monkeypatched with fake objects returning canned data."""
from datetime import datetime, timedelta, timezone

import screener.analysis.news as news_module


class _FakeTicker:
    def __init__(self, news):
        self._news = news

    @property
    def news(self):
        if isinstance(self._news, Exception):
            raise self._news
        return self._news


def _patch_ticker(monkeypatch, news):
    monkeypatch.setattr(news_module.yf, "Ticker", lambda symbol: _FakeTicker(news))


# --- flat shape ---

async def test_fetch_news_normalizes_flat_shape(monkeypatch):
    now = datetime.now(timezone.utc)
    recent_epoch = (now - timedelta(days=1)).timestamp()
    _patch_ticker(
        monkeypatch,
        [
            {
                "title": "Flat News Title",
                "publisher": "Flat Publisher",
                "link": "https://example.com/flat",
                "providerPublishTime": recent_epoch,
                "summary": "Flat summary.",
            }
        ],
    )

    items = await news_module.fetch_news("AAPL")

    assert len(items) == 1
    item = items[0]
    assert item.title == "Flat News Title"
    assert item.publisher == "Flat Publisher"
    assert item.url == "https://example.com/flat"
    assert item.summary == "Flat summary."
    assert item.published_at is not None
    assert item.published_at.tzinfo is not None


# --- nested shape ---

async def test_fetch_news_normalizes_nested_shape(monkeypatch):
    now = datetime.now(timezone.utc)
    recent_iso = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _patch_ticker(
        monkeypatch,
        [
            {
                "content": {
                    "title": "Nested News Title",
                    "provider": {"displayName": "Nested Publisher"},
                    "canonicalUrl": {"url": "https://example.com/nested"},
                    "pubDate": recent_iso,
                    "summary": "Nested summary.",
                }
            }
        ],
    )

    items = await news_module.fetch_news("AAPL")

    assert len(items) == 1
    item = items[0]
    assert item.title == "Nested News Title"
    assert item.publisher == "Nested Publisher"
    assert item.url == "https://example.com/nested"
    assert item.summary == "Nested summary."
    assert item.published_at is not None


# --- days filter ---

async def test_fetch_news_drops_old_items_keeps_recent(monkeypatch):
    now = datetime.now(timezone.utc)
    old_epoch = (now - timedelta(days=30)).timestamp()
    recent_epoch = (now - timedelta(days=1)).timestamp()
    _patch_ticker(
        monkeypatch,
        [
            {"title": "Old Item", "providerPublishTime": old_epoch},
            {"title": "Recent Item", "providerPublishTime": recent_epoch},
        ],
    )

    items = await news_module.fetch_news("AAPL", days=7)

    titles = [i.title for i in items]
    assert "Recent Item" in titles
    assert "Old Item" not in titles


async def test_fetch_news_sorts_newest_first_and_caps_limit(monkeypatch):
    now = datetime.now(timezone.utc)
    raw = [
        {"title": f"Item {i}", "providerPublishTime": (now - timedelta(hours=i)).timestamp()}
        for i in range(5)
    ]
    # Shuffle so sort order actually matters.
    _patch_ticker(monkeypatch, [raw[3], raw[0], raw[4], raw[1], raw[2]])

    items = await news_module.fetch_news("AAPL", days=7, limit=3)

    assert len(items) == 3
    assert [i.title for i in items] == ["Item 0", "Item 1", "Item 2"]


# --- garbage / malformed payloads ---

async def test_fetch_news_returns_empty_on_news_property_raising(monkeypatch):
    _patch_ticker(monkeypatch, RuntimeError("network exploded"))

    items = await news_module.fetch_news("AAPL")

    assert items == []


async def test_fetch_news_returns_empty_on_none_payload(monkeypatch):
    _patch_ticker(monkeypatch, None)

    items = await news_module.fetch_news("AAPL")

    assert items == []


async def test_fetch_news_skips_junk_items_without_crashing(monkeypatch):
    now = datetime.now(timezone.utc)
    recent_epoch = (now - timedelta(days=1)).timestamp()
    _patch_ticker(
        monkeypatch,
        [
            "not a dict",
            {"publisher": "No Title Here"},  # missing title -> dropped
            {"title": "Valid Item", "providerPublishTime": recent_epoch},
        ],
    )

    items = await news_module.fetch_news("AAPL")

    assert len(items) == 1
    assert items[0].title == "Valid Item"


async def test_fetch_news_drops_items_with_unparseable_timestamp(monkeypatch):
    _patch_ticker(
        monkeypatch,
        [
            {"title": "No Timestamp"},
            {"title": "Bad Timestamp", "content": {"pubDate": "not-a-date"}},
        ],
    )

    items = await news_module.fetch_news("AAPL")

    assert items == []
