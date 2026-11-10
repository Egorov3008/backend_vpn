import uuid
from typing import List, Optional

from database.base import BaseRepository
from database.protocols import DatabaseProtocol
from models import (
    ApprovedInbound,
    GiftLink,
    Key,
    LoginCode,
    PaymentModel,
    ReferralLink,
    ReferralRedemption,
    ReferralReward,
    Server,
    Tariff,
    User,
)
from models.stocks.stock import Stock

# Через сколько минут платёж в статусе 'processing' считается брошенным
# (процесс упал между захватом и финальным статусом) и может быть захвачен заново.
PAYMENT_CLAIM_STALE_MINUTES = 10


class PaymentRepository(BaseRepository[PaymentModel]):
    """Репозиторий платежей с атомарным захватом для обработки.

    Захват — единственный способ войти в обработку платежа: UPDATE с условием
    по статусу выполняется в одной SQL-операции, поэтому два параллельных
    процесса (вебхук и поллинг) не могут оба получить платёж.
    """

    async def claim_pending(self, pool, payment_id: str) -> Optional[str]:
        """Перевести платёж pending -> processing.

        Возвращает claim-token владельца захвата или None, если захват не удался.
        Также перехватывает брошенные claim'ы старше PAYMENT_CLAIM_STALE_MINUTES:
        после перехвата прежний владелец уже не может сбросить или завершить захват.
        """
        token = str(uuid.uuid4())
        query = f"""
            UPDATE {self.table_name}
            SET status = 'processing',
                processing_started_at = NOW(),
                processing_token = $3
            WHERE payment_id = $1
              AND (
                  status = 'pending'
                  OR (status = 'processing'
                      AND processing_started_at < NOW() - make_interval(mins => $2))
              )
            RETURNING payment_id
        """
        async with await self._acquire(pool) as conn:
            claimed = await conn.fetchval(query, payment_id, PAYMENT_CLAIM_STALE_MINUTES, token)
            return token if claimed is not None else None

    async def release_claim(self, pool, payment_id: str, token: str) -> bool:
        """Вернуть платёж processing -> pending после неудачной обработки (для retry).

        True, только если захват ещё принадлежит владельцу с этим токеном.
        """
        query = f"""
            UPDATE {self.table_name}
            SET status = 'pending', processing_started_at = NULL, processing_token = NULL
            WHERE payment_id = $1 AND status = 'processing' AND processing_token = $2
            RETURNING payment_id
        """
        async with await self._acquire(pool) as conn:
            released = await conn.fetchval(query, payment_id, token)
            return released is not None

    async def complete_claim(self, pool, payment_id: str, token: str) -> bool:
        """Перевести платёж processing -> succeeded, если захват принадлежит токену.

        True, только если владелец ещё держит захват. False — захват перехвачен
        другим процессом, финальный статус писать нельзя.
        """
        query = f"""
            UPDATE {self.table_name}
            SET status = 'succeeded', processing_token = NULL, succeeded_at = NOW()
            WHERE payment_id = $1 AND status = 'processing' AND processing_token = $2
            RETURNING payment_id
        """
        async with await self._acquire(pool) as conn:
            completed = await conn.fetchval(query, payment_id, token)
            return completed is not None

    async def reserve_renewal_expiry(self, pool, payment_id: str, expiry_time: int) -> int:
        """Зафиксировать целевой срок продления за платежом и вернуть итоговое значение.

        Срок записывается, только если ещё не был записан (первая попытка). Повтор
        (после сбоя или перехвата захвата) получает уже сохранённое значение, а не новое,
        поэтому панель и БД получают один и тот же абсолютный срок — повтор не удлиняет
        ключ второй раз.
        """
        write = f"""
            UPDATE {self.table_name}
            SET renewal_expiry_time = $2
            WHERE payment_id = $1 AND renewal_expiry_time IS NULL
            RETURNING renewal_expiry_time
        """
        read = f"SELECT renewal_expiry_time FROM {self.table_name} WHERE payment_id = $1"
        async with await self._acquire(pool) as conn:
            written = await conn.fetchval(write, payment_id, expiry_time)
            if written is not None:
                return int(written)
            stored = await conn.fetchval(read, payment_id)
            if stored is None:
                raise RuntimeError(f"Платёж {payment_id} не найден при фиксации срока продления")
            return int(stored)

    async def list_open_for_user(
        self, pool, tg_id: int, payment_type: str, max_age_minutes: int
    ) -> list[PaymentModel]:
        """Незавершённые (pending/processing) платежи пользователя за ту же операцию.

        Свежие сначала. Нужны, чтобы не выставлять второй счёт за тот же ключ, пока
        первый ещё может быть оплачен: иначе обе оплаты пройдут и выдадут два ключа.
        """
        query = f"""
            SELECT * FROM {self.table_name}
            WHERE tg_id = $1::bigint
              AND payment_type = $2
              AND status IN ('pending', 'processing')
              AND created_at >= NOW() - make_interval(mins => $3)
            ORDER BY created_at DESC
            LIMIT 5
        """
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(query, tg_id, payment_type, max_age_minutes)
            return [self.model(**self._adapt(r)) for r in records]

    async def mark_canceled_if_pending(self, pool, payment_id: str) -> bool:
        """Перевести платёж pending -> canceled, только если он ещё не захвачен.

        Условие по статусу в SQL: если вебхук или поллинг уже захватили платёж
        (processing) или завершили его (succeeded), строка не меняется.
        """
        query = f"""
            UPDATE {self.table_name}
            SET status = 'canceled'
            WHERE payment_id = $1 AND status = 'pending'
            RETURNING payment_id
        """
        async with await self._acquire(pool) as conn:
            canceled = await conn.fetchval(query, payment_id)
            return canceled is not None


