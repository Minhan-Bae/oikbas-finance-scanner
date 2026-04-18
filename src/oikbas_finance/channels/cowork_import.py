"""Cowork import channels.

Reads JSON exports produced by Claude Cowork in Chrome sessions (the user's
monthly manual scrape of OpenInsider, Finviz short interest, Yahoo smallcap
gainers). The Cowork session bypasses Cloudflare/bot blocks; this channel
just consumes the resulting files — no live scraping.

Expected layout (override via config or env OIKBAS_FINANCE_CONFIG):
    ~/cowork-export/
        openinsider/<YYMMDD>.json     → insider purchase rows
        finviz/<YYMMDD>.json           → short interest + market cap rows
        gainers/<YYMMDD>.json          → smallcap gainers (price/volume/mcap)

For convenience during M4 verification, we also accept the existing seed
files at /mnt/c/Users/HAN/Documents/workspace/seed_data/ as a fallback so
the user can validate without copying anything.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from oikbas_finance.channels import register
from oikbas_finance.channels.base import Channel, CheckStatus, Signal
from oikbas_finance.config import CoworkImportConfig, load_config

SEED_FALLBACK = Path("/mnt/c/Users/HAN/Documents/workspace/seed_data")

# ── Shared JSON loader (handles double-encoded strings) ────────────


def _load_json(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    # Some Cowork exports double-encode the payload as a string.
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _newest_export(base_dir: Path, subdir: str, fallback_glob: str) -> Path | None:
    """Look in base_dir/subdir/*.json first, then in seed_data/<fallback_glob>."""
    primary = base_dir / subdir
    if primary.exists():
        files = sorted(primary.glob("*.json"))
        if files:
            return files[-1]
    if SEED_FALLBACK.exists():
        candidates = sorted(SEED_FALLBACK.glob(fallback_glob))
        if candidates:
            return candidates[-1]
    return None


# ── Value parsers (Cowork exports use display strings) ─────────────

_NUM_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _parse_money(value: Any) -> float | None:
    """'$0.01' / '+$113,976' / '794.46M' / 1234 → float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("$", "").replace(",", "").replace("+", "")
    if not s:
        return None
    suffix = s[-1].upper()
    if suffix in _NUM_SUFFIX:
        try:
            return float(s[:-1]) * _NUM_SUFFIX[suffix]
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(value: Any) -> int | None:
    """'+7,842,032' / '17,862,032' → int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip().replace(",", "").replace("+", "").replace("$", "")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_pct(value: Any) -> float | None:
    """'52.90%' / '+78%' / 0.529 → 0.529 (fraction)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 100 if abs(v) > 1.5 else v
    s = str(value).strip().replace("%", "").replace("+", "")
    if not s:
        return None
    try:
        v = float(s)
        return v / 100 if abs(v) > 1.5 else v
    except ValueError:
        return None


def _role_priority_from_title(title: str) -> int:
    upper = (title or "").upper()
    if "CEO" in upper or "CHIEF EXECUTIVE" in upper:
        return 5
    if "CFO" in upper or "CHIEF FINANCIAL" in upper:
        return 4
    if "COO" in upper or "PRESIDENT" in upper or "CHAIRMAN" in upper:
        return 3
    if "DIRECTOR" in upper:
        return 2
    if "10%" in upper or "10 PERCENT" in upper:
        return 2
    return 1


# ── OpenInsider channel ────────────────────────────────────────────


@register("cowork-openinsider")
class CoworkOpenInsiderChannel(Channel):
    """Consume OpenInsider JSON exports from Cowork sessions.

    Maps each row to the same legacy purchase dict shape that EDGARChannel
    produces, so the analyzer treats both sources uniformly.
    """

    name = "cowork-openinsider"

    def __init__(self, cowork_cfg: CoworkImportConfig | None = None):
        cfg = load_config()
        self.cfg = cowork_cfg or cfg.cowork_import

    def can_handle(self, query: dict[str, Any]) -> bool:
        return True

    def check(self) -> tuple[CheckStatus, str]:
        path = _newest_export(self.cfg.base_dir, "openinsider", "openinsider*.json")
        if path is None:
            return ("skipped", f"no openinsider export found in {self.cfg.base_dir} or seed fallback")
        return ("healthy", f"latest export: {path}")

    def fetch(self, query: dict[str, Any]) -> list[Signal]:
        path = _newest_export(self.cfg.base_dir, "openinsider", "openinsider*.json")
        if path is None:
            print("[COWORK-OI] no export available", file=sys.stderr)
            return []

        data = _load_json(path)
        rows = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            print(f"[COWORK-OI] unexpected shape in {path}", file=sys.stderr)
            return []

        min_value = float(query.get("min_value", 0))
        signals: list[Signal] = []

        for row in rows:
            ticker = (row.get("ticker") or "").strip()
            insider = (row.get("insider") or "").strip()
            title = (row.get("title") or "").strip()
            price = _parse_money(row.get("price"))
            qty = _parse_int(row.get("qty"))
            owned = _parse_int(row.get("owned"))
            value = _parse_money(row.get("value"))
            delta_own = _parse_pct(row.get("delta_own"))
            trade_date = row.get("trade_date") or row.get("filing_date") or "N/A"

            if not ticker or value is None or qty is None:
                continue
            if value < min_value:
                continue

            role_priority = _role_priority_from_title(title)
            purchase = {
                "ticker": ticker,
                "issuer": (row.get("company") or "").strip(),
                "insider": insider,
                "role": title or "Other",
                "role_priority": role_priority,
                "security": "Common Stock",
                "date": trade_date.split(" ")[0] if isinstance(trade_date, str) else "N/A",
                "shares": qty,
                "price": round(price or 0, 2),
                "value": round(value, 0),
                "shares_after": owned or 0,
                "pct_change": round((delta_own or 0) * 100, 1),
            }
            signals.append(Signal(
                ticker=ticker,
                source="cowork-openinsider",
                signal_type="insider_purchase",
                timestamp=purchase["date"],
                value=float(value),
                confidence=min(5, max(1, role_priority)),
                raw=purchase,
                metadata={
                    "issuer": purchase["issuer"],
                    "insider": insider,
                    "role": title,
                    "role_priority": role_priority,
                    "shares": qty,
                    "price": purchase["price"],
                    "shares_after": owned or 0,
                    "pct_change": purchase["pct_change"],
                    "export_path": str(path),
                },
            ))

        print(f"[COWORK-OI] loaded {len(signals)} purchases from {path.name}", file=sys.stderr)
        return signals


# ── Finviz / short interest channel ────────────────────────────────


@register("cowork-finviz")
class CoworkFinvizChannel(Channel):
    """Consume Finviz / short interest JSON exports.

    Each row carries market_cap, float, shares_short, short_pct_of_float,
    short_ratio_days_to_cover. Emits Signals of type "short_interest" used
    by the analyzer's Squeeze Setup detection.
    """

    name = "cowork-finviz"

    def __init__(self, cowork_cfg: CoworkImportConfig | None = None):
        cfg = load_config()
        self.cfg = cowork_cfg or cfg.cowork_import

    def can_handle(self, query: dict[str, Any]) -> bool:
        return True

    def check(self) -> tuple[CheckStatus, str]:
        path = self._find_export()
        if path is None:
            return ("skipped", f"no finviz/short export found in {self.cfg.base_dir} or seed fallback")
        return ("healthy", f"latest export: {path}")

    def fetch(self, query: dict[str, Any]) -> list[Signal]:
        path = self._find_export()
        if path is None:
            print("[COWORK-FV] no export available", file=sys.stderr)
            return []

        data = _load_json(path)
        # Accept either bare list or dict-with-data wrapper.
        if isinstance(data, dict):
            rows = data.get("data") or data.get("stocks") or []
        else:
            rows = data
        if not isinstance(rows, list):
            print(f"[COWORK-FV] unexpected shape in {path}", file=sys.stderr)
            return []

        signals: list[Signal] = []
        for row in rows:
            symbol = (row.get("symbol") or row.get("ticker") or "").strip()
            if not symbol:
                continue
            mcap = _parse_money(row.get("market_cap"))
            short_pct = _parse_pct(row.get("short_pct_of_float"))
            short_ratio = _parse_money(row.get("short_ratio_days_to_cover")
                                        or row.get("short_ratio"))
            signals.append(Signal(
                ticker=symbol,
                source="cowork-finviz",
                signal_type="short_interest",
                timestamp=row.get("timestamp") or row.get("data_date") or datetime.utcnow().isoformat(),
                value=short_pct,
                confidence=3,
                raw=row,
                metadata={
                    "market_cap": mcap,
                    "float": _parse_int(row.get("float")) or _parse_money(row.get("float")),
                    "shares_short": _parse_int(row.get("shares_short")) or _parse_money(row.get("shares_short")),
                    "short_pct_of_float": short_pct,
                    "short_ratio_days_to_cover": short_ratio,
                    "export_path": str(path),
                },
            ))

        print(f"[COWORK-FV] loaded {len(signals)} short_interest rows from {path.name}",
              file=sys.stderr)
        return signals

    def _find_export(self) -> Path | None:
        """Look in finviz/ then short_interest/, fall back to seed_data."""
        for sub, fallback in (
            ("finviz", "short_interest*.json"),
            ("short_interest", "short_interest*.json"),
        ):
            p = _newest_export(self.cfg.base_dir, sub, fallback)
            if p is not None:
                return p
        return None
