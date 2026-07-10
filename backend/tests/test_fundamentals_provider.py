"""Tests for FundamentalsProvider."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from screener.data.fundamentals_cache import FundamentalsCache
from screener.data.fundamentals_provider import FundamentalsProvider


def _cols(years: list[int]) -> list[pd.Timestamp]:
    """Most-recent-first period columns, matching yfinance's usual ordering."""
    return [pd.Timestamp(f"{y}-12-31") for y in sorted(years, reverse=True)]


@pytest.fixture
def cache(tmp_path):
    c = FundamentalsCache(db_path=tmp_path / "fundamentals.db")
    yield c
    c.close()


@pytest.fixture
def provider(cache):
    return FundamentalsProvider(cache=cache)


def _full_fixture():
    """5 years of realistic-ish financials/balance_sheet/cashflow, columns
    most-recent-first (2024 .. 2020), mimicking yfinance's DataFrame shape:
    index = row labels, columns = period Timestamps."""
    cols = _cols([2020, 2021, 2022, 2023, 2024])

    financials = pd.DataFrame(
        {
            cols[0]: {  # 2024
                "Total Revenue": 1000.0,
                "Gross Profit": 400.0,
                "Net Income": 150.0,
                "EBIT": 200.0,
                "Interest Expense": -20.0,
            },
            cols[1]: {  # 2023
                "Total Revenue": 900.0,
                "Gross Profit": 360.0,
                "Net Income": 120.0,
                "EBIT": 180.0,
                "Interest Expense": -18.0,
            },
            cols[2]: {  # 2022
                "Total Revenue": 800.0,
                "Gross Profit": 320.0,
                "Net Income": 100.0,
                "EBIT": 150.0,
                "Interest Expense": -15.0,
            },
            cols[3]: {  # 2021
                "Total Revenue": 700.0,
                "Gross Profit": 280.0,
                "Net Income": 80.0,
                "EBIT": 120.0,
                "Interest Expense": -12.0,
            },
            cols[4]: {  # 2020
                "Total Revenue": 600.0,
                "Gross Profit": 240.0,
                "Net Income": 60.0,
                "EBIT": 90.0,
                "Interest Expense": -10.0,
            },
        }
    )

    cashflow = pd.DataFrame(
        {
            cols[0]: {"Operating Cash Flow": 250.0, "Capital Expenditure": -50.0},
            cols[1]: {"Operating Cash Flow": 220.0, "Capital Expenditure": -45.0},
            cols[2]: {"Operating Cash Flow": 190.0, "Capital Expenditure": -40.0},
            cols[3]: {"Operating Cash Flow": 160.0, "Capital Expenditure": -35.0},
            cols[4]: {"Operating Cash Flow": 130.0, "Capital Expenditure": -30.0},
        }
    )

    balance_sheet = pd.DataFrame(
        {
            cols[0]: {"Share Issued": 1100.0},
            cols[1]: {"Share Issued": 1080.0},
            cols[2]: {"Share Issued": 1050.0},
            cols[3]: {"Share Issued": 1020.0},
            cols[4]: {"Share Issued": 1000.0},
        }
    )

    return financials, cashflow, balance_sheet


def _mock_ticker(financials, cashflow, balance_sheet, info=None):
    mock = MagicMock()
    mock.financials = financials
    mock.cashflow = cashflow
    mock.balance_sheet = balance_sheet
    mock.info = info if info is not None else {}
    return mock


async def test_full_fixture_computes_all_metrics(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(financials, cashflow, balance_sheet),
    ):
        snap = await provider.get_fundamentals("AAPL")

    assert snap.ticker == "AAPL"
    assert snap.years_available == 5

    # fcf: sum(OCF + CapEx) across all 5 years
    expected_fcf = (250 - 50) + (220 - 45) + (190 - 40) + (160 - 35) + (130 - 30)
    assert snap.fcf_5y_cumulative == pytest.approx(expected_fcf)

    # interest_coverage: latest year EBIT / |interest expense| = 200 / 20
    assert snap.interest_coverage == pytest.approx(10.0)

    # gross_margin: average of GP/Rev across 5 years
    expected_gm = sum(
        gp / rev
        for gp, rev in [(400, 1000), (360, 900), (320, 800), (280, 700), (240, 600)]
    ) / 5
    assert snap.gross_margin == pytest.approx(expected_gm)

    # net_margin: average of NI/Rev across 5 years
    expected_nm = sum(
        ni / rev
        for ni, rev in [(150, 1000), (120, 900), (100, 800), (80, 700), (60, 600)]
    ) / 5
    assert snap.net_margin == pytest.approx(expected_nm)

    # ocf_ni_ratio: average of OCF/NI across 5 years (all NI > 0 here)
    expected_ocf_ni = sum(
        ocf / ni
        for ocf, ni in [(250, 150), (220, 120), (190, 100), (160, 80), (130, 60)]
    ) / 5
    assert snap.ocf_ni_ratio == pytest.approx(expected_ocf_ni)

    # share_dilution_5y: (latest - oldest) / oldest = (1100 - 1000) / 1000
    assert snap.share_dilution_5y == pytest.approx(0.1)

    # result is cached: a second call must not touch yf.Ticker again
    with patch("screener.data.fundamentals_provider.yf.Ticker") as mock_ticker_cls:
        snap2 = await provider.get_fundamentals("AAPL")
    mock_ticker_cls.assert_not_called()
    assert snap2.fcf_5y_cumulative == pytest.approx(expected_fcf)


