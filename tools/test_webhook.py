"""
Симулятор вебхуков YooKassa для тестирования платёжного модуля backend.

Отправляет POST-запросы на эндпоинт backend, имитируя уведомления от YooKassa.
Требует DISABLE_WEBHOOK_IP_CHECK=true в .env backend.

Использование:
    # Успешный платёж
    python backend/tools/test_webhook.py --event succeeded --payment-id "test_001" --amount 300

    # С автосозданием записи платежа в БД
    python backend/tools/test_webhook.py --event succeeded --payment-id "test_002" --amount 300 \
        --create-payment --tg-id 123456 --payment-type "create_key|10" --months 1

    # Отменённый платёж
    python backend/tools/test_webhook.py --event canceled --payment-id "test_001"

    # Возврат
    python backend/tools/test_webhook.py --event refund --payment-id "test_004"
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# Добавляем корень проекта в sys.path для импорта config
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# --- Генераторы JSON-тел вебхуков ---

def make_payment_object(payment_id: str, status: str, amount: float, paid: bool) -> dict:
    """Базовый объект платежа в формате YooKassa."""
    return {
        "id": payment_id,
        "status": status,
        "paid": paid,
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB",
        },
        "income_amount": {
            "value": f"{amount * 0.97:.2f}",
            "currency": "RUB",
        },
        "description": f"Тестовый платёж {payment_id}",
        "recipient": {
            "account_id": "test_shop",
            "gateway_id": "test_gw",
        },
        "payment_method": {
            "type": "bank_card",
            "id": str(uuid.uuid4()),
            "saved": False,
            "card": {
                "first6": "555555",
                "last4": "4444",
                "expiry_month": "12",
                "expiry_year": "2028",
                "card_type": "MasterCard",
                "issuer_country": "RU",
            },
        },
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "test": True,
        "refundable": status == "succeeded",
        "metadata": {},
    }


def build_succeeded(payment_id: str, amount: float) -> dict:
    """payment.succeeded — успешный платёж."""
    obj = make_payment_object(payment_id, "succeeded", amount, paid=True)
    obj["captured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    obj["refunded_amount"] = {"value": "0.00", "currency": "RUB"}
    return {
        "type": "notification",
        "event": "payment.succeeded",
        "object": obj,
    }


def build_waiting(payment_id: str, amount: float) -> dict:
    """payment.waiting_for_capture — ожидание подтверждения."""
    obj = make_payment_object(payment_id, "waiting_for_capture", amount, paid=True)
    return {
        "type": "notification",
        "event": "payment.waiting_for_capture",
        "object": obj,
    }


def build_canceled(payment_id: str, amount: float, reason: str = "card_expired") -> dict:
    """payment.canceled — отменённый платёж."""
    obj = make_payment_object(payment_id, "canceled", amount, paid=False)
    obj["cancellation_details"] = {
        "party": "payment_network",
        "reason": reason,
    }
    return {
        "type": "notification",
        "event": "payment.canceled",
        "object": obj,
    }


def build_refund(payment_id: str, amount: float) -> dict:
    """refund.succeeded — успешный возврат."""
    return {
        "type": "notification",
        "event": "refund.succeeded",
        "object": {
            "id": str(uuid.uuid4()),
            "payment_id": payment_id,
            "status": "succeeded",
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB",
            },
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        },
    }


EVENT_BUILDERS = {
    "succeeded": build_succeeded,
    "waiting": build_waiting,
    "canceled": build_canceled,
    "refund": build_refund,
}


# --- Создание тестовой записи платежа в БД ---

async def create_payment_in_db(
    payment_id: str,
    tg_id: int,
    amount: float,
    payment_type: str,
    months: int,
) -> None:
    """Создаёт запись платежа в БД напрямую через asyncpg."""
    import asyncpg
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    from config import settings

    if not settings.database_url:
        print("ОШИБКА: DATABASE_URL не задан в .env")
        sys.exit(1)

    conn = await asyncpg.connect(settings.database_url)
    try:
        # Проверяем, есть ли уже такой платёж
        existing = await conn.fetchrow(
            "SELECT payment_id FROM payments WHERE payment_id = $1", payment_id
        )
        if existing:
            print(f"  Платёж {payment_id} уже существует в БД, пропускаю создание")
            return

        await conn.execute(
            """
            INSERT INTO payments (payment_id, tg_id, amount, payment_type, status, number_of_months, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            payment_id,
            tg_id,
            amount,
            payment_type,
            "pending",
            months,
            datetime.now(),
        )
        print(f"  Платёж создан в БД: id={payment_id}, type={payment_type}, "
              f"tg_id={tg_id}, amount={amount}, months={months}")
    finally:
        await conn.close()


