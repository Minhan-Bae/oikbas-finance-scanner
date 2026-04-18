"""analyzer.py 핵심 경로 유닛 테스트.

insider_scan.py:163-222 byte-identical parity가 이 코드의 주요 보증이므로,
회귀를 잡기 위한 최소 골든 케이스를 고정한다.
"""
from oikbas_finance.analyzer import (
    classify_signals,
    detect_squeeze_setup,
    extract_purchases,
    filter_smallcap,
    index_by_ticker,
)


def _make_signal(ticker, stype="insider_purchase", **raw):
    """Helper to build a Signal with raw dict."""
    base = {
        "ticker": ticker,
        "source": "edgar",
        "signal_type": stype,
        "timestamp": "2026-04-01",
        "value": float(raw.get("value", 0)),
        "confidence": 3,
        "raw": {
            "ticker": ticker,
            "insider": raw.get("insider", "Doe"),
            "value": float(raw.get("value", 0)),
            "role_priority": raw.get("role_priority", 1),
        },
        "metadata": raw.get("metadata", {}),
    }
    return base


def _purchase(ticker, insider="Doe", value=10_000, role_priority=1):
    return {
        "ticker": ticker,
        "insider": insider,
        "value": float(value),
        "role_priority": role_priority,
    }


# ─ extract_purchases ─────────────────────────────────────────────────


def test_extract_filters_by_signal_type():
    signals = [
        _make_signal("AAA", stype="insider_purchase", value=1000),
        _make_signal("BBB", stype="short_interest", value=0.5),
    ]
    purchases = extract_purchases(signals)
    assert len(purchases) == 1
    assert purchases[0]["ticker"] == "AAA"


def test_extract_requires_ticker_and_value_keys():
    # raw이 dict여도 ticker/value 없으면 제외
    bad = {
        "ticker": "XXX",
        "source": "edgar",
        "signal_type": "insider_purchase",
        "timestamp": "2026",
        "raw": {"only_ticker": "XXX"},
        "metadata": {},
    }
    assert extract_purchases([bad]) == []


# ─ classify_signals ──────────────────────────────────────────────────


def test_cluster_buy_two_distinct_insiders():
    purchases = [
        _purchase("AAA", insider="Doe", value=50_000),
        _purchase("AAA", insider="Smith", value=30_000),
    ]
    result = classify_signals(purchases)
    assert len(result["cluster"]) == 1
    assert result["cluster"][0]["rating"] == "★★★★★"
    assert result["cluster"][0]["insiders"] == 2
    assert result["cluster"][0]["total_value"] == 80_000


def test_large_purchase_single_large_value():
    purchases = [_purchase("BBB", value=150_000)]
    result = classify_signals(purchases)
    assert len(result["large"]) == 1
    assert result["large"][0]["rating"] == "★★★★"


def test_large_purchase_c_level_role():
    # $50K (<$100K 경계) but role_priority 4+ = CFO/CEO
    purchases = [_purchase("CCC", value=50_000, role_priority=4)]
    result = classify_signals(purchases)
    assert len(result["large"]) == 1
    assert "C-level" in result["large"][0]["reason"]


def test_consistent_buyer_same_insider_twice():
    purchases = [
        _purchase("DDD", insider="Doe", value=10_000),
        _purchase("DDD", insider="Doe", value=15_000),
    ]
    result = classify_signals(purchases)
    assert len(result["consistent"]) == 1
    assert result["consistent"][0]["rating"] == "★★★"


def test_other_single_small_purchase():
    purchases = [_purchase("EEE", value=5_000)]
    result = classify_signals(purchases)
    assert len(result["other"]) == 1
    assert result["other"][0]["rating"] == "★★"


def test_cluster_takes_precedence_over_large():
    # 2명 + 큰 금액 → cluster (large 아님)
    purchases = [
        _purchase("FFF", insider="A", value=200_000),
        _purchase("FFF", insider="B", value=50_000),
    ]
    result = classify_signals(purchases)
    assert len(result["cluster"]) == 1
    assert len(result["large"]) == 0


# ─ detect_squeeze_setup ──────────────────────────────────────────────


def _short_sig(ticker, mcap, short_pct):
    return {
        "ticker": ticker,
        "source": "cowork-finviz",
        "signal_type": "short_interest",
        "timestamp": "2026-04-01",
        "value": short_pct,
        "confidence": 3,
        "raw": {},
        "metadata": {"market_cap": mcap, "short_pct_of_float": short_pct},
    }


