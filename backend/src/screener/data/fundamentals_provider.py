"""yfinance fundamentals provider with SQLite cache integration.

Computes 6 balance-sheet-quality metrics used to hard-exclude weak-balance-
sheet tickers before they reach the technical rule engine. Deliberately does
NOT include a 10yr-ROE metric (yfinance's free tier only exposes ~5 annual
periods) and does not implement any exemption/leniency carve-outs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from screener.data.fundamentals_cache import FundamentalsCache
from screener.models import FundamentalsSnapshot
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_FETCH_DELAY = 0.2  # seconds between live fetches

# Row-label aliases, in preference order (yfinance has renamed some rows
# across versions).
_OCF_ROWS = ["Operating Cash Flow", "Total Cash From Operating Activities"]
_CAPEX_ROWS = ["Capital Expenditure"]
_EBIT_ROWS = ["EBIT", "Operating Income"]
_INTEREST_EXPENSE_ROWS = ["Interest Expense"]
_GROSS_PROFIT_ROWS = ["Gross Profit"]
_REVENUE_ROWS = ["Total Revenue"]
_NET_INCOME_ROWS = ["Net Income"]
_SHARES_ROWS = ["Share Issued", "Ordinary Shares Number"]


def _get_row(df: pd.DataFrame, names: list[str]) -> pd.Series:
    """Return the first matching row (by label) from df's index, trying each
    name in order. Raises KeyError if none of the names are present."""
    for name in names:
        if name in df.index:
            return df.loc[name]
    raise KeyError(f"none of {names!r} found in index")


def _compute_fcf_5y_cumulative(cashflow: pd.DataFrame) -> float | None:
    """Sum of (Operating Cash Flow + Capital Expenditure) across all
    available annual periods. CapEx is stored as a negative number in
    yfinance (a cash outflow), so adding it to OCF already nets out the
    amount spent — this is summed (not averaged) per spec."""
    ocf_row = _get_row(cashflow, _OCF_ROWS)
    capex_row = _get_row(cashflow, _CAPEX_ROWS)
    total = 0.0
    for col in cashflow.columns:
        ocf = ocf_row.get(col)
        capex = capex_row.get(col)
        if ocf is None or capex is None or pd.isna(ocf) or pd.isna(capex):
            continue
        total += float(ocf) + float(capex)
    return total


def _compute_interest_coverage(financials: pd.DataFrame) -> float | None:
    """Latest-year EBIT / |Interest Expense|. Missing, zero, or NaN interest
    expense means "no debt" — an expected, non-error case that returns None
    without raising (callers must not log a warning for this branch)."""
    latest_col = sorted(financials.columns, reverse=True)[0]

    try:
        interest_row = _get_row(financials, _INTEREST_EXPENSE_ROWS)
        interest_expense = interest_row.get(latest_col)
    except KeyError:
        interest_expense = None

    if interest_expense is None or pd.isna(interest_expense) or float(interest_expense) == 0.0:
        return None  # no debt / no interest expense — expected, not a failure

    ebit_row = _get_row(financials, _EBIT_ROWS)  # KeyError propagates -> logged by caller
    ebit = ebit_row.get(latest_col)
    if ebit is None or pd.isna(ebit):
        raise ValueError("EBIT unavailable for interest_coverage")

    return float(ebit) / abs(float(interest_expense))


def _compute_gross_margin(financials: pd.DataFrame) -> float | None:
    """Average of (Gross Profit / Total Revenue) across all available years."""
    gp_row = _get_row(financials, _GROSS_PROFIT_ROWS)
    rev_row = _get_row(financials, _REVENUE_ROWS)
    ratios = []
    for col in financials.columns:
        gp = gp_row.get(col)
        rev = rev_row.get(col)
        if gp is None or rev is None or pd.isna(gp) or pd.isna(rev) or float(rev) == 0.0:
            continue
        ratios.append(float(gp) / float(rev))
    return sum(ratios) / len(ratios) if ratios else None


def _compute_ocf_ni_ratio(cashflow: pd.DataFrame, financials: pd.DataFrame) -> float | None:
    """Average of (Operating Cash Flow / Net Income) across years, dropping
    any year where Net Income <= 0 from both the sum and the count."""
    ocf_row = _get_row(cashflow, _OCF_ROWS)
    ni_row = _get_row(financials, _NET_INCOME_ROWS)
    ratios = []
    for col in cashflow.columns:
        ocf = ocf_row.get(col)
        ni = ni_row.get(col) if col in ni_row.index else None
        if ocf is None or ni is None or pd.isna(ocf) or pd.isna(ni) or float(ni) <= 0.0:
            continue
        ratios.append(float(ocf) / float(ni))
    return sum(ratios) / len(ratios) if ratios else None


def _compute_net_margin(financials: pd.DataFrame) -> float | None:
    """Average of (Net Income / Total Revenue) across all available years."""
    ni_row = _get_row(financials, _NET_INCOME_ROWS)
    rev_row = _get_row(financials, _REVENUE_ROWS)
    ratios = []
    for col in financials.columns:
        ni = ni_row.get(col)
        rev = rev_row.get(col)
        if ni is None or rev is None or pd.isna(ni) or pd.isna(rev) or float(rev) == 0.0:
            continue
        ratios.append(float(ni) / float(rev))
    return sum(ratios) / len(ratios) if ratios else None


def _compute_share_dilution_5y(
    balance_sheet: pd.DataFrame, info: dict, symbol: str
) -> float | None:
    """(latest shares outstanding - oldest shares outstanding) / oldest.

    If the balance sheet has no shares-outstanding row at all, falls back to
    Ticker.info['sharesOutstanding'] for a latest-year-only value — but since
    that gives no historical baseline, the ratio is still None. This is a
    documented fallback case, so it logs a warning (unlike the "no debt"
    branch of interest_coverage, which is a normal/expected case)."""
    try:
        row = _get_row(balance_sheet, _SHARES_ROWS)
    except KeyError:
        _ = (info or {}).get("sharesOutstanding")  # latest-year-only; no baseline to diff against
        logger.warning(
            "fundamentals_metric_unavailable", symbol=symbol, metric="share_dilution_5y"
        )
        return None

    cols_sorted = sorted(row.index, reverse=True)
    if len(cols_sorted) < 2:
        logger.warning(
            "fundamentals_metric_unavailable", symbol=symbol, metric="share_dilution_5y"
        )
        return None

    latest = row.get(cols_sorted[0])
    oldest = row.get(cols_sorted[-1])
    if (
        latest is None
        or oldest is None
        or pd.isna(latest)
        or pd.isna(oldest)
        or float(oldest) == 0.0
    ):
        logger.warning(
            "fundamentals_metric_unavailable", symbol=symbol, metric="share_dilution_5y"
        )
        return None

    return (float(latest) - float(oldest)) / float(oldest)


class FundamentalsProvider:
    def __init__(self, cache: FundamentalsCache) -> None:
        self._cache = cache

    async def get_fundamentals(self, symbol: str) -> FundamentalsSnapshot | None:
        """Return a FundamentalsSnapshot from cache, computing (and caching)
        one on a miss. Always returns a real snapshot — even insufficient-
        data cases yield years_available=0/1 with all metrics None — never
        actually returns None itself."""
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached

        await asyncio.sleep(_FETCH_DELAY)
        logger.info("fetching_fundamentals", symbol=symbol)
        snapshot = await asyncio.to_thread(self._compute, symbol)
        self._cache.put(symbol, snapshot)
        return snapshot

    def _safe_metric(self, symbol: str, metric_name: str, func, *args) -> float | None:
        """Run a metric computation, converting any exception into a logged
        warning + None so one bad metric never aborts the others."""
        try:
            return func(*args)
        except Exception:
            logger.warning("fundamentals_metric_unavailable", symbol=symbol, metric=metric_name)
            return None

    def _compute(self, symbol: str) -> FundamentalsSnapshot:
        """Blocking fetch + metric computation — call via asyncio.to_thread only."""
        now = datetime.now(timezone.utc)
        ticker = yf.Ticker(symbol)
        financials = ticker.financials
        balance_sheet = ticker.balance_sheet
        cashflow = ticker.cashflow
        try:
            info = ticker.info
        except Exception:
            info = {}

        if financials.empty and balance_sheet.empty and cashflow.empty:
            logger.info("fundamentals_no_data", symbol=symbol)
            return FundamentalsSnapshot(
                ticker=symbol,
                as_of=now,
                years_available=0,
                fcf_5y_cumulative=None,
                interest_coverage=None,
                gross_margin=None,
                ocf_ni_ratio=None,
                net_margin=None,
                share_dilution_5y=None,
            )

        fin_n = 0 if financials.empty else financials.shape[1]
        cf_n = 0 if cashflow.empty else cashflow.shape[1]
        bs_n = 0 if balance_sheet.empty else balance_sheet.shape[1]
        # years_available = number of usable annual periods. Most metrics
        # need both financials and cashflow, so we take the minimum of the
        # two when both have data; if one is entirely empty, fall back to
        # whichever statement actually has periods (financials/cashflow/
        # balance_sheet) so we don't zero out years_available unnecessarily.
        core_counts = [n for n in (fin_n, cf_n) if n > 0]
        years_available = min(core_counts) if core_counts else max(fin_n, cf_n, bs_n)

        if years_available < 2:
            logger.info(
                "fundamentals_insufficient_years", symbol=symbol, years_available=years_available
            )
            return FundamentalsSnapshot(
                ticker=symbol,
                as_of=now,
                years_available=years_available,
                fcf_5y_cumulative=None,
                interest_coverage=None,
                gross_margin=None,
                ocf_ni_ratio=None,
                net_margin=None,
                share_dilution_5y=None,
            )

        fcf_5y_cumulative = self._safe_metric(
            symbol, "fcf_5y_cumulative", _compute_fcf_5y_cumulative, cashflow
        )
        interest_coverage = self._safe_metric(
            symbol, "interest_coverage", _compute_interest_coverage, financials
        )
        gross_margin = self._safe_metric(
            symbol, "gross_margin", _compute_gross_margin, financials
        )
        ocf_ni_ratio = self._safe_metric(
            symbol, "ocf_ni_ratio", _compute_ocf_ni_ratio, cashflow, financials
        )
        net_margin = self._safe_metric(symbol, "net_margin", _compute_net_margin, financials)
        share_dilution_5y = self._safe_metric(
            symbol, "share_dilution_5y", _compute_share_dilution_5y, balance_sheet, info, symbol
        )

        return FundamentalsSnapshot(
            ticker=symbol,
            as_of=now,
            years_available=years_available,
            fcf_5y_cumulative=fcf_5y_cumulative,
            interest_coverage=interest_coverage,
            gross_margin=gross_margin,
            ocf_ni_ratio=ocf_ni_ratio,
            net_margin=net_margin,
            share_dilution_5y=share_dilution_5y,
        )
