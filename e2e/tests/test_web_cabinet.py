"""Веб-кабинет (platform-web за nginx): гостевой ключ, вход через бота, триал, оплата."""
import re
import time

from playwright.sync_api import expect

from stack import LANDING_INBOUND, PAID_INBOUNDS


def test_guest_key_then_login_via_bot_attaches_key(stack, page, fakes, db, web_login, new_tg_id):
    page.goto(stack.cfg.web_url)
    page.get_by_role("button", name="Получить ключ на 24 часа").click()
    expect(page.get_by_role("heading", name="Ваш ключ на 24 часа")).to_be_visible()
    key_url = page.locator("code.link-box").inner_text()
    email = key_url.rsplit("/", 1)[1]
    assert key_url == f"{stack.cfg.fakes_url}/sub/{email}"

    panel = fakes.panel_client(email)
    assert panel and panel["inboundIds"] == [LANDING_INBOUND], panel

    # Второй гостевой ключ тому же браузеру не выдаётся
    page.reload()
    expect(page.get_by_role("heading", name="Ваш ключ на 24 часа")).to_be_visible()
    expect(page.get_by_role("button", name="Получить ключ на 24 часа")).to_have_count(0)

    expect(page.get_by_text("ключ с этого сайта привяжется к аккаунту")).to_be_visible()
    confirmed = web_login(page, new_tg_id, open_site=False)
    assert "Ключ с сайта привязан" in confirmed, confirmed

    # Кабинет показывает тот же ключ, теперь он принадлежит пользователю
    expect(page.get_by_text(email)).to_be_visible()
    keys = db.keys_of(new_tg_id)
    assert [k["email"] for k in keys] == [email]
    panel = fakes.panel_client(email)
    days = (panel["client"]["expiryTime"] / 1000 - time.time()) / 86400
    assert 6.9 < days <= 7.01, days
    assert panel["inboundIds"] == PAID_INBOUNDS, panel
    assert panel["client"]["expiryTime"] == keys[0]["expiry_time"]


def test_guest_key_expires_in_24h_in_panel(stack, page, fakes, db):
    """Срок гостевого ключа в панели (она и отключает VPN) — 24 часа, как в БД."""
    page.goto(stack.cfg.web_url)
    page.get_by_role("button", name="Получить ключ на 24 часа").click()
    email = page.locator("code.link-box").inner_text().rsplit("/", 1)[1]
    db_expiry = db.fetch("SELECT expiry_time FROM keys WHERE email=$1", email)[0]["expiry_time"]
    panel_expiry = fakes.panel_client(email)["client"]["expiryTime"]
    hours = (panel_expiry / 1000 - time.time()) / 3600
    assert 23 < hours <= 24, f"в панели ключ на {hours:.0f} ч"
    assert abs(panel_expiry - db_expiry) < 60_000


def test_login_without_guest_key_and_trial(stack, page, fakes, db, web_login, new_tg_id):
    confirmed = web_login(page, new_tg_id)
    assert "Ключ с сайта" not in confirmed
    expect(page.get_by_role("heading", name="Защиты пока нет")).to_be_visible()

    page.get_by_role("button", name="Попробовать бесплатно").click()
    expect(page.get_by_text("Пробный ключ создан.")).to_be_visible()
    keys = db.keys_of(new_tg_id)
    assert len(keys) == 1
    expect(page.get_by_text(keys[0]["email"])).to_be_visible()
    assert db.fetch("SELECT trial FROM users WHERE tg_id=$1", new_tg_id)[0]["trial"] == 1
    assert fakes.panel_client(keys[0]["email"]) is not None

    # Повторный триал невозможен: кнопки нет, а API отвечает отказом
    page.reload()
    expect(page.get_by_role("button", name="Попробовать бесплатно")).to_have_count(0)
    resp = page.request.post(f"{stack.cfg.web_url}/api/v1/web/keys/trial", headers={"X-Requested-With": "web"})
    assert resp.status == 403, resp.text()

    # Выход: сессия удалена, кабинет снова гостевой
    page.get_by_role("button", name="Выйти").click()
    expect(page.get_by_role("button", name="Войти через бота")).to_be_visible()
    page.reload()
    expect(page.get_by_role("button", name="Войти через бота")).to_be_visible()


