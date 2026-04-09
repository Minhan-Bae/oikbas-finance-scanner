"""Yahoo Finance channel — price/volume enrichment via v8/chart endpoint.

Why chart endpoint and not quote/quoteSummary:
    Yahoo's v7/quote and v10/quoteSummary now require crumb-based auth and
    increasingly throttle (429). v8/chart is the one endpoint that still
    answers without auth as of 2026-04. It returns price, volume, 52-week
    range, exchange, and longName — but NOT marketCap, sharesShort, or
    shortPercentOfFloat. Those metrics live in the cowork-* channels (M4),
    sourced from the user's monthly Cowork in Chrome scrapes.

What this channel does:
    For each ticker passed in query["tickers"] (typically populated by an
    earlier EDGAR fetch), pull current price, volume, and 52-week range.
    Emit Signal of type "price_context" — used by the analyzer to enrich
    insider purchases with live market state.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from oikbas_finance.channels import register
from oikbas_finance.channels.base import Channel, CheckStatus, Signal
from oikbas_finance.config import YahooConfig, load_config

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


@register("yahoo")
class YahooFinanceChannel(Channel):
    name = "yahoo"

    def __init__(self, config: YahooConfig | None = None):
        cfg = load_config()
        self.cfg = config or cfg.yahoo
        self.headers = {"User-Agent": USER_AGENT}

    # ── Channel interface ──────────────────────────────────────────

    def can_handle(self, query: dict[str, Any]) -> bool:
        # Yahoo enriches existing tickers; needs the analyzer to pass them in.
        # Also handles smallcap_only flag (filter applied in analyzer, not here).
        return bool(query.get("tickers"))

    def check(self) -> tuple[CheckStatus, str]:
        try:
            url = f"{CHART_URL}/AAPL?interval=1d&range=1d"
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            res = data.get("chart", {}).get("result", [])
            if res and res[0].get("meta", {}).get("symbol") == "AAPL":
                return ("healthy", "Yahoo v8/chart reachable")
            return ("degraded", "Yahoo responded but unexpected shape")
        except urllib.error.HTTPError as e:
            return ("down", f"Yahoo HTTP {e.code}: {e.reason}")
        except Exception as e:
            return ("down", f"Yahoo unreachable: {e}")

    def fetch(self, query: dict[str, Any]) -> list[Signal]:
        tickers = query.get("tickers") or []
        if not tickers:
            return []

        signals: list[Signal] = []
        errors = 0
        for ticker in tickers:
            try:
                meta = self._fetch_meta(ticker)
            except Exception as e:
                print(f"[YAHOO] {ticker} fetch failed: {e}", file=sys.stderr)
                errors += 1
                continue
            if not meta:
                continue

            signals.append(Signal(
                ticker=ticker,
                source="yahoo",
                signal_type="price_context",
                timestamp=datetime.utcnow().isoformat(),
                value=meta.get("regularMarketPrice"),
                confidence=1,  # context-only, no actionable signal on its own
                raw=meta,
                metadata={
                    "name": meta.get("longName") or meta.get("shortName"),
                    "exchange": meta.get("exchangeName"),
                    "currency": meta.get("currency"),
                    "regular_market_price": meta.get("regularMarketPrice"),
                    "regular_market_volume": meta.get("regularMarketVolume"),
                    "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
                    "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
                    "previous_close": meta.get("chartPreviousClose"),
                },
            ))
            time.sleep(0.15)  # gentle pacing

        print(f"[YAHOO] enriched {len(signals)}/{len(tickers)} tickers ({errors} errors)",
              file=sys.stderr)
        return signals

    # ── Internal ───────────────────────────────────────────────────

    def _fetch_meta(self, ticker: str) -> dict[str, Any] | None:
        url = f"{CHART_URL}/{ticker}?interval=1d&range=5d"
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        return result[0].get("meta") or None
