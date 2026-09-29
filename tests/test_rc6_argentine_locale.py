import pytest

from rc6_argentine_locale import format_es_ar_number, parse_es_ar_number


def test_argentine_thousands_and_decimal_separators_are_not_ambiguous():
    assert parse_es_ar_number("1.000") == 1000
    assert parse_es_ar_number("1.000,50") == 1000.50
    assert format_es_ar_number("1000.50") == "1.000,50"


def test_invalid_locale_number_fails_closed():
    with pytest.raises(ValueError):
        parse_es_ar_number("")
