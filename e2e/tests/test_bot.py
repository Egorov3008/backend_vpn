"""Telegram-бот (plaltform-bot) против фейкового Bot API: онбординг, лендинг, вход на сайт."""
import time

from helpers import text_of
from stack import ADMIN_TG_ID, PAID_INBOUNDS

WEB_HEADERS = {"X-Requested-With": "web"}


def _guest_key(stack, context) -> dict:
    resp = context.request.post(f"{stack.cfg.web_url}/api/v1/web/anonymous/key", headers=WEB_HEADERS)
    assert resp.ok, resp.text()
    return resp.json()


def _start_web_login(stack, context) -> str:
    resp = context.request.post(f"{stack.cfg.web_url}/api/v1/web/auth/bot-login", headers=WEB_HEADERS)
    assert resp.ok, resp.text()
    return resp.json()["deep_link"].split("start=", 1)[1]


def _poll(stack, context) -> dict:
    resp = context.request.post(f"{stack.cfg.web_url}/api/v1/web/auth/bot-login/complete", headers=WEB_HEADERS)
    assert resp.ok, resp.text()
    return resp.json()


def test_onboarding_activates_trial_key(fakes, db, new_tg_id):
    after = fakes.user_says(new_tg_id, "/start")
    welcome = fakes.wait_text(new_tg_id, "Добро пожаловать", after=after)
    fakes.wait_text(ADMIN_TG_ID, f"<code>{new_tg_id}</code>")  # админам — о новой регистрации

    after = fakes.press(new_tg_id, welcome, "Активировать пробный период")
    devices = fakes.wait_text(new_tg_id, "Выберете устройство", after=after)
    after = fakes.press(new_tg_id, devices, "ANDROID")
    download = fakes.wait_text(new_tg_id, "Скачайте приложение", after=after)
    after = fakes.press(new_tg_id, download, "Следующий шаг")
    done = fakes.wait_text(new_tg_id, "пробный ключ активирован", after=after)

    keys = db.keys_of(new_tg_id)
    assert len(keys) == 1
    buttons = [b for row in done["message"]["reply_markup"]["inline_keyboard"] for b in row]
    assert any(b.get("url") == keys[0]["key"] for b in buttons), buttons
    panel = fakes.panel_client(keys[0]["email"])
    assert panel["inboundIds"] == PAID_INBOUNDS
    assert panel["client"]["expiryTime"] == keys[0]["expiry_time"]

    after = fakes.user_says(new_tg_id, "/profile")
    profile = fakes.wait_text(new_tg_id, "Личный кабинет", after=after)
    assert "Количество ключей:</b> 1" in text_of(profile)


def test_landing_deep_link_claims_guest_key(stack, context, fakes, db, new_tg_id):
    guest = _guest_key(stack, context)
    assert guest["deep_link_bot"] == f"https://t.me/e2e_vpn_bot?start=landing_{guest['landing_uid']}"
    email = guest["key_value"].rsplit("/", 1)[1]

    after = fakes.user_says(new_tg_id, f"/start landing_{guest['landing_uid']}")
    fakes.wait_bot_message(new_tg_id, lambda m: True, after=after)
    for _ in range(50):
        keys = db.keys_of(new_tg_id)
        if keys:
            break
        time.sleep(0.2)
    assert [k["email"] for k in keys] == [email]
    days = (keys[0]["expiry_time"] / 1000 - time.time()) / 86400
    assert 6.9 < days <= 7.01, days
    assert fakes.panel_client(email)["inboundIds"] == PAID_INBOUNDS

    # Тот же ключ другому пользователю не достаётся
    other = new_tg_id + 500_000
    after = fakes.user_says(other, f"/start landing_{guest['landing_uid']}")
    fakes.wait_bot_message(other, lambda m: True, after=after)
    time.sleep(1)
    assert db.keys_of(other) == []
    assert db.keys_of(new_tg_id)[0]["email"] == email


def test_web_login_cancel_and_broken_link(stack, context, fakes, new_tg_id):
    payload = _start_web_login(stack, context)
    after = fakes.user_says(new_tg_id, f"/start {payload}")
    ask = fakes.wait_text(new_tg_id, "Вход на сайт", after=after)
    after = fakes.press(new_tg_id, ask, "Отмена")
    fakes.wait_text(new_tg_id, "Вход на сайт отменён.", after=after)
    assert _poll(stack, context)["status"] == "pending"

    after = fakes.user_says(new_tg_id, f"/start {payload[:-3]}")
    fakes.wait_text(new_tg_id, "Ссылка для входа на сайт повреждена", after=after)


def test_web_login_link_works_once_and_only_for_its_browser(stack, browser, context, fakes, new_tg_id):
    payload = _start_web_login(stack, context)
    after = fakes.user_says(new_tg_id, f"/start {payload}")
    ask = fakes.wait_text(new_tg_id, "Вход на сайт", after=after)

    after = fakes.press(new_tg_id, ask, "Это я, войти")
    fakes.wait_text(new_tg_id, "Вход подтверждён", after=after)

    # Ссылка одноразовая: другой аккаунт по ней уже не войдёт
    intruder = new_tg_id + 500_000
    after = fakes.user_says(intruder, f"/start {payload}")
    ask2 = fakes.wait_text(intruder, "Вход на сайт", after=after)
    after = fakes.press(intruder, ask2, "Это я, войти")
    fakes.wait_text(intruder, "Ссылка устарела", after=after)

    # Сессию забирает только браузер с секретом опроса, и только один раз
    other_ctx = browser.new_context(extra_http_headers={"X-Forwarded-For": "203.0.113.77"})
    try:
        assert _poll(stack, other_ctx)["status"] == "expired"
    finally:
        other_ctx.close()
    first = _poll(stack, context)
    assert first == {"status": "confirmed", "tg_id": new_tg_id}
    assert _poll(stack, context)["status"] == "expired"
    me = context.request.get(f"{stack.cfg.web_url}/api/v1/web/me")
    assert me.ok and me.json()["tg_id"] == new_tg_id


def test_web_login_existing_user_keeps_guest_key_until_day_end(stack, context, fakes, db, new_tg_id):
    after = fakes.user_says(new_tg_id, "/start")
    fakes.wait_text(new_tg_id, "Добро пожаловать", after=after)

    guest = _guest_key(stack, context)
    payload = _start_web_login(stack, context)
    after = fakes.user_says(new_tg_id, f"/start {payload}")
    ask = fakes.wait_text(new_tg_id, "Вход на сайт", after=after)
    after = fakes.press(new_tg_id, ask, "Это я, войти")
    done = fakes.wait_text(new_tg_id, "Вход подтверждён", after=after)
    assert "Временный ключ с сайта работает до конца суток" in text_of(done)
    assert _poll(stack, context)["status"] == "confirmed"
    # Существующему пользователю ключ не переносится: он доживает свои 24 часа
    state = context.request.get(f"{stack.cfg.web_url}/api/v1/web/anonymous/state").json()
    assert state["state"] == "active" and state["already_registered"] is True, state
    email = guest["key_value"].rsplit("/", 1)[1]
    assert db.keys_of(new_tg_id) == []
    assert db.fetch("SELECT converted_tg_id FROM keys WHERE email=$1", email)[0]["converted_tg_id"] == new_tg_id
