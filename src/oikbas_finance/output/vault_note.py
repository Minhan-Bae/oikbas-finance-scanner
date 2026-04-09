"""Vault note (markdown) generator.

Direct port of insider_scan.py:226-311 generate_vault_note(). Output must be
byte-identical to the legacy generator for the M2 regression diff to pass.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def generate_vault_note(
    signals: dict[str, list[dict[str, Any]]],
    purchases: list[dict[str, Any]],
    scan_date: str,
    days: int,
) -> str:
    total_purchases = len(purchases)
    total_value = sum(p["value"] for p in purchases)

    all_signals = signals["cluster"] + signals["large"] + signals["consistent"]
    top_signals = sorted(all_signals, key=lambda s: -s["total_value"])

    lines = [
        "---",
        'status: "active"',
        "tags:",
        '  - "finance"',
        '  - "insider-buying"',
        '  - "signal"',
        f'created: "{datetime.now().strftime("%Y-%m-%d")}"',
        f'scan_period: "{days}d"',
        f"total_purchases: {total_purchases}",
        f"total_value: {int(total_value)}",
        f'cluster_count: {len(signals["cluster"])}',
        f'large_count: {len(signals["large"])}',
        f'consistent_count: {len(signals["consistent"])}',
        'source: "SEC EDGAR API"',
        "---",
        "",
        f"# Insider Scan — {scan_date}",
        "",
        f"**기간**: 최근 {days}일 | **총 매수**: {total_purchases}건 | **총 금액**: ${total_value:,.0f}",
        "**소스**: SEC EDGAR Form 4 (자동 수집)",
        "",
    ]

    if signals["cluster"]:
        lines.append("## ★★★★★ Cluster Buy (복수 내부자 동시 매수)")
        lines.append("")
        for s in signals["cluster"]:
            lines.append(f"### {s['ticker']}")
            lines.append(f"- **패턴**: {s['reason']}")
            lines.append(f"- **총 금액**: ${s['total_value']:,.0f}")
            for t in s["transactions"]:
                lines.append(
                    f"  - {t['insider']} ({t['role']}) | {t['date']} | "
                    f"{t['shares']:,}주 × ${t['price']} = ${t['value']:,.0f} | "
                    f"보유량 +{t['pct_change']}%"
                )
            lines.append("")

    if signals["large"]:
        lines.append("## ★★★★ Large Purchase (대규모 / C-level)")
        lines.append("")
        for s in signals["large"]:
            lines.append(f"### {s['ticker']}")
            lines.append(f"- **패턴**: {s['reason']}")
            for t in s["transactions"]:
                lines.append(
                    f"  - {t['insider']} ({t['role']}) | {t['date']} | "
                    f"${t['value']:,.0f} | +{t['pct_change']}%"
                )
            lines.append("")

    if signals["consistent"]:
        lines.append("## ★★★ Consistent Buyer (반복 매수)")
        lines.append("")
        for s in signals["consistent"]:
            lines.append(f"### {s['ticker']}")
            lines.append(f"- **패턴**: {s['reason']}")
            lines.append(f"- **총 금액**: ${s['total_value']:,.0f}")
            lines.append("")

    lines.append("## 전체 매수 목록")
    lines.append("")
    lines.append("| Ticker | Insider | Role | Date | Shares | Price | Value | +% |")
    lines.append("|--------|---------|------|------|-------:|------:|------:|---:|")
    for p in sorted(purchases, key=lambda x: -x["value"]):
        lines.append(
            f"| {p['ticker']} | {p['insider'][:20]} | {p['role'][:15]} | {p['date']} | "
            f"{p['shares']:,} | ${p['price']:.2f} | ${p['value']:,.0f} | {p['pct_change']}% |"
        )
    lines.append("")

    lines.append("## Thales 교차분석 대기")
    lines.append("")
    if top_signals:
        tickers = list(set(s["ticker"] for s in top_signals))
        lines.append(f"내부자 매수 감지 종목: **{', '.join(tickers)}**")
        lines.append("")
        lines.append("→ /thales-signal 실행 시 위 종목에 대한 기술적 분석 교차 확인 필요")
    else:
        lines.append("현재 주기에 유의미한 시그널 없음. 다음 스캔 대기.")

    lines.append("")
    return "\n".join(lines)
