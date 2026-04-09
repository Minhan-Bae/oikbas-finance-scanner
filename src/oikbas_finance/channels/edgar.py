"""SEC EDGAR Form 4 channel.

Ported from oikbas-vault/090_System/093_Scripts/insider_scan.py — same network
calls, same XML parsing, same field semantics. The only difference is that
fetch() returns standardized Signal dicts AND keeps the legacy purchase dict
under signal["raw"] so the vault note generator can render it byte-identically
during M2 regression.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

from oikbas_finance.channels import register
from oikbas_finance.channels.base import Channel, CheckStatus, Signal
from oikbas_finance.config import EdgarConfig, load_config


@register("edgar")
class EDGARChannel(Channel):
    name = "edgar"

    def __init__(self, config: EdgarConfig | None = None, user_agent: str | None = None):
        cfg = load_config()
        self.cfg = config or cfg.edgar
        self.user_agent = user_agent or cfg.user_agent
        self.headers = {"User-Agent": self.user_agent}

    # ── Channel interface ──────────────────────────────────────────

    def can_handle(self, query: dict[str, Any]) -> bool:
        # EDGAR is the canonical insider source — always claim it.
        return True

    def check(self) -> tuple[CheckStatus, str]:
        # Lightweight HEAD-equivalent: search 1 day with max 1 result.
        try:
            url = (
                f"{self.cfg.search_url}?q=%22form+4%22"
                f"&dateRange=custom&startdt=2026-04-01&enddt=2026-04-02&forms=4"
            )
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            total = data.get("hits", {}).get("total", {}).get("value", 0)
            return ("healthy", f"EDGAR reachable, sample query returned total={total}")
        except Exception as exc:
            return ("down", f"EDGAR unreachable: {exc}")

    def fetch(self, query: dict[str, Any]) -> list[Signal]:
        days = int(query.get("days", self.cfg.default_days))
        max_filings = int(query.get("max_filings", self.cfg.max_filings))
        min_value = float(query.get("min_value", 0))

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        print(f"[EDGAR] scan {start_date} → {end_date}, max {max_filings}", file=sys.stderr)

        hits = self._search_form4(start_date, end_date)
        purchases: list[dict[str, Any]] = []
        scanned = 0
        errors = 0

        for hit in hits[:max_filings]:
            src = hit["_source"]
            ciks = src.get("ciks", [])

            xml_text = self._fetch_form4_xml(hit["_id"], ciks)
            if xml_text is None:
                errors += 1
                continue

            purchases.extend(self._parse_purchases(xml_text))
            scanned += 1
            time.sleep(self.cfg.rate_limit_delay)

            if scanned % 50 == 0:
                print(
                    f"  ... {scanned}/{min(len(hits), max_filings)} scanned, "
                    f"{len(purchases)} purchases",
                    file=sys.stderr,
                )

        print(f"[EDGAR] done: scanned={scanned} errors={errors} purchases={len(purchases)}",
              file=sys.stderr)

        if min_value > 0:
            purchases = [p for p in purchases if p["value"] >= min_value]
            print(f"[EDGAR] min_value=${min_value:,.0f} → {len(purchases)} purchases",
                  file=sys.stderr)

        # Wrap each legacy purchase dict in a Signal envelope.
        return [self._purchase_to_signal(p) for p in purchases]

    # ── Internal helpers (lifted from insider_scan.py) ─────────────

    def _search_form4(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        params = (
            f"?q=%22form+4%22"
            f"&dateRange=custom&startdt={start_date}&enddt={end_date}"
            f"&forms=4"
            f"&_source=ciks,display_names,adsh,file_date"
        )
        url = self.cfg.search_url + params
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)

        total = data["hits"]["total"]["value"]
        hits = data["hits"]["hits"]
        print(f"[EDGAR] search OK: total={total}, returned={len(hits)}", file=sys.stderr)
        return hits

    def _fetch_form4_xml(self, doc_id: str, ciks: list[str]) -> str | None:
        parts = doc_id.split(":")
        if len(parts) != 2 or len(ciks) < 2:
            return None
        accession, filename = parts
        issuer_cik = ciks[1].lstrip("0")
        accession_path = accession.replace("-", "")
        xml_url = f"{self.cfg.archive_url}/{issuer_cik}/{accession_path}/{filename}"
        try:
            req = urllib.request.Request(xml_url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8")
        except Exception as e:
            print(f"[EDGAR] xml fetch failed: {xml_url} — {e}", file=sys.stderr)
            return None

    def _parse_purchases(self, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)

        ticker = root.findtext(".//issuerTradingSymbol", "N/A").strip()
        issuer_name = root.findtext(".//issuerName", "N/A").strip()
        owner_name = root.findtext(".//rptOwnerName", "N/A").strip()

        title = root.findtext(".//officerTitle", "").strip()
        is_director = root.findtext(".//isDirector", "false") == "true"
        is_officer = root.findtext(".//isOfficer", "false") == "true"  # noqa: F841
        is_10pct = root.findtext(".//isTenPercentOwner", "false") == "true"

        if title:
            role = title
        elif is_director:
            role = "Director"
        elif is_10pct:
            role = "10% Owner"
        else:
            role = "Other"

        role_priority = 0
        upper = role.upper()
        if "CEO" in upper or "CHIEF EXECUTIVE" in upper:
            role_priority = 5
        elif "CFO" in upper or "CHIEF FINANCIAL" in upper:
            role_priority = 4
        elif "COO" in upper or "PRESIDENT" in upper or "CHAIRMAN" in upper:
            role_priority = 3
        elif is_director:
            role_priority = 2
        elif is_10pct:
            role_priority = 2

        purchases: list[dict[str, Any]] = []
        for txn in root.findall(".//nonDerivativeTransaction"):
            code = txn.findtext(".//transactionCode", "")
            security = txn.findtext(".//securityTitle/value", "").strip()

            if code != "P":
                continue

            shares_str = txn.findtext(".//transactionShares/value", "0") or "0"
            price_str = txn.findtext(".//transactionPricePerShare/value", "0") or "0"
            shares_after_str = txn.findtext(".//sharesOwnedFollowingTransaction/value", "0") or "0"

            try:
                shares = float(shares_str)
                price = float(price_str)
                shares_after = float(shares_after_str)
            except ValueError:
                continue

            value = shares * price
            prev_shares = shares_after - shares
            pct_change = (shares / prev_shares * 100) if prev_shares > 0 else 999.0

            trade_date = txn.findtext(".//transactionDate/value", "N/A")

            purchases.append({
                "ticker": ticker,
                "issuer": issuer_name,
                "insider": owner_name,
                "role": role,
                "role_priority": role_priority,
                "security": security,
                "date": trade_date,
                "shares": int(shares),
                "price": round(price, 2),
                "value": round(value, 0),
                "shares_after": int(shares_after),
                "pct_change": round(pct_change, 1),
            })

        return purchases

    def _purchase_to_signal(self, p: dict[str, Any]) -> Signal:
        return Signal(
            ticker=p["ticker"],
            source="edgar",
            signal_type="insider_purchase",
            timestamp=p["date"],
            value=float(p["value"]),
            confidence=min(5, max(1, p["role_priority"] or 1)),
            raw=p,  # legacy purchase dict — vault_note renders this directly
            metadata={
                "issuer": p["issuer"],
                "insider": p["insider"],
                "role": p["role"],
                "role_priority": p["role_priority"],
                "shares": p["shares"],
                "price": p["price"],
                "shares_after": p["shares_after"],
                "pct_change": p["pct_change"],
            },
        )