def test_squeeze_setup_all_conditions_met():
    classified = classify_signals([
        _purchase("SQZ", insider="A", value=50_000),
        _purchase("SQZ", insider="B", value=30_000),
    ])
    short_index = {"SQZ": _short_sig("SQZ", mcap=500_000_000, short_pct=0.30)}

    result = detect_squeeze_setup(classified, short_index)
    assert len(result) == 1
    assert result[0]["pattern"] == "Squeeze Setup"
    assert result[0]["rating"] == "★★★★★+"


def test_squeeze_excluded_when_not_smallcap():
    classified = classify_signals([
        _purchase("BIG", insider="A", value=50_000),
        _purchase("BIG", insider="B", value=30_000),
    ])
    short_index = {"BIG": _short_sig("BIG", mcap=10_000_000_000, short_pct=0.30)}

    assert detect_squeeze_setup(classified, short_index) == []


def test_squeeze_excluded_when_short_below_threshold():
    classified = classify_signals([
        _purchase("LSH", insider="A", value=50_000),
        _purchase("LSH", insider="B", value=30_000),
    ])
    short_index = {"LSH": _short_sig("LSH", mcap=500_000_000, short_pct=0.10)}

    assert detect_squeeze_setup(classified, short_index) == []


def test_squeeze_empty_short_index_returns_empty():
    """Cowork 데이터 없으면 graceful degrade."""
    classified = {"cluster": [{"ticker": "X", "total_value": 1, "insiders": 2, "transactions": []}]}
    assert detect_squeeze_setup(classified, short_index={}) == []


def test_squeeze_only_cluster_pattern():
    """Large/Consistent는 cluster가 아니므로 squeeze 후보 아님."""
    purchases = [_purchase("LARGE", value=200_000)]
    classified = classify_signals(purchases)
    short_index = {"LARGE": _short_sig("LARGE", mcap=500_000_000, short_pct=0.30)}

    assert detect_squeeze_setup(classified, short_index) == []


# ─ index_by_ticker ───────────────────────────────────────────────────


def test_index_by_ticker_filters_by_type():
    signals = [
        _short_sig("A", mcap=1e9, short_pct=0.3),
        _short_sig("B", mcap=2e9, short_pct=0.1),
        _make_signal("A", stype="insider_purchase", value=100),
    ]
    index = index_by_ticker(signals, "short_interest")
    assert set(index.keys()) == {"A", "B"}


def test_index_by_ticker_last_wins_on_duplicate():
    # 같은 ticker + 같은 type의 시그널이 두 번 들어오면 마지막 값으로 덮어쓴다.
    sig1 = _short_sig("DUP", mcap=1e9, short_pct=0.1)
    sig2 = _short_sig("DUP", mcap=5e8, short_pct=0.5)
    index = index_by_ticker([sig1, sig2], "short_interest")
    assert index["DUP"]["metadata"]["short_pct_of_float"] == 0.5


# ─ filter_smallcap ───────────────────────────────────────────────────


def test_filter_smallcap_removes_large_caps():
    classified = {
        "cluster": [{"ticker": "BIG"}, {"ticker": "SMALL"}],
        "large": [{"ticker": "BIG"}],
        "consistent": [],
        "other": [],
    }
    short_index = {
        "BIG": _short_sig("BIG", mcap=5e9, short_pct=0.1),
        "SMALL": _short_sig("SMALL", mcap=5e8, short_pct=0.3),
    }
    out = filter_smallcap(classified, short_index)
    assert [x["ticker"] for x in out["cluster"]] == ["SMALL"]
    assert out["large"] == []


def test_filter_smallcap_keeps_tickers_without_mcap():
    classified = {"cluster": [{"ticker": "UNK"}], "large": [], "consistent": [], "other": []}
    short_index = {}  # 데이터 없음
    out = filter_smallcap(classified, short_index)
    # short_index 비어있으면 classified 그대로
    assert out == classified


def test_filter_smallcap_keeps_ticker_with_mcap_none():
    classified = {"cluster": [{"ticker": "X"}], "large": [], "consistent": [], "other": []}
    sig = _short_sig("X", mcap=None, short_pct=0.3)
    out = filter_smallcap(classified, {"X": sig})
    assert [x["ticker"] for x in out["cluster"]] == ["X"]
