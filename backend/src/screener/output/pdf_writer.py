"""Renders a ScreenerRun (or a single Signal) to a human-readable PDF report.

This is purely additive to json_writer.py: the JSON schema stays untouched
(it is spec-locked for the Phase 3 consumer). The PDF is a second, human-
facing artifact rendered from the same ScreenerRun/Signal models, with all
numbers formatted for readability (no raw floats like 540.8800048828125).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from screener.models import BacktestResult, RuleAttribution, RuleResult, ScreenerRun, Signal
from screener.utils.logging import get_logger

logger = get_logger(__name__)

_OUTPUT_DIR = Path("output/reports")
_DISPLAY_TZ = ZoneInfo("America/New_York")

_styles = getSampleStyleSheet()
_TITLE_STYLE = _styles["Title"]
_HEADING_STYLE = _styles["Heading2"]
_NORMAL_STYLE = _styles["Normal"]
_SMALL_STYLE = ParagraphStyle("Small", parent=_styles["Normal"], fontSize=8, leading=10)

_SUMMARY_HEADER = [
    "Rank", "Ticker", "Score", "Rules", "Watchlist",
    "Close", "Change %", "RSI-14", "SMA-200", "P/B",
]
_RULE_HEADER = ["Rule", "Pass", "Weight", "Detail"]
_TRADE_HEADER = [
    "Ticker", "Signal Date", "Score", "Buy Close",
    "Sell Date", "Sell Close", "Return %", "Result",
]
_ATTRIBUTION_HEADER = [
    "Rule", "Weight", "Passed (n / win% / avg%)", "Failed (n / win% / avg%)", "Edge",
]

_GLOSSARY = [
    ("Score", "The percentage of rules a stock passed, weighted by how important each rule is. Higher = more of our criteria line up."),
    ("Rules passed", "How many of the individual checks (out of the total) this stock met."),
    ("Close", "The stock's most recent closing price."),
    ("Volume", "How many shares traded — a rough gauge of investor interest/activity."),
    ("Change %", "How much the price moved versus the previous close."),
    ("RSI-14", "Relative Strength Index over 14 days — a 0-100 gauge of momentum. Below ~30-35 often signals 'oversold' (potentially cheap); above ~70 often signals 'overbought'."),
    ("SMA-50 / SMA-200", "The average closing price over the last 50 / 200 trading days — smoothed trend lines. Price above these lines generally signals an uptrend."),
    ("ATR-14", "Average True Range over 14 days — a measure of how much the price typically swings day to day (volatility)."),
    ("P/B (Price-to-Book)", "The stock's price divided by the company's book (accounting) value. Lower can mean the stock is cheap relative to its underlying assets."),
]


def write_report(
    run: ScreenerRun,
    output_dir: Path = _OUTPUT_DIR,
    rule_descriptions: dict[str, str] | None = None,
) -> Path:
    """Write a full multi-ticker PDF report.

    Writes output/reports/report_<UTC_ISO>.pdf (same timestamp convention as
    json_writer.write_run) and returns the path written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = run.run_timestamp.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"report_{ts}.pdf"

    story: list = []
    story.extend(_header_block(run))
    story.append(Spacer(1, 0.2 * inch))
    if rule_descriptions:
        story.extend(_how_to_read_block(rule_descriptions))
        story.append(Spacer(1, 0.2 * inch))
    story.extend(_summary_table(run.signals, run.alert_threshold))
    story.append(Spacer(1, 0.3 * inch))
    for signal in run.signals:
        story.extend(_ticker_section(signal, run.alert_threshold, rule_descriptions=rule_descriptions))
        story.append(Spacer(1, 0.2 * inch))

    _build_doc(path, story)

    logger.info("report_written", path=str(path), signals=len(run.signals))
    return path


