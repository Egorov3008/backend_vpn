"""Массовое продление из веб-админки: расчёт, запуск фоновой задачи, результат.

Продлеваются все действующие платные ключи стенда, поэтому проверяем свой ключ:
его срок сдвигается ровно на выбранное число дней и это же видно в fake-панели.
"""
import re
import time

from playwright.sync_api import expect


def _active_paid_keys(db) -> int:
    [row] = db.fetch(
        """
        SELECT COUNT(*) AS n
        FROM keys k
        JOIN tariff t ON t.id = k.tariff_id
        WHERE k.expiry_time > $1 AND k.tg_id > 0 AND t.amount > 0
        """,
        int(time.time() * 1000),
    )
    return row["n"]


def test_admin_bulk_renews_active_paid_keys(
    stack, telegram_page, sign, fakes, db, admin_tg_id, new_tg_id
):
    page = telegram_page(sign(admin_tg_id, bot="admin"))
    page.goto(f"{stack.cfg.admin_url}/admin")
    expect(page.get_by_role("heading", name="Сводка")).to_be_visible()

    # Платный ключ нашему пользователю через карточку «Люди».
    page.get_by_role("navigation").get_by_role("button", name="Люди").click()
    page.get_by_label("Поиск пользователя").fill(str(new_tg_id))
    page.get_by_role("button", name=re.compile(str(new_tg_id))).click()
    page.get_by_role("combobox").first.select_option(label="Стандарт (199 ₽)")
    page.get_by_role("button", name="Выдать ключ").click()
    expect(page.get_by_text(re.compile(r"Ключ \S+ на \d+ дн\."))).to_be_visible()

    [key] = db.keys_of(new_tg_id)
    email, old_expiry = key["email"], key["expiry_time"]
    panel = fakes.panel_client(email)
    assert panel is not None and panel["client"]["expiryTime"] == old_expiry, panel

    # «Ещё» → «Массовое продление»: расчёт только по кнопке, при открытии ничего не считается.
    page.get_by_role("navigation").get_by_role("button", name="Ещё").click()
    page.get_by_role("button", name=re.compile("Массовое продление")).click()
    expect(page.get_by_text("Продлеваются только действующие платные ключи")).to_be_visible()
    expect(page.get_by_text("Предварительный расчёт")).to_have_count(0)

    page.get_by_label("Дней продления").fill("7")
    page.get_by_role("button", name="Посчитать").click()
    expect(page.get_by_text("Предварительный расчёт")).to_be_visible()

    expected = _active_paid_keys(db)
    assert page.locator(".kpi", has_text="Ключей").locator(".kpi-value").inner_text() == str(expected)

    page.get_by_role("button", name="Продлить на 7 дн.").click()
    # Подтверждение Telegram-заглушка принимает сразу; задача идёт в фоне, экран опрашивает статус.
    expect(page.get_by_text(re.compile(r"Продление на 7 дн\..*Завершено"))).to_be_visible(timeout=120_000)
    expect(page.get_by_text(re.compile(r"Готово: продлено \d+ из \d+"))).to_be_visible()

    [updated] = db.fetch("SELECT expiry_time FROM keys WHERE email = $1", email)
    assert updated["expiry_time"] == old_expiry + 7 * 86_400_000
    assert fakes.panel_client(email)["client"]["expiryTime"] == updated["expiry_time"]
