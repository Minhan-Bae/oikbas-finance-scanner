"""Channel abstract base + standardized Signal dict.

Borrowed from Panniantong/Agent-Reach `channels/base.py` (MIT) — adapted to
financial signal use case.

Every channel returns a list of Signal dicts. The analyzer treats them
uniformly regardless of source — that's the whole point of the abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, TypedDict


class Signal(TypedDict, total=False):
    """Standardized signal output across all channels.

    Required fields: ticker, source, signal_type, timestamp, confidence.
    Other fields are optional / channel-specific and live under `metadata`.
    """

    ticker: str
    source: str               # e.g. "edgar", "yahoo", "cowork-openinsider"
    signal_type: str          # e.g. "insider_purchase", "short_squeeze", "smallcap_surge"
    timestamp: str            # ISO 8601 (UTC or +TZ); event time, not fetch time
    value: float | None       # interpretation depends on signal_type ($ for purchase, % for short)
    confidence: int           # 1-5, maps to ★ rating in vault notes
    raw: dict[str, Any]       # original payload for audit / debugging
    metadata: dict[str, Any]  # channel-specific extras (insider_name, role, mcap, short_float, ...)


CheckStatus = Literal["healthy", "degraded", "down", "skipped"]


class Channel(ABC):
    """Abstract base for all data source channels."""

    name: str  # set by subclasses; used as registry key + CLI flag

    @abstractmethod
    def can_handle(self, query: dict[str, Any]) -> bool:
        """Return True if this channel can serve the given query.

        Query is a free-form dict — channels inspect what they need.
        Common keys: `days`, `min_value`, `smallcap_only`, `tickers`.
        """

    @abstractmethod
    def check(self) -> tuple[CheckStatus, str]:
        """Lightweight liveness probe. Used by `oikbas-finance doctor`.

        Should NOT make heavy API calls. Verify credentials, reachability,
        cache freshness. Return (status, human-readable message).
        """

    @abstractmethod
    def fetch(self, query: dict[str, Any]) -> list[Signal]:
        """Fetch raw data and return list of standardized Signal dicts.

        Channels MUST return Signal dicts only — no channel-specific shapes
        leak to the analyzer.
        """

    def __repr__(self) -> str:
        return f"<Channel {self.name}>"