def _pay_on_checkout(page) -> None:
    """Страница «ЮKassa»: кнопка оплаты шлёт webhook и возвращает на сайт."""
    expect(page.get_by_role("heading", name=re.compile("Тестовая оплата"))).to_be_visible()
    page.get_by_role("button", name="Оплатить").click()


def test_buy_and_renew_key_through_yookassa(stack, page, fakes, db, web_login, new_tg_id):
    web_login(page, new_tg_id)
    page.get_by_role("button", name="Оплата").click()
    page.get_by_role("button", name=re.compile("Стандарт")).click()
    pay = page.get_by_role("button", name=re.compile(r"^Оплатить"))
    expect(pay).to_contain_text("199")
    pay.click()

    page.wait_for_url(re.compile(r"/yk/checkout/"))
    payment_id = page.url.rsplit("/", 1)[1]
    yk = fakes.state()["payments"][payment_id]
    assert yk["amount"]["value"] == "199.00"
    assert yk["confirmation"]["return_url"] == f"{stack.cfg.web_url}/pay/result"
    assert yk["metadata"]["tg_id"] == str(new_tg_id)

    _pay_on_checkout(page)
    page.wait_for_url(f"{stack.cfg.web_url}/pay/result")
    expect(page.get_by_role("heading", name="Оплата прошла")).to_be_visible(timeout=30_000)
    expect(page.get_by_text("Новый ключ создан на 1 мес.")).to_be_visible()

    payment = db.fetch("SELECT * FROM payments WHERE payment_id=$1", payment_id)[0]
    assert payment["status"] == "succeeded"
    keys = db.keys_of(new_tg_id)
    assert len(keys) == 1 and keys[0]["payment_id"] == payment_id
    email = keys[0]["email"]
    panel = fakes.panel_client(email)
    assert panel["inboundIds"] == PAID_INBOUNDS, panel
    assert panel["client"]["expiryTime"] == keys[0]["expiry_time"]
    first_expiry = keys[0]["expiry_time"]
    assert 29 < (first_expiry / 1000 - time.time()) / 86400 <= 31

    # Пользователь получил уведомление в боте
    fakes.wait_bot_message(new_tg_id, lambda m: m["method"] == "sendMessage")

    # Повторный webhook ЮKassa не выдаёт второй ключ и не продлевает
    again = fakes.pay(payment_id)
    assert again["webhook_status"] == 200, again
    assert len(db.keys_of(new_tg_id)) == 1
    assert db.keys_of(new_tg_id)[0]["expiry_time"] == first_expiry

    # Продление того же ключа
    page.get_by_role("button", name="В кабинет").click()
    expect(page.get_by_text(email)).to_be_visible()
    page.get_by_role("button", name="Оплата").click()
    page.get_by_role("button", name=re.compile("Продлить Стандарт")).click()
    page.get_by_role("button", name=re.compile(r"^Продлить \d")).click()
    page.wait_for_url(re.compile(r"/yk/checkout/"))
    renew_id = page.url.rsplit("/", 1)[1]
    _pay_on_checkout(page)
    expect(page.get_by_role("heading", name="Оплата прошла")).to_be_visible(timeout=30_000)
    expect(page.get_by_text("Ключ продлён на 1 мес.")).to_be_visible()

    keys = db.keys_of(new_tg_id)
    assert len(keys) == 1
    renewed = keys[0]["expiry_time"]
    added_days = (renewed - first_expiry) / 86_400_000
    assert 29 < added_days <= 31, added_days
    assert fakes.panel_client(email)["client"]["expiryTime"] == renewed
    assert db.fetch("SELECT status FROM payments WHERE payment_id=$1", renew_id)[0]["status"] == "succeeded"

    # История платежей в кабинете
    page.get_by_role("button", name="В кабинет").click()
    page.get_by_role("button", name="Оплата").click()
    expect(page.get_by_text("Продление")).to_be_visible()
    expect(page.get_by_text("Оплачен")).to_have_count(2)