def write_ticker_report(
    signal: Signal,
    alert_threshold: float,
    universe: str,
    output_dir: Path = _OUTPUT_DIR,
    rule_descriptions: dict[str, str] | None = None,
) -> Path:
    """Write a single-ticker PDF report for --ticker debug mode.

    Writes output/reports/report_<TICKER>_<UTC_ISO>.pdf and returns the path
    written. Reuses _header_block/_ticker_section so the per-ticker layout
    has one source of truth shared with write_report.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.now(timezone.utc)
    ts = run_ts.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"report_{signal.ticker}_{ts}.pdf"

    # Wrap the single signal in a ScreenerRun so _header_block has one
    # source of truth for the header layout shared with write_report.
    wrapper_run = ScreenerRun(
        run_timestamp=run_ts,
        universe=universe,
        alert_threshold=alert_threshold,
        signals=[signal],
    )

    story: list = []
    story.extend(_header_block(wrapper_run))
    story.append(Spacer(1, 0.2 * inch))
    if rule_descriptions:
        story.extend(_how_to_read_block(rule_descriptions))
        story.append(Spacer(1, 0.2 * inch))
    story.extend(_ticker_section(signal, alert_threshold, rule_descriptions=rule_descriptions))

    _build_doc(path, story)

    logger.info("report_written", path=str(path), signals=1)
    return path


def write_backtest_report(result: BacktestResult, output_dir: Path = _OUTPUT_DIR) -> Path:
    """Write a historical backtest report to PDF.

    Writes output/reports/backtest_<UTC_ISO>.pdf (timestamp taken at write
    time, same `%Y%m%dT%H%M%SZ` convention as write_report) and returns the
    path written. This is a separate report family from write_report/
    write_ticker_report — it renders a BacktestResult, not a ScreenerRun.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"backtest_{ts}.pdf"

    story: list = []
    story.append(Paragraph("Screener Backtest Report", _TITLE_STYLE))
    story.append(Spacer(1, 0.1 * inch))
    story.extend(_backtest_caveats())
    story.append(Spacer(1, 0.2 * inch))
    story.extend(_backtest_stats(result))
    story.append(Spacer(1, 0.3 * inch))
    story.extend(_rule_attribution_table(result.rule_attribution))
    story.append(Spacer(1, 0.3 * inch))
    story.extend(_trade_table(result.trades))

    _build_doc(path, story, title="Screener Backtest Report")

    logger.info("report_written", path=str(path), signals=result.total_signals)
    return path


def _backtest_caveats() -> list:
    """Plain-language caveats block so the backtest numbers aren't over-read."""
    caveats = (
        "<b>Caveats:</b> This backtest uses a fixed 16-symbol watchlist universe "
        "(config/watchlist.yaml), not the live day-losers screen used in normal "
        "runs — the losers screen only reflects today's movers and cannot be "
        "reconstructed historically. The <b>undervalued_pb</b> rule is dropped "
        "(price-to-book has no historical daily series), so scores here are out "
        "of the remaining 4 rules' combined weight (6.5), not the full 8.0 used "
        "in live runs. The exit rule is a fixed 5-trading-day hold (buy at the "
        "signal day's close, sell at the close N trading days later) — no "
        "stop-loss, profit target, or other exit logic is modeled. No "
        "transaction costs or slippage are included. The sample size (16 "
        "symbols &times; a few weeks) is small — treat these results as "
        "illustrative, not a robust or statistically significant backtest."
    )
    return [Paragraph(caveats, _SMALL_STYLE)]


def _backtest_stats(result: BacktestResult) -> list:
    """Aggregate stats block: period, config echo, and headline numbers,
    including the baseline comparison ('did the rules add edge?')."""
    delta_pp = result.avg_return_pct - result.baseline_avg_return_pct
    sign = "+" if delta_pp >= 0 else ""

    rows = [
        ["Period", f"{_fmt_ts(result.start_date)}  –  {_fmt_ts(result.end_date)}"],
        ["Universe", result.universe],
        ["Holding period", f"{result.holding_days} trading days"],
        ["Alert threshold", _fmt_pct(result.alert_threshold * 100)],
        ["Total signals", str(result.total_signals)],
        ["Wins / Losses", f"{result.wins} / {result.losses}"],
        ["Win rate", _fmt_pct(result.win_rate * 100)],
        ["Avg return per trade", _fmt_pct(result.avg_return_pct)],
        ["Total return (equal-weight)", _fmt_pct(result.total_return_pct)],
        ["Best trade", _fmt_pct(result.best_trade_return_pct)],
        ["Worst trade", _fmt_pct(result.worst_trade_return_pct)],
        ["Baseline avg forward return (all symbol-days)", _fmt_pct(result.baseline_avg_return_pct)],
    ]

    table = Table(rows, hAlign="LEFT", colWidths=[2.8 * inch, 3.2 * inch])
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    table.setStyle(TableStyle(style))

    delta_color = "#1a7f37" if delta_pp >= 0 else "#c0362c"
    delta_line = Paragraph(
        f"<b>Signal avg return vs. baseline: "
        f"<font color='{delta_color}'>{sign}{delta_pp:.2f} pp</font></b>",
        _NORMAL_STYLE,
    )

    return [
        Paragraph("Summary", _HEADING_STYLE),
        Spacer(1, 0.1 * inch),
        table,
        Spacer(1, 0.15 * inch),
        delta_line,
    ]