class KeyRepository(BaseRepository[Key]):
    """Ключи + выборки для массового продления.

    Действующий платный ключ: срок ещё не истёк, владелец — реальный
    Telegram-пользователь, тариф существует и стоит больше нуля. Так
    исключаются trial (`tariff.amount = 0`), лендинговые ключи (`tg_id < 0`,
    виртуальный tariff_id без строки в tariff) и истёкшие ключи. Тариф,
    снятый с продажи, по-прежнему продлевается — ключ на нём действующий.
    """

    _ACTIVE_PAID = """
        k.expiry_time > $1
        AND k.tg_id > 0
        AND k.tariff_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM tariff t WHERE t.id = k.tariff_id AND t.amount > 0)
    """

    async def list_active_paid_emails(self, pool, now_ms: int) -> List[str]:
        """Email всех действующих платных ключей (без загрузки полных моделей)."""
        query = f"SELECT k.email FROM keys k WHERE {self._ACTIVE_PAID} ORDER BY k.email"
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(query, now_ms)
        return [r["email"] for r in records]

    async def list_all_emails(self, pool) -> List[str]:
        """Email всех ключей БД — цель массовой синхронизации inbound-набора."""
        async with await self._acquire(pool) as conn:
            records = await conn.fetch("SELECT email FROM keys ORDER BY email")
        return [r["email"] for r in records]

    async def count_all(self, pool) -> int:
        """Сколько ключей БД затронет массовая синхронизация."""
        async with await self._acquire(pool) as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM keys"))

    async def preview_active_paid(self, pool, now_ms: int) -> dict:
        """Сколько ключей затронет продление и как они распределены по тарифам."""
        totals_query = f"""
            SELECT COUNT(*)::int AS total_keys,
                   COUNT(DISTINCT k.tg_id)::int AS total_users,
                   MIN(k.expiry_time) AS min_expiry_time,
                   MAX(k.expiry_time) AS max_expiry_time
            FROM keys k
            WHERE {self._ACTIVE_PAID}
        """
        # total_users считается глобально: у одного пользователя могут быть
        # ключи на разных тарифах, сумма по группам была бы завышена.
        by_tariff_query = f"""
            SELECT k.tariff_id,
                   t.name_tariff,
                   COUNT(*)::int AS keys_count,
                   COUNT(DISTINCT k.tg_id)::int AS users_count
            FROM keys k
            JOIN tariff t ON t.id = k.tariff_id
            WHERE {self._ACTIVE_PAID}
            GROUP BY k.tariff_id, t.name_tariff
            ORDER BY keys_count DESC, k.tariff_id
        """
        async with await self._acquire(pool) as conn:
            totals = await conn.fetchrow(totals_query, now_ms)
            rows = await conn.fetch(by_tariff_query, now_ms)
        return {
            "total_keys": int(totals["total_keys"]),
            "total_users": int(totals["total_users"]),
            "min_expiry_time": (
                int(totals["min_expiry_time"]) if totals["min_expiry_time"] is not None else None
            ),
            "max_expiry_time": (
                int(totals["max_expiry_time"]) if totals["max_expiry_time"] is not None else None
            ),
            "by_tariff": [
                {
                    "tariff_id": r["tariff_id"],
                    "name_tariff": r["name_tariff"],
                    "keys": int(r["keys_count"]),
                    "users": int(r["users_count"]),
                }
                for r in rows
            ],
        }


