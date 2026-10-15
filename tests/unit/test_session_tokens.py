import base64
import json

import pytest

from app.core.miniapp_session import miniapp_sessions, web_sessions
from app.core.session_tokens import SessionTokenError, SessionTokenService


def _service(label=b"t-v1", secret="s3cret-value-for-tests", ttl=60):
    return SessionTokenService(label=label, secret=lambda: secret, default_ttl=lambda: ttl)


def test_roundtrip():
    svc = _service()
    token, ttl = svc.issue(42)
    assert ttl == 60
    assert svc.verify(token) == 42


def test_token_of_other_label_rejected():
    token, _ = _service(label=b"a").issue(42)
    with pytest.raises(SessionTokenError):
        _service(label=b"b").verify(token)


def test_miniapp_token_not_accepted_as_web_session_and_back():
    mini, _ = miniapp_sessions.issue(42)
    web, _ = web_sessions.issue(42)
    with pytest.raises(SessionTokenError):
        web_sessions.verify(mini)
    with pytest.raises(SessionTokenError):
        miniapp_sessions.verify(web)


def test_other_secret_rejected():
    token, _ = _service(secret="one-secret-value-xx").issue(42)
    with pytest.raises(SessionTokenError):
        _service(secret="two-secret-value-xx").verify(token)


def test_expired_rejected():
    svc = _service()
    token, _ = svc.issue(42, ttl_seconds=-1)
    with pytest.raises(SessionTokenError, match="expired"):
        svc.verify(token)


def test_forged_payload_rejected():
    svc = _service()
    token, _ = svc.issue(42)
    _, sig = token.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({"tg_id": 1, "exp": 9999999999}).encode()).rstrip(b"=").decode()
    with pytest.raises(SessionTokenError):
        svc.verify(f"{forged}.{sig}")


def test_bool_tg_id_rejected():
    svc = _service()
    body = base64.urlsafe_b64encode(json.dumps({"tg_id": True, "exp": 9999999999}).encode()).rstrip(b"=").decode()
    with pytest.raises(SessionTokenError):
        svc.verify(f"{body}.{svc._sign(body)}")


@pytest.mark.parametrize("garbage", ["", "abc", ".", "a.", ".b", "a.b.c"])
def test_garbage_rejected(garbage):
    with pytest.raises(SessionTokenError):
        _service().verify(garbage)
