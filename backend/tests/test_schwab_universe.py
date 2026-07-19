"""Tests for SchwabLosersUniverse (Milestone 3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from screener.data.schwab.universe import SchwabLosersUniverse


def _movers_payload(symbols: list[str]) -> dict:
    return {"screeners": [{"symbol": s} for s in symbols]}


def _quote_entry(
    change_pct: float,
    quote_type: str = "EQUITY",
    industry: str | None = None,
    sector: str | None = None,
) -> dict:
    """A single flattened Schwab quotes-response entry. Deliberately has NO
    `pbRatio`/`marketCap` under `fundamental` — CONFIRMED LIVE this session
    that the real quotes endpoint never carries those two fields at all;
    they are sourced separately via `_instruments_payload` below."""
    return {
        "assetMainType": quote_type,
        "quote": {"netPercentChange": change_pct},
        "fundamental": {},
        "reference": {"industry": industry, "sector": sector},
    }


def _instruments_payload(entries: dict[str, tuple[float | None, float | None]]) -> dict:
    """Build a `/marketdata/v1/instruments` (FUNDAMENTAL projection) style
    response: a top-level `instruments` LIST (not dict-keyed-by-symbol, per
    the confirmed-live envelope shape), each entry matched by its own
    `symbol` field. `entries` maps symbol -> (price_to_book, market_cap)."""
    return {
        "instruments": [
            {
                "symbol": symbol,
                "assetType": "EQUITY",
                "fundamental": {"pbRatio": pb, "marketCap": mc},
            }
            for symbol, (pb, mc) in entries.items()
        ]
    }


def _client(side_effect: list) -> MagicMock:
    client = MagicMock()
    client.call = AsyncMock(side_effect=side_effect)
    return client


# --- cap-floor / quote-type filtering (applied on the flattened quotes
# response merged with the instruments response, since Schwab's movers
# payload itself has no market-cap/quote-type field, and the quotes
# endpoint itself has no market-cap field — see universe.py's
# `_filter_and_rank` docstring) ---


async def test_drops_penny_stocks_below_cap_floor():
    movers = _movers_payload(["BIGCO", "PENNY"])
    quotes = {
        "BIGCO": _quote_entry(-5.0),
        "PENNY": _quote_entry(-20.0),
    }
    instruments = _instruments_payload(
        {
            "BIGCO": (5.0, 50_000_000_000),
            "PENNY": (5.0, 100_000_000),  # below $10B floor
        }
    )
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "PENNY" not in symbols


async def test_missing_instruments_entry_leaves_market_cap_none_and_filtered_out():
    """A symbol quoted successfully but absent from the instruments response
    (CONFIRMED LIVE this session: Schwab silently omits unrecognized/failed
    symbols from `instruments` rather than erroring) must leave marketCap as
    None for that symbol -- which then fails the $10B floor -- rather than
    raising or fabricating a value."""
    movers = _movers_payload(["BIGCO", "NOFUND"])
    quotes = {
        "BIGCO": _quote_entry(-5.0),
        "NOFUND": _quote_entry(-6.0),
    }
    instruments = _instruments_payload({"BIGCO": (5.0, 50_000_000_000)})  # NOFUND absent
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()
    quotes_out = await u.get_quotes()

    assert "BIGCO" in symbols
    assert "NOFUND" not in symbols
    assert quotes_out["BIGCO"]["market_cap"] == 50_000_000_000


async def test_drops_non_equity_quote_types():
    movers = _movers_payload(["BIGCO", "SOMEETF"])
    quotes = {
        "BIGCO": _quote_entry(-5.0),
        "SOMEETF": _quote_entry(-10.0, quote_type="ETF"),
    }
    instruments = _instruments_payload(
        {
            "BIGCO": (5.0, 50_000_000_000),
            "SOMEETF": (5.0, 50_000_000_000),
        }
    )
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "SOMEETF" not in symbols


# --- top-20 cap ---


async def test_top_20_cap_enforced():
    symbols_in = [f"SYM{i}" for i in range(25)]
    movers = _movers_payload(symbols_in)
    quotes = {s: _quote_entry(-(i + 1.0)) for i, s in enumerate(symbols_in)}
    instruments = _instruments_payload({s: (5.0, 50_000_000_000) for s in symbols_in})
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert len(symbols) == 20
    expected_top20 = {f"SYM{i}" for i in range(5, 25)}
    assert set(symbols) == expected_top20


async def test_fewer_than_20_qualifying_losers_returns_all():
    symbols_in = [f"SYM{i}" for i in range(5)]
    movers = _movers_payload(symbols_in)
    quotes = {s: _quote_entry(-(i + 1.0)) for i, s in enumerate(symbols_in)}
    instruments = _instruments_payload({s: (5.0, 50_000_000_000) for s in symbols_in})
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert len(symbols) == 5


# --- watchlist union ---


async def test_watchlist_union_adds_down_symbols_not_in_screen():
    movers = _movers_payload(["BIGCO"])
    mover_quotes = {"BIGCO": _quote_entry(-5.0)}
    mover_instruments = _instruments_payload({"BIGCO": (5.0, 50_000_000_000)})
    # _watchlist_down_today iterates sorted(watchlist) -> AAPL, then NVDA
    aapl_quote = {"AAPL": _quote_entry(1.2)}  # up today
    aapl_instruments = _instruments_payload({"AAPL": (30.0, 3e12)})
    nvda_quote = {"NVDA": _quote_entry(-3.5)}  # down today
    nvda_instruments = _instruments_payload({"NVDA": (40.0, 1e12)})

    client = _client(
        [
            movers,
            mover_quotes,
            mover_instruments,
            aapl_quote,
            aapl_instruments,
            nvda_quote,
            nvda_instruments,
        ]
    )
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA", "AAPL"})

    symbols = await u.get_symbols()

    assert "BIGCO" in symbols
    assert "NVDA" in symbols  # down today -> unioned in
    assert "AAPL" not in symbols  # up today -> excluded


async def test_watchlist_symbol_already_in_losers_not_duplicated():
    movers = _movers_payload(["NVDA"])
    mover_quotes = {"NVDA": _quote_entry(-8.0)}
    mover_instruments = _instruments_payload({"NVDA": (5.0, 50_000_000_000)})
    nvda_watchlist_quote = {"NVDA": _quote_entry(-8.0)}
    nvda_watchlist_instruments = _instruments_payload({"NVDA": (5.0, 50_000_000_000)})

    client = _client(
        [movers, mover_quotes, mover_instruments, nvda_watchlist_quote, nvda_watchlist_instruments]
    )
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA"})

    symbols = await u.get_symbols()

    assert symbols.count("NVDA") == 1


# --- get_quotes() shape ---


async def test_get_quotes_shape():
    movers = _movers_payload(["BIGCO"])
    quotes = {"BIGCO": _quote_entry(-5.0)}
    instruments = _instruments_payload({"BIGCO": (3.2, 20_000_000_000)})
    client = _client([movers, quotes, instruments])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    await u.get_symbols()
    result = await u.get_quotes()

    assert "BIGCO" in result
    meta = result["BIGCO"]
    assert set(meta.keys()) == {"price_to_book", "change_pct", "market_cap", "industry", "sector"}
    assert meta["price_to_book"] == 3.2
    assert meta["change_pct"] == -5.0
    assert meta["market_cap"] == 20_000_000_000
    assert meta["industry"] is None
    assert meta["sector"] is None


async def test_get_quotes_empty_before_get_symbols_called():
    client = MagicMock()
    client.call = AsyncMock()
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u.get_quotes()

    assert result == {}


async def test_get_quotes_includes_watchlist_union_symbols():
    movers = _movers_payload(["BIGCO"])
    mover_quotes = {"BIGCO": _quote_entry(-5.0)}
    mover_instruments = _instruments_payload({"BIGCO": (5.0, 50_000_000_000)})
    nvda_quote = {"NVDA": _quote_entry(-2.0)}
    nvda_instruments = _instruments_payload({"NVDA": (40.0, 1e12)})

    client = _client([movers, mover_quotes, mover_instruments, nvda_quote, nvda_instruments])
    u = SchwabLosersUniverse(client=client, watchlist={"NVDA"})

    await u.get_symbols()
    result = await u.get_quotes()

    assert "NVDA" in result
    assert result["NVDA"]["change_pct"] == -2.0


# --- quote_for (single-symbol debug helper) ---


async def test_flatten_quote_entry_reads_net_percent_change_confirmed_field_name():
    """Confirmed against the real Schwab OpenAPI spec: QuoteEquity's field is
    `netPercentChange`, NOT `netPercentChangeInDouble` (a previously-fixed
    bug). This directly exercises `_flatten_quote_entry`'s read of the
    correct key."""
    entry = _quote_entry(-2.75)
    flattened = SchwabLosersUniverse._flatten_quote_entry("BIGCO", entry)

    assert flattened["netPercentChange"] == -2.75


def test_flatten_quote_entry_price_to_book_and_market_cap_always_none_from_quotes():
    """CONFIRMED LIVE this session: the quotes endpoint's `fundamental`
    section never carries `pbRatio`/`marketCap` -- so `_flatten_quote_entry`
    alone always yields None for both; the real values only show up after
    `_fetch_quotes_batch` merges in the separate instruments-endpoint call."""
    entry = _quote_entry(-2.75)
    flattened = SchwabLosersUniverse._flatten_quote_entry("BIGCO", entry)

    assert flattened["priceToBook"] is None
    assert flattened["marketCap"] is None


async def test_quote_for_returns_meta_for_arbitrary_symbol():
    client = _client(
        [
            {"NVDA": _quote_entry(-4.0)},
            _instruments_payload({"NVDA": (12.0, 5e11)}),
        ]
    )
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("NVDA")

    assert meta == {
        "price_to_book": 12.0,
        "change_pct": -4.0,
        "market_cap": 5e11,
        "industry": None,
        "sector": None,
    }


async def test_quote_for_includes_industry_when_present():
    client = _client(
        [
            {"SKYT": _quote_entry(-4.0, industry="Semiconductors", sector="Technology")},
            _instruments_payload({"SKYT": (12.0, 5e11)}),
        ]
    )
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("SKYT")

    assert meta["industry"] == "Semiconductors"
    assert meta["sector"] == "Technology"


async def test_quote_for_returns_none_on_fetch_error():
    client = MagicMock()
    client.call = AsyncMock(side_effect=RuntimeError("boom"))
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("BADSYM")

    assert meta is None


async def test_quote_for_price_to_book_none_when_instruments_call_fails():
    """The quotes call succeeds but the instruments call fails entirely --
    per-field-independent-failure: priceToBook/marketCap degrade to None,
    the rest of the quote (change_pct) is still returned."""
    client = MagicMock()
    client.call = AsyncMock(
        side_effect=[{"NVDA": _quote_entry(-4.0)}, RuntimeError("instruments down")]
    )
    u = SchwabLosersUniverse(client=client, watchlist=set())

    meta = await u.quote_for("NVDA")

    assert meta["change_pct"] == -4.0
    assert meta["price_to_book"] is None
    assert meta["market_cap"] is None


# --- resilience ---


async def test_movers_error_returns_empty_symbols_list():
    client = MagicMock()
    client.call = AsyncMock(side_effect=RuntimeError("network down"))
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []


async def test_movers_non_dict_result_treated_as_empty():
    client = _client([None])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []


async def test_quotes_batch_error_after_movers_success_yields_no_candidates():
    movers = _movers_payload(["BIGCO"])
    client = MagicMock()
    client.call = AsyncMock(side_effect=[movers, RuntimeError("quotes down")])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    symbols = await u.get_symbols()

    assert symbols == []


# --- typed schwab-py method wiring (confirmed enums from the installed
# schwab-py package; `client.call`'s real implementation awaits the passed
# `request_fn`, so these tests invoke it themselves via a fake `client.call`
# to verify the exact args/enums `_fetch_movers`/`_fetch_quotes_batch`/
# `_fetch_instruments_fundamentals` build, rather than the `_client()`
# helper above which never invokes `request_fn` at all) ---


async def test_fetch_movers_uses_typed_get_movers_with_confirmed_enums():
    from schwab.client.base import BaseClient

    captured: dict = {}

    class FakeRaw:
        Movers = BaseClient.Movers

        def get_movers(self, index, *, sort_order=None, frequency=None):
            captured["index"] = index
            captured["sort_order"] = sort_order
            captured["frequency"] = frequency
            return {"screeners": []}

    async def fake_call(request_fn, *, label="typed_call"):
        return request_fn()

    client = MagicMock()
    client.raw = FakeRaw()
    client.call = fake_call
    u = SchwabLosersUniverse(client=client, watchlist=set())

    await u._fetch_movers()

    assert captured["index"] == BaseClient.Movers.Index.SPX
    assert captured["sort_order"] == BaseClient.Movers.SortOrder.PERCENT_CHANGE_DOWN
    assert captured["frequency"] == BaseClient.Movers.Frequency.ZERO


async def test_fetch_quotes_batch_uses_typed_get_quotes_with_confirmed_fields():
    from schwab.client.base import BaseClient

    captured: dict = {}

    class FakeRaw:
        Quote = BaseClient.Quote
        Instrument = BaseClient.Instrument

        def get_quotes(self, symbols, *, fields=None):
            captured["symbols"] = symbols
            captured["fields"] = fields
            return {}

        def get_instruments(self, symbols, *, projection=None):
            captured["instruments_symbols"] = symbols
            captured["instruments_projection"] = projection
            return {"instruments": []}

    async def fake_call(request_fn, *, label="typed_call"):
        return request_fn()

    client = MagicMock()
    client.raw = FakeRaw()
    client.call = fake_call
    u = SchwabLosersUniverse(client=client, watchlist=set())

    await u._fetch_quotes_batch(["AAPL", "MSFT"])

    assert captured["symbols"] == ["AAPL", "MSFT"]
    assert captured["fields"] == [
        BaseClient.Quote.Fields.FUNDAMENTAL,
        BaseClient.Quote.Fields.QUOTE,
        BaseClient.Quote.Fields.REFERENCE,
    ]
    # get_quotes() returned {} (no symbols), so the instruments merge is
    # never called -- nothing to merge fundamentals into.
    assert "instruments_symbols" not in captured


async def test_fetch_quotes_batch_calls_get_instruments_for_quoted_symbols():
    """The instruments merge is called with exactly the symbols that came
    back from get_quotes (not necessarily the originally-requested batch),
    using the FUNDAMENTAL projection."""
    from schwab.client.base import BaseClient

    captured: dict = {}

    class FakeRaw:
        Quote = BaseClient.Quote
        Instrument = BaseClient.Instrument

        def get_quotes(self, symbols, *, fields=None):
            return {"AAPL": _quote_entry(-1.0), "MSFT": _quote_entry(-2.0)}

        def get_instruments(self, symbols, *, projection=None):
            captured["instruments_symbols"] = symbols
            captured["instruments_projection"] = projection
            return {"instruments": []}

    async def fake_call(request_fn, *, label="typed_call"):
        return request_fn()

    client = MagicMock()
    client.raw = FakeRaw()
    client.call = fake_call
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u._fetch_quotes_batch(["AAPL", "MSFT"])

    assert set(captured["instruments_symbols"]) == {"AAPL", "MSFT"}
    assert captured["instruments_projection"] == BaseClient.Instrument.Projection.FUNDAMENTAL
    # instruments returned no entries, so priceToBook/marketCap stay None.
    assert result["AAPL"]["priceToBook"] is None
    assert result["MSFT"]["marketCap"] is None


async def test_fetch_instruments_fundamentals_parses_instruments_list_envelope():
    """Directly exercises `_fetch_instruments_fundamentals`'s parsing of the
    real CONFIRMED-LIVE envelope shape: a top-level `instruments` LIST
    (matched by each entry's own `symbol` field), NOT a dict keyed by
    symbol like `get_quotes`."""
    payload = _instruments_payload({"AAPL": (34.26882, 4901758191440.0), "MSFT": (6.63661, 2925466155129.0)})
    client = _client([payload])
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u._fetch_instruments_fundamentals(["AAPL", "MSFT"])

    assert result["AAPL"] == {"priceToBook": 34.26882, "marketCap": 4901758191440.0}
    assert result["MSFT"] == {"priceToBook": 6.63661, "marketCap": 2925466155129.0}


async def test_fetch_instruments_fundamentals_error_returns_empty_dict():
    client = MagicMock()
    client.call = AsyncMock(side_effect=RuntimeError("instruments endpoint down"))
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u._fetch_instruments_fundamentals(["AAPL"])

    assert result == {}


async def test_fetch_instruments_fundamentals_empty_symbols_short_circuits():
    client = MagicMock()
    client.call = AsyncMock()
    u = SchwabLosersUniverse(client=client, watchlist=set())

    result = await u._fetch_instruments_fundamentals([])

    assert result == {}
    client.call.assert_not_called()
