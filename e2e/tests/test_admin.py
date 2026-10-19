"""Веб-админка (/admin на домене ADMIN_HOST): вход по initData админ-бота, работа с ключами."""
import re

import httpx
from playwright.sync_api import expect

from stack import PAID_INBOUNDS


def _register_in_bot(fakes, tg_id: int) -> None:
    after = fakes.user_says(tg_id, "/start")
    fakes.wait_text(tg_id, "Добро пожаловать", after=after)


def test_admin_issues_and_deletes_key(stack, telegram_page, sign, fakes, db, admin_tg_id, new_tg_id):
    _register_in_bot(fakes, new_tg_id)

    page = telegram_page(sign(admin_tg_id, bot="admin"))
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_role("heading", name="Сводка")).to_be_visible()

    page.get_by_role("navigation").get_by_role("button", name="Люди").click()
    page.get_by_label("Поиск пользователя").fill(str(new_tg_id))
    page.get_by_role("button", name=re.compile(str(new_tg_id))).click()
    expect(page.get_by_text("Ключей нет")).to_be_visible()

    page.get_by_role("combobox").first.select_option(label="Стандарт (199 ₽)")
    page.get_by_role("button", name="Выдать ключ").click()
    expect(page.get_by_text(re.compile(r"Ключ \S+ на \d+ дн\."))).to_be_visible()

    keys = db.keys_of(new_tg_id)
    assert len(keys) == 1, keys
    email = keys[0]["email"]
    panel = fakes.panel_client(email)
    assert panel is not None and panel["inboundIds"] == PAID_INBOUNDS, panel
    assert panel["client"]["expiryTime"] == keys[0]["expiry_time"]

    # Удаление ключа из карточки пользователя (подтверждение — диалог Telegram)
    page.get_by_role("button", name=re.compile(email)).first.click()
    page.get_by_role("button", name="Удалить ключ").click()
    expect(page.get_by_text("Ключей нет")).to_be_visible()
    assert db.keys_of(new_tg_id) == []
    assert fakes.panel_client(email) is None

    actions = db.fetch("SELECT action, target FROM admin_audit_log WHERE admin_tg_id=$1", admin_tg_id)
    assert any(email in (a["target"] or "") for a in actions), actions


def test_admin_login_refused_for_non_admin_and_foreign_bot(stack, telegram_page, sign, new_tg_id, admin_tg_id):
    page = telegram_page(sign(new_tg_id, bot="admin"))
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_role("alert")).to_have_text("Этот Telegram-аккаунт не админ.")

    # initData основного бота (не админ-бота) не подходит даже админу
    page = telegram_page(sign(admin_tg_id, bot="main"))
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_role("alert")).to_contain_text("Telegram не подтвердил вход")

    # Протухший initData
    page = telegram_page(sign(admin_tg_id, bot="admin", auth_date=1_700_000_000))
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_role("alert")).to_contain_text("Telegram не подтвердил вход")


def test_admin_outside_telegram(stack, page):
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_text("Админка открывается только из админ-бота")).to_be_visible()


def test_admin_api_needs_session_not_bot_secrets(stack, sign, admin_tg_id):
    """Через nginx админки ключ бота не работает: X-API-Key и X-Admin-Tg-Id вырезаются."""
    s = stack.secrets
    with httpx.Client(
        base_url=f"http://127.0.0.1:{stack.cfg.web_port}",
        headers={"Host": stack.cfg.admin_host},
        trust_env=False,
        timeout=30,
    ) as web:
        headers = {"X-API-Key": s.admin_api_key, "X-Admin-Tg-Id": str(admin_tg_id), "X-Bot-Secret": s.bot_secret}
        assert web.get("/api/v1/admin/users", headers=headers).status_code == 401
        assert web.delete("/api/v1/admin/users/1", headers=headers).status_code == 401

        session = web.post("/api/v1/admin/session", json={"init_data": sign(admin_tg_id, bot="admin")})
        assert session.status_code == 200, session.text
        token = session.json()["access_token"]
        users = web.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert users.status_code == 200
        # Админка не видит API кабинета и сервисные маршруты
        assert web.get("/api/v1/web/me").status_code == 404
        assert web.post("/api/v1/auth/web-login/x/confirm").status_code == 404
        assert web.get("/").headers.get("location") == "/admin"
        assert web.get("/index.html").status_code == 404

    # Сессия админки не подходит для кабинета и наоборот
    with httpx.Client(base_url=stack.cfg.backend_url, trust_env=False, timeout=30) as api:
        r = api.get("/api/v1/web/me", headers={"Cookie": f"web_session={token}"})
        assert r.status_code == 401
