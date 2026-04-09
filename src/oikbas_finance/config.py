"""Configuration loader for ~/.config/oikbas-finance/config.yaml.

Schema (all keys optional, defaults below):

    user_agent: "oikbas miinh.anr@gmail.com"  # required by SEC EDGAR
    edgar:
      rate_limit_delay: 0.12   # seconds between requests (~8 req/sec)
      default_days: 7
      max_filings: 500
      min_value: 100000
    yahoo:
      smallcap_mcap_threshold: 1000000000   # $1B
      short_float_high: 0.20                # 20%
    cowork_import:
      base_dir: "~/cowork-export"
    output:
      vault_finance_dir: "/mnt/c/Users/HAN/Documents/workspace/oikbas-vault/030_Areas/034_Finance"
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(os.environ.get(
    "OIKBAS_FINANCE_CONFIG",
    str(Path.home() / ".config" / "oikbas-finance" / "config.yaml"),
))

_DEFAULT_VAULT_FINANCE_DIR = Path(
    "/mnt/c/Users/HAN/Documents/workspace/oikbas-vault/030_Areas/034_Finance"
)


@dataclass
class EdgarConfig:
    rate_limit_delay: float = 0.12
    default_days: int = 7
    max_filings: int = 500
    min_value: float = 100_000.0
    search_url: str = "https://efts.sec.gov/LATEST/search-index"
    archive_url: str = "https://www.sec.gov/Archives/edgar/data"


@dataclass
class YahooConfig:
    smallcap_mcap_threshold: float = 1_000_000_000.0  # $1B
    short_float_high: float = 0.20                    # 20%
    quote_url: str = "https://query2.finance.yahoo.com/v7/finance/quote"


@dataclass
class CoworkImportConfig:
    base_dir: Path = field(default_factory=lambda: Path.home() / "cowork-export")


@dataclass
class OutputConfig:
    vault_finance_dir: Path = field(default_factory=lambda: _DEFAULT_VAULT_FINANCE_DIR)


@dataclass
class Config:
    user_agent: str = "oikbas miinh.anr@gmail.com"
    edgar: EdgarConfig = field(default_factory=EdgarConfig)
    yahoo: YahooConfig = field(default_factory=YahooConfig)
    cowork_import: CoworkImportConfig = field(default_factory=CoworkImportConfig)
    output: OutputConfig = field(default_factory=OutputConfig)


def load_config(path: Path | str | None = None) -> Config:
    """Load config from YAML, falling back to defaults for missing keys."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        return Config()

    raw: dict[str, Any] = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    edgar_raw = raw.get("edgar", {}) or {}
    yahoo_raw = raw.get("yahoo", {}) or {}
    cowork_raw = raw.get("cowork_import", {}) or {}
    output_raw = raw.get("output", {}) or {}

    return Config(
        user_agent=raw.get("user_agent", "oikbas miinh.anr@gmail.com"),
        edgar=EdgarConfig(**{**EdgarConfig().__dict__, **edgar_raw}),
        yahoo=YahooConfig(**{**YahooConfig().__dict__, **yahoo_raw}),
        cowork_import=CoworkImportConfig(
            base_dir=Path(cowork_raw.get("base_dir", str(Path.home() / "cowork-export"))).expanduser()
        ),
        output=OutputConfig(
            vault_finance_dir=Path(
                output_raw.get("vault_finance_dir", str(_DEFAULT_VAULT_FINANCE_DIR))
            ).expanduser()
        ),
    )
