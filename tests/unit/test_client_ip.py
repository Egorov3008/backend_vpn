"""extract_client_ip: заголовкам прокси верим только от своих прокси."""
from types import SimpleNamespace

import pytest

from app.client_ip import extract_client_ip


def make_request(peer, headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers={k.lower(): v for k, v in (headers or {}).items()},
    )


def test_direct_client_headers_ignored():
    # Клиент ходит напрямую и подставляет адрес YooKassa — не верим.
    req = make_request("203.0.113.7", {"X-Forwarded-For": "185.71.76.1", "X-Real-IP": "185.71.76.1"})
    assert extract_client_ip(req) == "203.0.113.7"


def test_spoofed_left_entry_ignored_behind_proxy():
    # nginx с proxy_add_x_forwarded_for дописал реальный адрес справа.
    req = make_request("172.18.0.5", {"X-Forwarded-For": "185.71.76.1, 203.0.113.7"})
    assert extract_client_ip(req) == "203.0.113.7"


def test_two_proxy_hops():
    # Клиент → edge nginx → web nginx (172.x) → backend.
    req = make_request("172.18.0.5", {"X-Forwarded-For": "1.2.3.4, 203.0.113.7, 172.18.0.9"})
    assert extract_client_ip(req) == "203.0.113.7"


def test_yookassa_through_proxy():
    req = make_request("172.18.0.5", {"X-Forwarded-For": "185.71.76.3"})
    assert extract_client_ip(req) == "185.71.76.3"


def test_x_real_ip_from_trusted_proxy():
    req = make_request("127.0.0.1", {"X-Real-IP": "203.0.113.7"})
    assert extract_client_ip(req) == "203.0.113.7"


def test_all_trusted_chain_returns_leftmost():
    req = make_request("172.18.0.5", {"X-Forwarded-For": "10.0.0.2, 172.18.0.9"})
    assert extract_client_ip(req) == "10.0.0.2"


def test_no_headers_returns_peer():
    assert extract_client_ip(make_request("172.18.0.5")) == "172.18.0.5"


def test_no_client():
    assert extract_client_ip(make_request(None)) is None


def test_invalid_cidr_rejected_at_startup():
    from config import Settings

    with pytest.raises(ValueError):
        Settings(TRUSTED_PROXY_CIDRS="10.0.0.0/8,not-a-cidr")
