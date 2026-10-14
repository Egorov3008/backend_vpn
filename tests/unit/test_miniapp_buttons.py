"""Кнопки уведомлений: web_app при заданном MINIAPP_URL, иначе callback бота."""

import pytest

from config import settings
from services.notifications import miniapp_buttons
from services.notifications.funnels.key_expiry_24h import KeyExpiryFunnel24h
from shared.miniapp_url import miniapp_link, normalize_miniapp_url

URL = "https://app.example.com/"


def test_renew_button_opens_pay_screen(monkeypatch):
    monkeypatch.setattr(settings, "miniapp_url", URL)
    button = miniapp_buttons.renew_key_button("user@vpn.ru")
    assert button == {
        "text": "🔄 Продлить ключ",
        "web_app": {"url": "https://app.example.com/?screen=pay&renew=user%40vpn.ru"},
    }


def test_renew_button_falls_back_to_callback(monkeypatch):
    monkeypatch.setattr(settings, "miniapp_url", "")
    assert miniapp_buttons.renew_key_button("user@vpn.ru")["callback_data"] == "renew_key|user@vpn.ru"


def test_trial_button(monkeypatch):
    monkeypatch.setattr(settings, "miniapp_url", URL)
    assert miniapp_buttons.trial_button("x")["web_app"]["url"] == "https://app.example.com/?screen=trial"
    monkeypatch.setattr(settings, "miniapp_url", "")
    assert miniapp_buttons.trial_button("x")["callback_data"] == "activate_stock"


def test_funnel_keyboard_uses_renew_button(monkeypatch):
    monkeypatch.setattr(settings, "miniapp_url", URL)

    class _Key:
        email = "k@vpn.ru"

    keyboard = KeyExpiryFunnel24h._build_keyboard(_Key())
    assert "web_app" in keyboard["inline_keyboard"][0][0]


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", ""),
        ("  ", ""),
        ("https://app.example.com", "https://app.example.com/"),
        ("https://app.example.com/miniapp/", "https://app.example.com/miniapp/"),
    ],
)
def test_normalize_valid(raw, expected):
    assert normalize_miniapp_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["http://app.example.com", "app.example.com", "https://", "https://u:p@app.example.com", "https://app.example.com/#x"],
)
def test_normalize_rejects(raw):
    with pytest.raises(ValueError):
        normalize_miniapp_url(raw)


def test_link_appends_to_existing_query():
    assert miniapp_link("https://a.ru/?v=2", screen="trial") == "https://a.ru/?v=2&screen=trial"
