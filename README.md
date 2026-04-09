# oikbas-finance-scanner

Channel-based multi-source signal scanner for small-cap swing trading.

## Why this exists

The legacy [`insider_scan.py`](../oikbas-vault/090_System/093_Scripts/insider_scan.py)
fetched SEC EDGAR Form 4 only — a single source baked into a single file.
Adding a new source (Yahoo Finance, OpenInsider, Finviz short interest) meant
copy-pasting the boilerplate again.

This package introduces a `Channel` abstraction (borrowed from
[Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach), MIT) so
each source becomes a small module that returns the same standardized
`Signal` dict. The analyzer then cross-matches across all of them.

## Channels

| Channel | Source | Signal type | Lives because |
|---|---|---|---|
| `edgar` | SEC EDGAR Form 4 (live API) | `insider_purchase` | Authoritative + free + no auth |
| `yahoo` | Yahoo Finance v8/chart endpoint | `price_context` | Auth-free price/volume/52wk enrichment |
| `cowork-openinsider` | JSON exports from Cowork in Chrome | `insider_purchase` | OpenInsider blocks bots → Cowork unblocks |
| `cowork-finviz` | JSON exports from Cowork in Chrome | `short_interest` | Finviz/short interest blocks bots → Cowork unblocks |

The two `cowork-*` channels read JSON files the user produces in monthly
Claude Cowork in Chrome sessions. Default location:
`~/cowork-export/{openinsider,finviz}/<YYMMDD>.json`. As a convenience the
channels also fall back to `/mnt/c/Users/HAN/Documents/workspace/seed_data/`
so you can validate without copying anything.

## Quick start

```bash
# Run via wrapper (auto-finds the right Python)
./bin/oikbas-finance --version
./bin/oikbas-finance doctor
./bin/oikbas-finance channels list

# EDGAR-only scan (legacy parity)
./bin/oikbas-finance scan --channels edgar --days 7 --output table

# Full multi-source scan with Squeeze Setup detection
./bin/oikbas-finance scan \
    --channels cowork-openinsider,cowork-finviz,yahoo \
    --days 30 --smallcap-only --output table

# Vault note generation (writes to oikbas-vault/030_Areas/034_Finance/)
./bin/oikbas-finance scan --channels edgar --days 7 --output vault
```

## Squeeze Setup pattern

The headline cross-channel pattern: a ticker that simultaneously satisfies

- **Cluster Buy** — 2+ distinct insiders bought (any source)
- **Smallcap** — market_cap < $1B (from `cowork-finviz`)
- **High Short Float** — short_pct_of_float ≥ 20% (from `cowork-finviz`)

Surfaces as `★★★★★+ Squeeze Setup` in table/json output. Without Cowork data
the analyzer gracefully degrades — no Squeeze entries, but everything else
works.

## Configuration

`~/.config/oikbas-finance/config.yaml` (all keys optional):

```yaml
user_agent: "oikbas miinh.anr@gmail.com"
edgar:
  rate_limit_delay: 0.12
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
```

Override the config path with `OIKBAS_FINANCE_CONFIG=/path/to/file.yaml`.

## Adding a new channel

1. Create `src/oikbas_finance/channels/<name>.py`
2. Subclass `Channel`, implement `can_handle`, `check`, `fetch`
3. Decorate with `@register("<name>")`
4. Add the import in `src/oikbas_finance/channels/__init__.py::_ensure_loaded()`
5. Run `./bin/oikbas-finance doctor` to verify

The analyzer's cross-matching helpers (`index_by_ticker`, `detect_squeeze_setup`,
`filter_smallcap`) work on any channel that emits the right `signal_type`.

## Migration from `insider_scan.py`

The legacy script still lives at
`oikbas-vault/090_System/093_Scripts/insider_scan.py` and runs in parallel
during a one-cycle validation period. Vault note output is byte-identical
between the two implementations (M2 regression verified).

After one weekly run with both producing matching notes, the legacy script
will be archived. The `/insider-scan` slash command already routes through
the new package.

## Verification

```bash
# Regression diff against legacy (proves byte-identical vault notes)
/root/miniconda/bin/python3 /tmp/m2_regression.py
```

## Known limitations

- EDGAR `parse_purchases` doesn't handle XML files with undefined HTML
  entities — same bug as legacy. Tracked as post-M5 hardening.
- Yahoo `v7/quote` and `v10/quoteSummary` are auth-walled now; only
  `v8/chart` works without crumbs. Market cap and short float must come
  from the `cowork-*` channels.
- `yfinance` library was evaluated and rejected — too brittle against
  Yahoo's anti-bot escalations.