def _trade_table(trades: list) -> list:
    """Per-trade table, sorted as given (already return-descending from
    run_backtest). Win/loss cells are colored green/red like _rule_table."""
    if not trades:
        return [
            Paragraph("Trades", _HEADING_STYLE),
            Spacer(1, 0.1 * inch),
            Paragraph("No signals fired in this window.", _NORMAL_STYLE),
        ]

    rows: list[list[Any]] = [_TRADE_HEADER]
    for t in trades:
        rows.append([
            t.ticker,
            _fmt_ts(t.signal_date),
            _fmt_pct(t.score * 100),
            _fmt_num(t.buy_close),
            _fmt_ts(t.sell_date),
            _fmt_num(t.sell_close),
            _fmt_pct(t.return_pct),
            "WIN" if t.win else "LOSS",
        ])

    table = Table(rows, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2f38")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    for row_idx, t in enumerate(trades, start=1):
        color = colors.HexColor("#1a7f37") if t.win else colors.HexColor("#c0362c")
        style.append(("TEXTCOLOR", (6, row_idx), (7, row_idx), color))
        style.append(("FONTNAME", (6, row_idx), (7, row_idx), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return [Paragraph("Trades", _HEADING_STYLE), Spacer(1, 0.1 * inch), table]


def _rule_attribution_table(attributions: list) -> list:
    """Per-rule predictive-power table: average forward return on days each
    rule passed vs. days it didn't, across the whole evaluated window (not
    just days a signal fired). Edge column colored green/red like the
    Trades table's WIN/LOSS coloring."""
    if not attributions:
        return [
            Paragraph("Per-Rule Attribution", _HEADING_STYLE),
            Spacer(1, 0.1 * inch),
            Paragraph("No rule attribution data.", _NORMAL_STYLE),
        ]

    rows: list[list[Any]] = [_ATTRIBUTION_HEADER]
    for a in attributions:
        rows.append([
            a.rule_name,
            _fmt_num(a.weight, decimals=1),
            f"{a.passed_count} / {_fmt_pct(a.passed_win_rate * 100)} / {_fmt_pct(a.passed_avg_return_pct)}",
            f"{a.failed_count} / {_fmt_pct(a.failed_win_rate * 100)} / {_fmt_pct(a.failed_avg_return_pct)}",
            f"{'+' if a.edge_pct >= 0 else ''}{a.edge_pct:.2f} pp",
        ])

    table = Table(rows, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2f38")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    for row_idx, a in enumerate(attributions, start=1):
        color = colors.HexColor("#1a7f37") if a.edge_pct >= 0 else colors.HexColor("#c0362c")
        style.append(("TEXTCOLOR", (4, row_idx), (4, row_idx), color))
        style.append(("FONTNAME", (4, row_idx), (4, row_idx), "Helvetica-Bold"))
    table.setStyle(TableStyle(style))
    return [Paragraph("Per-Rule Attribution", _HEADING_STYLE), Spacer(1, 0.1 * inch), table]


def _build_doc(path: Path, story: list, title: str = "Stock Screener Report") -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        title=title,
    )
    doc.build(story)


def _header_block(run: ScreenerRun) -> list:
    """Title, run timestamp, universe, alert threshold, and counts."""
    above = sum(1 for s in run.signals if s.score >= run.alert_threshold)
    lines = [
        Paragraph("Stock Screener Report", _TITLE_STYLE),
        Spacer(1, 0.1 * inch),
        Paragraph(f"<b>Run timestamp:</b> {_fmt_ts(run.run_timestamp)}", _NORMAL_STYLE),
        Paragraph(f"<b>Universe:</b> {run.universe}", _NORMAL_STYLE),
        Paragraph(f"<b>Alert threshold:</b> {_fmt_pct(run.alert_threshold * 100)}", _NORMAL_STYLE),
        Paragraph(
            f"<b>Signals:</b> {len(run.signals)} total, {above} at/above threshold",
            _NORMAL_STYLE,
        ),
    ]
    return lines


def _how_to_read_block(rule_descriptions: dict[str, str]) -> list:
    """Plain-English glossary + per-rule explanation block, shown once near
    the top of the report so non-technical readers have a key before the
    numeric tables start."""
    story: list = [Paragraph("How to Read This Report", _HEADING_STYLE), Spacer(1, 0.05 * inch)]
    for term, definition in _GLOSSARY:
        story.append(Paragraph(f"<b>{term}:</b> {definition}", _SMALL_STYLE))
    story.append(Spacer(1, 0.1 * inch))
    story.append(Paragraph("What each check means", _HEADING_STYLE))
    story.append(Spacer(1, 0.05 * inch))
    for name, description in rule_descriptions.items():
        humanized = name.replace("_", " ").title()
        text = f"<b>{humanized}:</b> {description}" if description else f"<b>{humanized}</b>"
        story.append(Paragraph(text, _SMALL_STYLE))
    return story


def _plain_english_takeaway(signal: Signal, alert_threshold: float, rule_descriptions: dict[str, str]) -> list:
    """Short human-readable summary for a single ticker: overall verdict
    plus which checks it met/missed, in plain language above the numeric
    rule breakdown table."""
    if signal.score >= alert_threshold:
        verdict = "Strong match"
    elif signal.score >= 0.5:
        verdict = "Partial match"
    else:
        verdict = "Weak match"

    headline = (
        f"<b>{verdict}</b> — cleared {signal.rules_passed} of {signal.rules_total} checks."
    )
    story: list = [Paragraph(headline, _NORMAL_STYLE)]

    met = [r.rule_name.replace("_", " ").title() for r in signal.rule_results if r.passed]
    missed = [r.rule_name.replace("_", " ").title() for r in signal.rule_results if not r.passed]

    story.append(Paragraph(f"<b>✓ Met:</b> {', '.join(met) if met else 'None'}", _SMALL_STYLE))
    story.append(Paragraph(f"<b>✗ Missed:</b> {', '.join(missed) if missed else 'None'}", _SMALL_STYLE))
    return story


def _summary_table(signals: list[Signal], alert_threshold: float) -> list:
    """Ranked summary table of all signals, sorted as given (already
    score-descending from main.py:run_screener)."""
    if not signals:
        return [Paragraph("No signals evaluated this run.", _NORMAL_STYLE)]

    rows: list[list[Any]] = [_SUMMARY_HEADER]
    emphasized_rows: list[int] = []

    for i, s in enumerate(signals, start=1):
        snap = s.snapshot
        if s.score >= alert_threshold:
            emphasized_rows.append(i)  # header is row 0
        rows.append([
            str(i),
            s.ticker,
            _fmt_pct(s.score * 100),
            f"{s.rules_passed}/{s.rules_total}",
            "Yes" if snap.get("in_watchlist") else "No",
            _fmt_num(snap.get("close")),
            _fmt_pct(snap.get("change_pct")),
            _fmt_num(snap.get("rsi_14")),
            _fmt_num(snap.get("sma_200")),
            _fmt_pb(snap.get("price_to_book")),
        ])

    table = Table(rows, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b2f38")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    for row_idx in emphasized_rows:
        style.append(("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#fff2b2")))
        style.append(("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold"))

    table.setStyle(TableStyle(style))
    return [Paragraph("Ranked Summary", _HEADING_STYLE), Spacer(1, 0.1 * inch), table]


def _ticker_section(
    signal: Signal,
    alert_threshold: float,
    rule_descriptions: dict[str, str] | None = None,
) -> list:
    """Per-ticker heading, snapshot line, and full rule breakdown table."""
    passed = "PASS" if signal.score >= alert_threshold else ""
    heading = (
        f"{signal.ticker} — {_fmt_pct(signal.score * 100)} — "
        f"{signal.rules_passed}/{signal.rules_total} rules"
        + (f"  [{passed}]" if passed else "")
    )
    story: list = [Paragraph(heading, _HEADING_STYLE)]

    snap = signal.snapshot
    snapshot_bits = [
        f"Close: {_fmt_num(snap.get('close'))}",
        f"Volume: {_fmt_vol(snap.get('volume'))}",
        f"Change: {_fmt_pct(snap.get('change_pct'))}",
        f"RSI-14: {_fmt_num(snap.get('rsi_14'))}",
        f"SMA-50: {_fmt_num(snap.get('sma_50'))}",
        f"SMA-200: {_fmt_num(snap.get('sma_200'))}",
        f"ATR-14: {_fmt_num(snap.get('atr_14'))}",
        f"P/B: {_fmt_pb(snap.get('price_to_book'))}",
    ]
    story.append(Paragraph("  |  ".join(snapshot_bits), _SMALL_STYLE))
    story.append(Spacer(1, 0.05 * inch))
    if rule_descriptions:
        story.extend(_plain_english_takeaway(signal, alert_threshold, rule_descriptions))
        story.append(Spacer(1, 0.05 * inch))
    story.append(_rule_table(signal.rule_results))
    return story


def _rule_table(rule_results: list[RuleResult]) -> Table:
    rows: list[list[Any]] = [_RULE_HEADER]
    for r in rule_results:
        detail = ", ".join(f"{k}={_fmt_detail_value(v)}" for k, v in r.detail.items())
        rows.append([
            r.rule_name,
            "✓" if r.passed else "✗",
            _fmt_num(r.weight, decimals=1),
            Paragraph(detail or "—", _SMALL_STYLE),
        ])

    table = Table(rows, repeatRows=1, hAlign="LEFT", colWidths=[1.6 * inch, 0.5 * inch, 0.6 * inch, 3.3 * inch])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3f4654")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    for row_idx, r in enumerate(rule_results, start=1):
        if r.passed:
            style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), colors.HexColor("#1a7f37")))
        else:
            style.append(("TEXTCOLOR", (1, row_idx), (1, row_idx), colors.HexColor("#c0362c")))
    table.setStyle(TableStyle(style))
    return table


