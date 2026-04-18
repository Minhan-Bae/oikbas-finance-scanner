"""oikbas-finance CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from oikbas_finance import __version__
from oikbas_finance.analyzer import (
    attach_price_context,
    classify_signals,
    collect_tickers,
    detect_squeeze_setup,
    extract_purchases,
    filter_smallcap,
    index_by_ticker,
)
from oikbas_finance.channels import REGISTRY, list_channels
from oikbas_finance.config import load_config
from oikbas_finance.doctor import run_doctor
from oikbas_finance.output.vault_note import generate_vault_note


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oikbas-finance",
        description="Channel-based multi-source small-cap signal scanner.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = subparsers.add_parser("scan", help="Run channels and analyze signals")
    p_scan.add_argument("--channels", default="edgar",
                        help="Comma-separated channel names (default: edgar)")
    p_scan.add_argument("--days", type=int, default=7, help="Lookback window in days")
    p_scan.add_argument("--min-value", type=float, default=0,
                        help="Minimum transaction value filter ($)")
    p_scan.add_argument("--max-filings", type=int, default=500,
                        help="Cap on filings scanned per channel")
    p_scan.add_argument("--smallcap-only", action="store_true",
                        help="Filter to market cap < $1B (requires yahoo channel)")
    p_scan.add_argument("--output", choices=["table", "json", "vault"], default="table")

    # doctor
    subparsers.add_parser("doctor", help="Check health of all channels")

    # channels
    p_channels = subparsers.add_parser("channels", help="Channel registry inspection")
    channels_sub = p_channels.add_subparsers(dest="channels_cmd", required=True)
    channels_sub.add_parser("list", help="List registered channels")

    # vision — Gemini Vision 보조 분석
    p_vision = subparsers.add_parser(
        "vision", help="Summarize a macro PDF or analyze a chart image via Gemini Vision"
    )
    vision_sub = p_vision.add_subparsers(dest="vision_cmd", required=True)

    p_pdf = vision_sub.add_parser("pdf", help="Summarize a PDF report as Markdown")
    p_pdf.add_argument("path", help="Path to PDF file")
    p_pdf.add_argument("--focus", default=None, help="Optional framing (e.g. 'small-cap swing')")
    p_pdf.add_argument("--model", default="gemini-2.5-flash")

    p_chart = vision_sub.add_parser("chart", help="Structured analysis of a chart image")
    p_chart.add_argument("path", help="Path to chart image (PNG/JPG)")
    p_chart.add_argument("--context", default=None, help="Ticker/timeframe context")
    p_chart.add_argument("--model", default="gemini-2.5-flash")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor()

    if args.command == "channels":
        if args.channels_cmd == "list":
            for name in list_channels():
                print(name)
            return 0

    if args.command == "scan":
        return _run_scan(args)

    if args.command == "vision":
        return _run_vision(args)

    parser.print_help()
    return 0


def _run_vision(args: argparse.Namespace) -> int:
    from pathlib import Path

    from oikbas_finance.vision import (
        VisionError,
        analyze_chart,
        summarize_pdf,
    )

    path = Path(args.path)
    if not path.exists():
        print(f"[vision] file not found: {path}", file=sys.stderr)
        return 2
    try:
        if args.vision_cmd == "pdf":
            md = summarize_pdf(path, focus=args.focus, model=args.model)
            print(md)
            return 0
        if args.vision_cmd == "chart":
            result = analyze_chart(path, context=args.context, model=args.model)
            print(json.dumps(result.raw, ensure_ascii=False, indent=2))
            return 0
    except VisionError as exc:
        print(f"[vision] {exc}", file=sys.stderr)
        return 1
    return 2


def _run_scan(args: argparse.Namespace) -> int:
    """Dispatch to channels, analyze, render output.

    Channel ordering matters: insider sources (edgar, cowork-openinsider)
    run first to discover tickers, then yahoo enriches with price context,
    then cowork-finviz / cowork-short-interest provides mcap/short metrics.
    """
    requested = [name.strip() for name in args.channels.split(",") if name.strip()]
    cfg = load_config()

    # Trigger channel module imports so registry populates.
    list_channels()

    base_query = {
        "days": args.days,
        "min_value": args.min_value,
        "max_filings": args.max_filings,
        "smallcap_only": args.smallcap_only,
    }

    # Phase 1: insider + short_interest sources (don't need ticker hints)
    insider_channels = [n for n in requested if n != "yahoo"]
    yahoo_requested = "yahoo" in requested

    all_signals = []
    for name in insider_channels:
        if name not in REGISTRY:
            print(f"[scan] unknown channel: {name}", file=sys.stderr)
            return 2
        channel = REGISTRY[name]()
        query = dict(base_query)
        if not channel.can_handle(query):
            print(f"[scan] channel {name} declined query", file=sys.stderr)
            continue
        all_signals.extend(channel.fetch(query))

    # Phase 2: Yahoo enrichment for discovered tickers
    if yahoo_requested:
        tickers = collect_tickers(all_signals)
        if tickers:
            yahoo = REGISTRY["yahoo"]()
            yq = dict(base_query)
            yq["tickers"] = tickers
            if yahoo.can_handle(yq):
                all_signals.extend(yahoo.fetch(yq))

    purchases = extract_purchases(all_signals)
    classified = classify_signals(purchases)

    # Cross-channel enrichment
    short_index = index_by_ticker(all_signals, "short_interest")
    price_index = index_by_ticker(all_signals, "price_context")

    if args.smallcap_only and short_index:
        classified = filter_smallcap(classified, short_index, cfg.yahoo.smallcap_mcap_threshold)

    attach_price_context(classified, price_index)
    squeeze = detect_squeeze_setup(
        classified, short_index,
        smallcap_mcap_threshold=cfg.yahoo.smallcap_mcap_threshold,
        short_float_high=cfg.yahoo.short_float_high,
    )

    end_date = datetime.now().strftime("%Y-%m-%d")

    if args.output == "json":
        payload = {
            "scan_date": end_date,
            "period_days": args.days,
            "total_purchases": len(purchases),
            "signals": {
                k: [{"ticker": s["ticker"], "rating": s["rating"], "pattern": s["pattern"],
                     "reason": s["reason"], "total_value": s["total_value"]}
                    for s in v]
                for k, v in classified.items()
            },
            "squeeze_setup": squeeze,
            "purchases": purchases,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.output == "vault":
        note = generate_vault_note(classified, purchases, end_date, args.days)
        today = datetime.now().strftime("%y%m%d")
        note_path = cfg.output.vault_finance_dir / f"{today}_Insider_Scan.md"
        try:
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(note, encoding="utf-8")
            print(f"[VAULT] note written: {note_path}", file=sys.stderr)
        except Exception as exc:
            print(f"[VAULT] write failed: {exc}", file=sys.stderr)
        print(note)
        return 0

    # default: table
    print(f"\n{'Ticker':<8} {'Insider':<28} {'Role':<18} {'Date':<12} "
          f"{'Shares':>8} {'Price':>8} {'Value':>12} {'%Chg':>6}")
    print("-" * 110)
    for p in sorted(purchases, key=lambda x: -x["value"]):
        print(f"{p['ticker']:<8} {p['insider'][:27]:<28} {p['role'][:17]:<18} "
              f"{p['date']:<12} {p['shares']:>8,} {p['price']:>8.2f} "
              f"${p['value']:>10,.0f} {p['pct_change']:>5.1f}%")

    all_sig = classified["cluster"] + classified["large"] + classified["consistent"]
    if all_sig:
        print("\n=== 시그널 요약 ===")
        for s in sorted(all_sig, key=lambda x: -x["total_value"]):
            print(f"  {s['rating']} {s['ticker']:<8} {s['pattern']:<20} "
                  f"${s['total_value']:>10,.0f} — {s['reason']}")

    if squeeze:
        print("\n=== ★★★★★+ Squeeze Setup (Cluster × Smallcap × High Short) ===")
        for s in sorted(squeeze, key=lambda x: -x["total_value"]):
            print(f"  {s['rating']} {s['ticker']:<8} {s['pattern']:<15} "
                  f"${s['total_value']:>10,.0f} — {s['reason']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
