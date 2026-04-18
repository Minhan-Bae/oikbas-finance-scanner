"""cowork_import.py 숫자/통화 파서 유닛 테스트.

Cowork in Chrome이 export하는 display string 형식이 다양해서 (`$1.23`, `+123%`,
`794.46M`, `7,842,032`), 이 파서들이 조용히 None을 내면 analyzer에 구멍이 생긴다.
"""
from oikbas_finance.channels.cowork_import import (
    _parse_int,
    _parse_money,
    _parse_pct,
    _role_priority_from_title,
)


# ─ _parse_money ──────────────────────────────────────────────────────


def test_parse_money_plain_float():
    assert _parse_money("123.45") == 123.45


def test_parse_money_with_dollar_and_comma():
    assert _parse_money("$113,976") == 113976.0


def test_parse_money_with_plus_sign():
    assert _parse_money("+$113,976") == 113976.0


def test_parse_money_suffix_M():
    assert _parse_money("794.46M") == 794_460_000.0


def test_parse_money_suffix_B():
    assert _parse_money("2.5B") == 2_500_000_000.0


def test_parse_money_suffix_K():
    assert _parse_money("100K") == 100_000.0


def test_parse_money_suffix_T():
    assert _parse_money("1.5T") == 1_500_000_000_000.0


def test_parse_money_numeric_input():
    assert _parse_money(1234) == 1234.0
    assert _parse_money(1234.5) == 1234.5


def test_parse_money_none():
    assert _parse_money(None) is None


def test_parse_money_empty_string():
    assert _parse_money("") is None


def test_parse_money_invalid():
    assert _parse_money("not-a-number") is None


# ─ _parse_int ────────────────────────────────────────────────────────


def test_parse_int_plain():
    assert _parse_int("1234") == 1234


def test_parse_int_with_comma():
    assert _parse_int("7,842,032") == 7_842_032


def test_parse_int_with_plus():
    assert _parse_int("+7,842,032") == 7_842_032


def test_parse_int_from_float_string():
    # "17862032.0" 같은 케이스도 수용
    assert _parse_int("17862032.0") == 17_862_032


def test_parse_int_none():
    assert _parse_int(None) is None


def test_parse_int_invalid():
    assert _parse_int("abc") is None


# ─ _parse_pct ────────────────────────────────────────────────────────


def test_parse_pct_percent_string():
    assert _parse_pct("52.90%") == 0.529


def test_parse_pct_with_plus():
    assert _parse_pct("+78%") == 0.78


def test_parse_pct_fraction_passthrough():
    # 이미 소수 0.529 형태면 그대로
    assert _parse_pct(0.529) == 0.529


def test_parse_pct_small_integer_preserved():
    # 1.5 이하는 이미 fraction으로 간주
    assert _parse_pct(0.5) == 0.5
    assert _parse_pct(1.0) == 1.0


def test_parse_pct_large_integer_divided():
    # 2 이상은 percent로 간주하고 100으로 나눔
    assert _parse_pct(78) == 0.78


def test_parse_pct_none():
    assert _parse_pct(None) is None


def test_parse_pct_empty():
    assert _parse_pct("") is None


# ─ _role_priority_from_title ────────────────────────────────────────


def test_role_priority_ceo():
    assert _role_priority_from_title("CEO") == 5
    assert _role_priority_from_title("Chief Executive Officer") == 5


def test_role_priority_cfo():
    assert _role_priority_from_title("CFO") == 4
    assert _role_priority_from_title("Chief Financial Officer") == 4


def test_role_priority_coo_president_chairman():
    assert _role_priority_from_title("COO") == 3
    assert _role_priority_from_title("President") == 3
    assert _role_priority_from_title("Chairman") == 3


def test_role_priority_director():
    assert _role_priority_from_title("Director") == 2


def test_role_priority_10pct_owner():
    assert _role_priority_from_title("10% Owner") == 2


def test_role_priority_other():
    assert _role_priority_from_title("Chief Technology Officer") == 1
    assert _role_priority_from_title("") == 1
    assert _role_priority_from_title(None) == 1
