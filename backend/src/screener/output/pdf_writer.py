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

from screener.models import RuleResult, ScreenerRun, Signal
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


def write_report(run: ScreenerRun, output_dir: Path = _OUTPUT_DIR) -> Path:
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
    story.extend(_summary_table(run.signals, run.alert_threshold))
    story.append(Spacer(1, 0.3 * inch))
    for signal in run.signals:
        story.extend(_ticker_section(signal, run.alert_threshold))
        story.append(Spacer(1, 0.2 * inch))

    _build_doc(path, story)

    logger.info("report_written", path=str(path), signals=len(run.signals))
    return path


def write_ticker_report(
    signal: Signal,
    alert_threshold: float,
    universe: str,
    output_dir: Path = _OUTPUT_DIR,
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
    story.extend(_ticker_section(signal, alert_threshold))

    _build_doc(path, story)

    logger.info("report_written", path=str(path), signals=1)
    return path


def _build_doc(path: Path, story: list) -> None:
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        title="Stock Screener Report",
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


def _ticker_section(signal: Signal, alert_threshold: float) -> list:
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