def test_unpaid_invoice_is_reused_not_duplicated(stack, page, fakes, db, web_login, new_tg_id):
    """Вернулся со страницы оплаты, не заплатив, и нажал «Оплатить» ещё раз — счёт тот же."""
    web_login(page, new_tg_id)
    ids = []
    for _ in range(2):
        page.goto(stack.cfg.web_url)
        page.get_by_role("button", name="Оплата").click()
        page.get_by_role("button", name=re.compile(r"^Оплатить")).click()
        page.wait_for_url(re.compile(r"/yk/checkout/"))
        ids.append(page.url.rsplit("/", 1)[1])
    assert ids[0] == ids[1]
    assert len(db.payments_of(new_tg_id)) == 1

    # Webhook, которому ЮKassa не подтверждает оплату, ключ не выдаёт
    resp = page.request.post(
        f"{stack.cfg.backend_url}/api/v1/payments/webhook",
        data={"type": "notification", "event": "payment.succeeded", "object": {"id": ids[0]}},
    )
    assert resp.status == 409, resp.text()
    assert db.keys_of(new_tg_id) == []


def test_channel_bonus_and_delete_key(stack, page, fakes, db, web_login, new_tg_id):
    web_login(page, new_tg_id)
    page.get_by_role("button", name="Попробовать бесплатно").click()
    expect(page.get_by_text("Пробный ключ создан.")).to_be_visible()
    email = db.keys_of(new_tg_id)[0]["email"]
    before = db.keys_of(new_tg_id)[0]["expiry_time"]

    # Без подписки на канал бонуса нет
    page.get_by_role("button", name="Бонус за канал").click()
    expect(page.get_by_role("status")).to_contain_text("подпишитесь на наш канал")
    assert db.keys_of(new_tg_id)[0]["expiry_time"] == before

    fakes.http.post("/_ctl/tg/chat-member", json={"chat_id": "*", "user_id": new_tg_id, "status": "member"})
    page.get_by_role("button", name="Бонус за канал").click()
    expect(page.get_by_role("status")).to_contain_text("Бонус начислен")
    after = db.keys_of(new_tg_id)[0]["expiry_time"]
    assert 6.9 < (after - before) / 86_400_000 <= 7.01
    assert fakes.panel_client(email)["client"]["expiryTime"] == after

    # Второй раз бонус не начисляется
    page.get_by_role("button", name="Бонус за канал").click()
    expect(page.get_by_role("status")).to_have_text("Бонус за канал уже получен.")
    assert db.keys_of(new_tg_id)[0]["expiry_time"] == after

    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Удалить").click()
    expect(page.get_by_text("Ключ удалён.")).to_be_visible()
    assert db.keys_of(new_tg_id) == []
    assert fakes.panel_client(email) is None


def test_referral_link_from_web_works_in_bot(stack, page, fakes, db, web_login, new_tg_id):
    web_login(page, new_tg_id)
    page.get_by_role("button", name="Друзья").click()
    link = page.locator("code.link-box").inner_text()
    assert link.startswith("https://t.me/e2e_vpn_bot?start="), link
    payload = link.split("start=", 1)[1]

    friend = new_tg_id + 500_000
    after = fakes.user_says(friend, f"/start {payload}")
    fakes.wait_text(friend, "Добро пожаловать", after=after)
    rows = db.fetch("SELECT referral_id FROM users WHERE tg_id=$1", friend)
    assert rows and rows[0]["referral_id"] == new_tg_id, rows