def _fmt_detail_value(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return _fmt_num(v)
    return str(v)


def _fmt_ts(dt: datetime) -> str:
    """Format a datetime for display in US Eastern time (auto EST/EDT via
    zoneinfo), 12-hour clock. Internal storage/JSON stay UTC per spec — this
    is a display-only conversion at the report's I/O edge."""
    return dt.astimezone(_DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")


def _fmt_num(v: Any, decimals: int = 2) -> str:
    """Format a number to a fixed number of decimals; None/inf/NaN render
    as an em dash rather than 'None'/'inf'/'nan'."""
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isinf(fv) or math.isnan(fv):
        return "—"
    return f"{fv:.{decimals}f}"


def _fmt_pb(v: Any) -> str:
    """Price-to-book specific formatter: inf (no data) renders as '—'."""
    return _fmt_num(v)


def _fmt_pct(v: Any, decimals: int = 2) -> str:
    """Format a number already expressed in percentage units, e.g.
    -6.89 -> '-6.89%'."""
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isinf(fv) or math.isnan(fv):
        return "—"
    return f"{fv:.{decimals}f}%"


def _fmt_vol(v: Any) -> str:
    """Humanize a volume number, e.g. 27_754_556 -> '27.75M'."""
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isinf(fv) or math.isnan(fv):
        return "—"
    sign = "-" if fv < 0 else ""
    fv = abs(fv)
    if fv >= 1_000_000_000:
        return f"{sign}{fv / 1_000_000_000:.2f}B"
    if fv >= 1_000_000:
        return f"{sign}{fv / 1_000_000:.2f}M"
    if fv >= 1_000:
        return f"{sign}{fv / 1_000:.2f}K"
    return f"{sign}{fv:.0f}"