async def test_zero_interest_expense_returns_none_no_crash(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    cols = list(financials.columns)
    financials.loc["Interest Expense", cols[0]] = 0.0

    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(financials, cashflow, balance_sheet),
    ):
        snap = await provider.get_fundamentals("AAPL")

    assert snap.interest_coverage is None
    # other metrics still computed fine
    assert snap.gross_margin is not None


async def test_missing_interest_expense_row_returns_none(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    financials = financials.drop(index="Interest Expense")

    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(financials, cashflow, balance_sheet),
    ):
        snap = await provider.get_fundamentals("AAPL")

    assert snap.interest_coverage is None


async def test_negative_ni_year_excluded_from_ocf_ni_ratio(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    cols = list(financials.columns)
    # Make the oldest year's Net Income negative
    financials.loc["Net Income", cols[-1]] = -20.0

    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(financials, cashflow, balance_sheet),
    ):
        snap = await provider.get_fundamentals("AAPL")

    # Manual average excluding the oldest (now negative-NI) year
    expected_ocf_ni = sum(
        ocf / ni for ocf, ni in [(250, 150), (220, 120), (190, 100), (160, 80)]
    ) / 4
    assert snap.ocf_ni_ratio == pytest.approx(expected_ocf_ni)


async def test_missing_shares_row_falls_back_to_info(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    balance_sheet = balance_sheet.drop(index="Share Issued")

    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(
            financials, cashflow, balance_sheet, info={"sharesOutstanding": 1_200_000}
        ),
    ):
        snap = await provider.get_fundamentals("AAPL")

    assert snap.share_dilution_5y is None
    # other metrics unaffected
    assert snap.gross_margin is not None


async def test_completely_empty_data_returns_zero_years_all_none(provider):
    empty = pd.DataFrame()
    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(empty, empty, empty),
    ):
        snap = await provider.get_fundamentals("ZZZZ")

    assert snap.years_available == 0
    assert snap.fcf_5y_cumulative is None
    assert snap.interest_coverage is None
    assert snap.gross_margin is None
    assert snap.ocf_ni_ratio is None
    assert snap.net_margin is None
    assert snap.share_dilution_5y is None


async def test_single_year_of_data_returns_all_none(provider):
    financials, cashflow, balance_sheet = _full_fixture()
    cols = list(financials.columns)
    # Keep only the most recent column in each statement
    financials = financials[[cols[0]]]
    cashflow = cashflow[[cols[0]]]
    balance_sheet = balance_sheet[[cols[0]]]

    with patch(
        "screener.data.fundamentals_provider.yf.Ticker",
        return_value=_mock_ticker(financials, cashflow, balance_sheet),
    ):
        snap = await provider.get_fundamentals("AAPL")

    assert snap.years_available == 1
    assert snap.fcf_5y_cumulative is None
    assert snap.interest_coverage is None
    assert snap.gross_margin is None
    assert snap.ocf_ni_ratio is None
    assert snap.net_margin is None
    assert snap.share_dilution_5y is None


@pytest.mark.integration
async def test_real_aapl_fundamentals_fetch(tmp_path):
    """Live network test — skipped in CI with -m 'not integration'."""
    cache = FundamentalsCache(db_path=tmp_path / "fundamentals.db")
    provider = FundamentalsProvider(cache=cache)
    snap = await provider.get_fundamentals("AAPL")
    assert snap.ticker == "AAPL"
    assert snap.as_of.tzinfo == timezone.utc
    cache.close()