# --- Отправка вебхука ---

async def send_webhook(url: str, payload: dict) -> None:
    """Отправляет POST-запрос на эндпоинт вебхука."""
    print(f"\n{'=' * 60}")
    print(f"  URL:   {url}")
    print(f"  Event: {payload['event']}")
    print(f"  ID:    {payload['object']['id']}")
    if "amount" in payload["object"]:
        print(f"  Sum:   {payload['object']['amount']['value']} RUB")
    print(f"{'=' * 60}")
    print(f"\nJSON:\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                body = await resp.text()
                status = resp.status

                if status == 200:
                    print(f"  УСПЕХ: HTTP {status} — {body}")
                else:
                    print(f"  ОШИБКА: HTTP {status} — {body}")
                    if status == 403:
                        print("  Подсказка: проверьте DISABLE_WEBHOOK_IP_CHECK=true в .env")

        except aiohttp.ClientConnectorError:
            print(f"  ОШИБКА: не удалось подключиться к {url}")
            print("  Подсказка: убедитесь что backend запущен (uvicorn app.main:app --reload)")


# --- CLI ---

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Симулятор вебхуков YooKassa для backend",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  %(prog)s --event succeeded --payment-id pay_001 --amount 300
  %(prog)s --event canceled --payment-id pay_001 --reason insufficient_funds
  %(prog)s --event succeeded --payment-id pay_002 --create-payment --tg-id 123 --payment-type "create_key|10"
        """,
    )

    parser.add_argument(
        "--event",
        choices=list(EVENT_BUILDERS.keys()),
        default="succeeded",
        help="Тип события (default: succeeded)",
    )
    parser.add_argument(
        "--payment-id",
        default=f"test_{uuid.uuid4().hex[:8]}",
        help="ID платежа (default: случайный)",
    )
    parser.add_argument("--amount", type=float, default=100.0, help="Сумма в рублях (default: 100)")
    parser.add_argument("--reason", default="card_expired", help="Причина отмены (для canceled)")

    # Адрес эндпоинта
    parser.add_argument("--host", default="localhost", help="Хост (default: localhost)")
    parser.add_argument("--port", type=int, default=8000, help="Порт (default: 8000)")
    parser.add_argument("--path", default="/api/v1/payments/webhook", help="Путь (default: /api/v1/payments/webhook)")

    # Создание платежа в БД
    db_group = parser.add_argument_group("создание платежа в БД")
    db_group.add_argument("--create-payment", action="store_true", help="Создать запись платежа в БД перед отправкой")
    db_group.add_argument("--tg-id", type=int, default=0, help="Telegram ID пользователя")
    db_group.add_argument(
        "--payment-type",
        default="create_key|10",
        help='Тип операции: "create_key|<tariff_id>" или "renew_key|<email>" (default: create_key|10)',
    )
    db_group.add_argument("--months", type=int, default=1, help="Количество месяцев (default: 1)")

    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    url = f"http://{args.host}:{args.port}{args.path}"

    print(f"\n  Симулятор вебхуков YooKassa (backend)")
    print(f"  Endpoint: {url}\n")

    # Создание записи в БД (если запрошено)
    if args.create_payment:
        print("  Создание платежа в БД...")
        await create_payment_in_db(
            payment_id=args.payment_id,
            tg_id=args.tg_id,
            amount=args.amount,
            payment_type=args.payment_type,
            months=args.months,
        )

    # Сборка payload
    builder = EVENT_BUILDERS[args.event]
    if args.event == "canceled":
        payload = builder(args.payment_id, args.amount, args.reason)
    else:
        payload = builder(args.payment_id, args.amount)

    # Отправка
    await send_webhook(url, payload)


if __name__ == "__main__":
    asyncio.run(main())
