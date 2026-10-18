"""Mini App (vpn_miniapp за своим nginx): вход по initData, триал, продление триала оплатой."""
import re
import time

from playwright.sync_api import expect

from stack import PAID_INBOUNDS


def test_trial_then_pay_to_renew_trial(stack, telegram_page, sign, fakes, db, new_tg_id):
    page = telegram_page(sign(new_tg_id))
    page.goto(stack.cfg.miniapp_url)
    expect(page.get_by_role("heading", name="Защиты пока нет")).to_be_visible()

    page.get_by_role("button", name="Попробовать бесплатно").click()
    keys = []
    for _ in range(50):
        keys = db.keys_of(new_tg_id)
        if keys:
            break
        time.sleep(0.2)
    assert len(keys) == 1
    email = keys[0]["email"]
    expect(page.get_by_text(email)).to_be_visible()
    trial_days = (keys[0]["expiry_time"] / 1000 - time.time()) / 86400
    assert 6.9 < trial_days <= 7.01, trial_days
    panel = fakes.panel_client(email)
    assert panel["inboundIds"] == PAID_INBOUNDS and panel["client"]["expiryTime"] == keys[0]["expiry_time"]

    # Продление пробного ключа платным тарифом: месяц + остаток триала
    page.get_by_role("navigation").get_by_role("button", name="Оплата").click()
    page.get_by_role("button", name=re.compile("Продлить Пробный")).click()
    page.get_by_role("button", name=re.compile("Стандарт")).click()
    page.get_by_role("button", name=re.compile(r"^Продлить \d")).click()

    opened: list[str] = []
    for _ in range(50):
        opened = page.evaluate("window.__openedLinks || []")
        if opened:
            break
        time.sleep(0.2)
    assert opened and "/yk/checkout/" in opened[0], opened
    payment_id = opened[0].rsplit("/", 1)[1]
    assert fakes.pay(payment_id)["webhook_status"] == 200

    expect(page.get_by_role("dialog")).to_contain_text("продлён на 1 мес.", timeout=20_000)
    key = db.keys_of(new_tg_id)
    assert len(key) == 1
    days = (key[0]["expiry_time"] / 1000 - time.time()) / 86400
    assert 36.5 < days <= 37.1, days
    assert key[0]["tariff_id"] == 7
    assert fakes.panel_client(email)["client"]["expiryTime"] == key[0]["expiry_time"]


def test_miniapp_outside_telegram_and_wrong_bot(stack, page, telegram_page, sign, new_tg_id):
    page.goto(stack.cfg.miniapp_url)
    expect(page.get_by_role("alert")).to_have_text("Откройте приложение из Telegram.")

    # initData админ-бота не открывает Mini App основного бота
    p = telegram_page(sign(new_tg_id, bot="admin"))
    p.goto(stack.cfg.miniapp_url)
    expect(p.get_by_role("alert")).to_be_visible()
    expect(p.get_by_role("heading", name="Ключи")).to_have_count(0)
