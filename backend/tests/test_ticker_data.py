"""Unit tests for the shared yfinance info-dict parsing helpers in
`screener.api.ticker_data`. No network access -- built from plain dicts."""
from screener.api.ticker_data import looks_like_real_symbol, num, pick


# --- pick ---

def test_pick_returns_first_non_none_value_not_first_truthy():
    # 0.0 / False / "" are legitimate values and must not be skipped.
    assert pick({"a": 0.0, "b": 5.0}, "a", "b") == 0.0
    assert pick({"a": False, "b": True}, "a", "b") is False
    assert pick({"a": "", "b": "x"}, "a", "b") == ""


def test_pick_skips_none_and_missing_keys():
    assert pick({"a": None, "b": None, "c": 3.0}, "a", "b", "c") == 3.0
    assert pick({}, "a", "b") is None


# --- num ---

def test_num_returns_none_for_none():
    assert num(None) is None


def test_num_returns_none_for_non_numeric():
    assert num("n/a") is None
    assert num("not a number") is None


def test_num_returns_none_for_nan():
    assert num(float("nan")) is None


def test_num_returns_none_for_inf():
    assert num(float("inf")) is None
    assert num(float("-inf")) is None


def test_num_coerces_valid_numbers():
    assert num(3.5) == 3.5
    assert num(0) == 0.0
    assert num("2.5") == 2.5


# --- looks_like_real_symbol ---

def test_looks_like_real_symbol_rejects_garbage_info():
    assert looks_like_real_symbol({"trailingPegRatio": None}) is False
    assert looks_like_real_symbol({}) is False


def test_looks_like_real_symbol_accepts_real_symbol_info():
    assert looks_like_real_symbol({"longName": "Apple Inc."}) is True
    assert looks_like_real_symbol({"regularMarketPrice": 100.0}) is True
    assert looks_like_real_symbol({"currentPrice": 50.0}) is True
    assert looks_like_real_symbol({"shortName": "AAPL"}) is True
