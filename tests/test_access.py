"""Тесты гейта подписки / пробных использований."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.access import check_processing_access, has_active_subscription


def _user(**kwargs):
    base = {
        "id": 1,
        "telegram_id": 100,
        "subscription_until": None,
        "free_uses_left": None,
        "promo_code": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _settings(**kwargs):
    base = {
        "admin_ids": set(),
        "free_trial_uses": 3,
        "subscription_price_rub": 299,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_active_subscription(monkeypatch):
    monkeypatch.setattr(
        "app.services.access.get_settings",
        lambda: _settings(free_trial_uses=3),
    )
    until = datetime.now(timezone.utc) + timedelta(days=10)
    user = _user(subscription_until=until)
    assert has_active_subscription(user)
    result = check_processing_access(user, 100)
    assert result.allowed
    assert result.reason == "subscription"
    assert not result.consume_trial


def test_hard_paywall_zero_trial(monkeypatch):
    monkeypatch.setattr(
        "app.services.access.get_settings",
        lambda: _settings(free_trial_uses=0),
    )
    user = _user(free_uses_left=None)
    result = check_processing_access(user, 100)
    assert not result.allowed
    assert result.reason == "paywall"


def test_soft_trial_consumes(monkeypatch):
    monkeypatch.setattr(
        "app.services.access.get_settings",
        lambda: _settings(free_trial_uses=3),
    )
    user = _user(free_uses_left=None)
    result = check_processing_access(user, 100)
    assert result.allowed
    assert result.consume_trial
    assert user.free_uses_left == 3


def test_admin_bypass(monkeypatch):
    monkeypatch.setattr(
        "app.services.access.get_settings",
        lambda: _settings(admin_ids={42}, free_trial_uses=0),
    )
    user = _user(telegram_id=42)
    result = check_processing_access(user, 42)
    assert result.allowed
    assert result.reason == "admin"