class ApprovedInboundRepository(BaseRepository[ApprovedInbound]):
    """Утверждённый набор inbound'ов панели (единственный источник истины).

    Порядок задаётся ``sort_order``: первый inbound используется как
    ``keys.inbound_id`` при создании ключа.
    """

    def __init__(self) -> None:
        super().__init__(table_name="approved_inbounds", model=ApprovedInbound)

    async def get_all(self, pool) -> List[ApprovedInbound]:
        return await self.list_ordered(pool)

    async def list_ordered(self, pool) -> List[ApprovedInbound]:
        query = (
            "SELECT * FROM approved_inbounds ORDER BY sort_order, inbound_id"
        )
        async with await self._acquire(pool) as conn:
            records = await conn.fetch(query)
        return [self.model(**self._adapt(r)) for r in records]

    async def count(self, pool) -> int:
        async with await self._acquire(pool) as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM approved_inbounds"))

    async def add(
        self, pool, inbound_id: int, created_by_tg_id: Optional[int] = None
    ) -> tuple[Optional[ApprovedInbound], bool]:
        """Добавить inbound в конец набора. Возвращает ``(model, created)``."""
        insert = """
            INSERT INTO approved_inbounds (inbound_id, sort_order, created_by_tg_id)
            SELECT $1, COALESCE(MAX(sort_order), -1) + 1, $2::bigint
            FROM approved_inbounds
            ON CONFLICT (inbound_id) DO NOTHING
            RETURNING *
        """
        async with await self._acquire(pool) as conn:
            record = await conn.fetchrow(insert, inbound_id, created_by_tg_id)
            if record is not None:
                return self.model(**self._adapt(record)), True
            record = await conn.fetchrow(
                "SELECT * FROM approved_inbounds WHERE inbound_id = $1", inbound_id
            )
        if record is None:
            return None, False
        return self.model(**self._adapt(record)), False

    async def remove_if_not_last(self, pool, inbound_id: int) -> bool:
        """Удалить inbound, только если в наборе останется хотя бы один."""
        query = """
            DELETE FROM approved_inbounds
            WHERE inbound_id = $1
              AND (SELECT COUNT(*) FROM approved_inbounds) > 1
            RETURNING inbound_id
        """
        async with await self._acquire(pool) as conn:
            return await conn.fetchval(query, inbound_id) is not None


class DataService:
    """Основной сервис"""

    def __init__(self) -> None:
        self.users: DatabaseProtocol[User] = BaseRepository[User](
            table_name="users", model=User
        )
        self.keys: DatabaseProtocol[Key] = KeyRepository(
            table_name="keys", model=Key
        )
        self.approved_inbounds = ApprovedInboundRepository()
        self.servers: DatabaseProtocol[Server] = BaseRepository[Server](
            table_name="servers", model=Server
        )
        self.payments: PaymentRepository = PaymentRepository(
            table_name="payments", model=PaymentModel
        )
        self.tariffs: DatabaseProtocol[Tariff] = BaseRepository[Tariff](
            table_name="tariff", model=Tariff
        )
        self.gifts: DatabaseProtocol[GiftLink] = BaseRepository[GiftLink](
            table_name="gift_links", model=GiftLink
        )
        # Новые методы, требуют рефакторинга БД
        self.stocks: DatabaseProtocol[Stock] = BaseRepository[Stock](
            table_name="stocks", model=Stock
        )
        self.referral_links: DatabaseProtocol[ReferralLink] = BaseRepository[ReferralLink](
            table_name="referral_links", model=ReferralLink
        )
        self.referral_redemptions: DatabaseProtocol[ReferralRedemption] = BaseRepository[ReferralRedemption](
            table_name="referral_redemptions", model=ReferralRedemption
        )
        self.referral_rewards: DatabaseProtocol[ReferralReward] = BaseRepository[ReferralReward](
            table_name="referral_rewards", model=ReferralReward
        )
        self.login_codes: DatabaseProtocol[LoginCode] = BaseRepository[LoginCode](
            table_name="login_codes", model=LoginCode
        )
