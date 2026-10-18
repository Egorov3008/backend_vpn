"""Границы доступа: что видно через nginx кабинета, какие секреты нужны backend'у."""
import itertools

import httpx
import pytest

_ips = (f"192.0.2.{i}" for i in itertools.count(1))  # TEST-NET-1


@pytest.fixture
def cabinet(stack):
    with httpx.Client(
        base_url=f"http://127.0.0.1:{stack.cfg.web_port}",
        headers={"Host": "localhost", "X-Forwarded-For": next(_ips)},
        trust_env=False,
        timeout=30,
    ) as c:
        yield c


@pytest.fixture
def raw_backend(stack):
    with httpx.Client(base_url=stack.cfg.backend_url, trust_env=False, timeout=30) as c:
        yield c


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/admin.html"),
        ("GET", "/api/v1/admin/users"),
        ("POST", "/api/v1/admin/session"),
        ("POST", "/api/v1/auth/web-login/x/confirm"),
        ("POST", "/api/v1/landing/claim/x"),
        ("GET", "/api/v1/keys/"),
        ("GET", "/api/v1/users/1"),
        ("POST", "/api/v1/payments/webhook"),
        ("GET", "/api/v1/miniapp/me"),
        ("GET", "/docs"),
    ],
)
def test_cabinet_host_exposes_only_web_api(cabinet, method, path):
    resp = cabinet.request(method, path)
    if path == "/docs":
        # SPA-фолбэк отдаёт index.html, но не Swagger бэкенда
        assert "swagger" not in resp.text.lower()
    else:
        assert resp.status_code == 404, (path, resp.status_code, resp.text[:200])


def test_cabinet_security_headers(cabinet, stack):
    resp = cabinet.get("/")
    csp = resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
    admin = cabinet.get("/admin", headers={"Host": stack.cfg.admin_host})
    assert "frame-ancestors" in admin.headers["content-security-policy"]
    assert "https://web.telegram.org" in admin.headers["content-security-policy"]


def test_web_api_needs_session_and_csrf_header(cabinet, stack):
    s = stack.secrets
    # Секреты бота из браузера ничего не дают: nginx их вырезает, а /web их и не принимает
    r = cabinet.get("/api/v1/web/me", headers={"X-Bot-Secret": s.bot_secret, "X-API-Key": s.admin_api_key})
    assert r.status_code == 401
    # Изменяющие запросы без X-Requested-With: web отклоняются (CSRF)
    r = cabinet.post("/api/v1/web/anonymous/key")
    assert r.status_code == 403
    r = cabinet.post("/api/v1/web/auth/bot-login")
    assert r.status_code == 403
    # Поддельная cookie сессии
    r = cabinet.get("/api/v1/web/me", headers={"Cookie": "web_session=1.2.3"})
    assert r.status_code == 401


def test_miniapp_token_is_not_a_web_session(stack, raw_backend, sign, new_tg_id):
    auth = raw_backend.post("/api/v1/miniapp/auth", json={"init_data": sign(new_tg_id)})
    assert auth.status_code == 200, auth.text
    token = auth.json()["access_token"]
    assert raw_backend.get("/api/v1/miniapp/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert raw_backend.get("/api/v1/web/me", headers={"Cookie": f"web_session={token}"}).status_code == 401
    assert raw_backend.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_bot_only_routes_need_internal_secret(stack, raw_backend):
    s = stack.secrets
    bot = {"X-Bot-Secret": s.bot_secret}
    body = {"tg_id": 1}
    assert raw_backend.post("/api/v1/landing/claim/abc", json=body, headers=bot).status_code == 401
    assert raw_backend.post("/api/v1/landing/mark-converted/abc", json=body, headers=bot).status_code == 401
    assert raw_backend.post("/api/v1/auth/web-login/" + "a" * 32 + "/confirm", json=body, headers=bot).status_code == 401
    both = {**bot, "X-Landing-Internal-Secret": s.landing_internal}
    assert raw_backend.post("/api/v1/landing/claim/abc", json=body, headers=both).status_code == 404
    r = raw_backend.post("/api/v1/auth/web-login/" + "a" * 32 + "/confirm", json=body, headers=both)
    assert r.status_code == 200 and r.json()["status"] == "expired"
    # Внутренние маршруты без секрета бота закрыты
    assert raw_backend.get("/api/v1/users/1").status_code in (401, 403)
    assert raw_backend.get("/api/v1/keys/", params={"tg_id": 1}).status_code in (401, 403)


def test_destructive_admin_routes_refuse_bot_secret(stack, raw_backend, admin_tg_id):
    s = stack.secrets
    r = raw_backend.delete("/api/v1/admin/users/1", headers={"X-Bot-Secret": s.bot_secret})
    assert r.status_code in (401, 403)
    r = raw_backend.delete("/api/v1/admin/users/1", headers={"X-API-Key": "wrong", "X-Admin-Tg-Id": str(admin_tg_id)})
    assert r.status_code == 401
    # Читающие маршруты с секретом бота работают
    r = raw_backend.get("/api/v1/admin/users", headers={"X-Bot-Secret": s.bot_secret})
    assert r.status_code == 200


def test_guest_key_rate_limit_is_per_client_ip(stack):
    """Не больше 3 гостевых ключей в час с одного адреса; другой адрес не задет."""
    ip = next(_ips)

    def guest_key(client_ip: str) -> int:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{stack.cfg.web_port}",
            headers={"Host": "localhost", "X-Forwarded-For": client_ip, "X-Requested-With": "web"},
            trust_env=False,
            timeout=30,
        ) as c:  # новый клиент — новый «браузер» без cookie
            return c.post("/api/v1/web/anonymous/key").status_code

    assert [guest_key(ip) for _ in range(4)] == [200, 200, 200, 429]
    assert guest_key(next(_ips)) == 200
