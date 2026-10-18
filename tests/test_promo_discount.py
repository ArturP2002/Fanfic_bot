"""Скидки промокодов."""

from decimal import Decimal

import pytest

from app.services.promo import parse_discount_percent, price_with_discount


def test_parse_discount():
    assert parse_discount_percent("10") == 10
    assert parse_discount_percent("10%") == 10
    assert parse_discount_percent("0") == 0
    assert parse_discount_percent("100") == 100
    with pytest.raises(ValueError):
        parse_discount_percent("101")
    with pytest.raises(ValueError):
        parse_discount_percent("abc")


def test_price_with_discount():
    assert price_with_discount(299, 0) == Decimal("299.00")
    assert price_with_discount(299, 10) == Decimal("269.10")
    assert price_with_discount(300, 50) == Decimal("150.00")
    assert price_with_discount(299, 100) == Decimal("0.00")
