"""Multi-channel signal analyzer.

For M2: replicates `classify_signals()` from insider_scan.py:163-222 byte for byte.
Operates on the legacy purchase dicts under `signal["raw"]` for the EDGAR
channel, so vault note output is identical.

For M3: extended to cross-match against Yahoo (price context) + Cowork
(market_cap + short_pct_of_float) to detect Squeeze Setup patterns. Gracefully
degrades when Cowork data is absent.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from oikbas_finance.channels.base import Signal


def extract_purchases(signals: list[Signal]) -> list[dict[str, Any]]:
    """Pull legacy purchase dicts out of EDGAR signals.

    Other channels can yield purchase-shaped raw dicts too (cowork-openinsider
    will), as long as they share the same field schema.
    """
    purchases: list[dict[str, Any]] = []
    for s in signals:
        if s.get("signal_type") != "insider_purchase":
            continue
        raw = s.get("raw")
        if isinstance(raw, dict) and "ticker" in raw and "value" in raw:
            purchases.append(raw)
    return purchases


def classify_signals(purchases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group purchases by ticker and emit Cluster / Large / Consistent / Other.

    Direct port of insider_scan.py:163-222. Field semantics preserved.
    """
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in purchases:
        by_ticker[p["ticker"]].append(p)

    signals: dict[str, list[dict[str, Any]]] = {
        "cluster": [],
        "large": [],
        "consistent": [],
        "other": [],
    }

    for ticker, txns in by_ticker.items():
        unique_insiders = set(t["insider"] for t in txns)
        total_value = sum(t["value"] for t in txns)
        max_single = max(t["value"] for t in txns)
        max_role = max(t["role_priority"] for t in txns)

        if len(unique_insiders) >= 2:
            signals["cluster"].append({
                "ticker": ticker,
                "rating": "★★★★★",
                "pattern": "Cluster Buy",
                "insiders": len(unique_insiders),
                "total_value": total_value,
                "transactions": txns,
                "reason": f"{len(unique_insiders)}명 내부자 동시 매수 (${total_value:,.0f})",
            })
        elif max_single >= 100_000 or max_role >= 4:
            signals["large"].append({
                "ticker": ticker,
                "rating": "★★★★",
                "pattern": "Large Purchase",
                "insiders": len(unique_insiders),
                "total_value": total_value,
                "transactions": txns,
                "reason": f"대규모 매수 ${max_single:,.0f}" + (" (C-level)" if max_role >= 4 else ""),
            })
        elif len(txns) >= 2:
            signals["consistent"].append({
                "ticker": ticker,
                "rating": "★★★",
                "pattern": "Consistent Buyer",
                "insiders": len(unique_insiders),
                "total_value": total_value,
                "transactions": txns,
                "reason": f"{txns[0]['insider']} 반복 매수 {len(txns)}회",
            })
        else:
            signals["other"].append({
                "ticker": ticker,
                "rating": "★★",
                "pattern": "Single Purchase",
                "insiders": 1,
                "total_value": total_value,
                "transactions": txns,
                "reason": f"단일 매수 ${total_value:,.0f}",
            })

    return signals


# ── M3+: cross-channel enrichment ──────────────────────────────────


def index_by_ticker(signals: list[Signal], signal_type: str) -> dict[str, Signal]:
    """Pull signals of a given type into a ticker → signal lookup."""
    out: dict[str, Signal] = {}
    for s in signals:
        if s.get("signal_type") == signal_type:
            out[s["ticker"]] = s
    return out


def detect_squeeze_setup(
    classified: dict[str, list[dict[str, Any]]],
    short_index: dict[str, Signal],
    smallcap_mcap_threshold: float = 1_000_000_000.0,
    short_float_high: float = 0.20,
) -> list[dict[str, Any]]:
    """Find tickers that satisfy: cluster buy + smallcap + high short float.

    short_index is keyed by ticker; values are Signal dicts of type
    "short_interest" with metadata.market_cap and metadata.short_pct_of_float
    (both as floats).

    Returns ★★★★★+ Squeeze Setup entries. Empty list if short_index is empty
    (graceful degradation when Cowork data isn't available).
    """
    if not short_index:
        return []

    out: list[dict[str, Any]] = []
    for cluster in classified.get("cluster", []):
        ticker = cluster["ticker"]
        short_sig = short_index.get(ticker)
        if not short_sig:
            continue

        meta = short_sig.get("metadata", {}) or {}
        mcap = meta.get("market_cap")
        short_pct = meta.get("short_pct_of_float")

        if mcap is None or short_pct is None:
            continue
        if mcap >= smallcap_mcap_threshold:
            continue
        if short_pct < short_float_high:
            continue

        out.append({
            "ticker": ticker,
            "rating": "★★★★★+",
            "pattern": "Squeeze Setup",
            "insiders": cluster["insiders"],
            "total_value": cluster["total_value"],
            "transactions": cluster["transactions"],
            "market_cap": mcap,
            "short_pct_of_float": short_pct,
            "reason": (
                f"Cluster Buy × Smallcap (${mcap/1e6:.1f}M) × "
                f"Short {short_pct*100:.1f}%"
            ),
        })
    return out


def filter_smallcap(
    classified: dict[str, list[dict[str, Any]]],
    short_index: dict[str, Signal],
    smallcap_mcap_threshold: float = 1_000_000_000.0,
) -> dict[str, list[dict[str, Any]]]:
    """Drop entries whose ticker has known mcap >= threshold.

    Tickers without mcap data are KEPT (no info ≠ disqualified).
    """
    if not short_index:
        return classified

    def keep(ticker: str) -> bool:
        sig = short_index.get(ticker)
        if not sig:
            return True
        mcap = (sig.get("metadata", {}) or {}).get("market_cap")
        if mcap is None:
            return True
        return mcap < smallcap_mcap_threshold

    return {
        bucket: [item for item in items if keep(item["ticker"])]
        for bucket, items in classified.items()
    }


def attach_price_context(
    classified: dict[str, list[dict[str, Any]]],
    price_index: dict[str, Signal],
) -> None:
    """Mutate classified signals to attach Yahoo price_context metadata.

    Each entry gains a `price_context` key (None if no Yahoo data for ticker).
    """
    for bucket in classified.values():
        for item in bucket:
            sig = price_index.get(item["ticker"])
            item["price_context"] = sig.get("metadata") if sig else None


def collect_tickers(signals: list[Signal]) -> list[str]:
    """Get unique ticker list from a heterogeneous signal list."""
    seen: set[str] = set()
    out: list[str] = []
    for s in signals:
        t = s.get("ticker")
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out
