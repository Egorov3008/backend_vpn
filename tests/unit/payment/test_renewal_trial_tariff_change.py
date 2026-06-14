"""Тесты для продления триального ключа на платный тариф.

Регрессионный тест для бага:
"При продлении триального ключа платный тариф не применяется, период остается +7 дней"

Корневая причина: бот сохранял выбранный тариф только в dialog_data,
но не передавал в backend кеш. Backend fallback'ился на key.tariff_id
(триальный), и калькулятор продлевал триал вместо платного тарифа.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add backend to path
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from services.core.payment.renewal_service import KeyRenewalService
from services.core.payment.processor import PaymentProcessor
from services.core.keys.utils.calculator import TRIAL_TARIFF_ID, TRIAL_PERIOD_DAYS


def make_mock_key(
    email: str,
    tariff_id: int,
    name_tariff: str,
    expiry_time: int,
    total_gb: int,
    tg_id: int = 123456789,
):
    """Helper для создания мок-ключа."""
    key = MagicMock()
    key.email = email
    key.tariff_id = tariff_id
    key.name_tariff = name_tariff
    key.expiry_time = expiry_time
    key.total_gb = total_gb
    key.tg_id = tg_id
    key.key = f"key_{email}"
    return key


def make_mock_tariff(tariff_id: int, name: str, amount: float, period: int, traffic_limit: int):
    """Helper для создания мок-тарифа."""
    tariff = MagicMock()
    tariff.id = tariff_id
    tariff.name_tariff = name
    tariff.amount = amount
    tariff.period = period
    tariff.traffic_limit = traffic_limit
    return tariff


class TestTrialKeyRenewalToPaidTariff:
    """Тесты продления триального ключа на платный тариф."""

    @pytest.mark.asyncio
    @patch("services.core.payment.renewal_service.bot", new_callable=AsyncMock)
    async def test_renewal_uses_cached_tariff_not_key_tariff(self, mock_bot):
        """При продлении триального ключа должен использоваться тариф из кеша, а не из ключа.

        Это основной тест бага: если кеш содержит выбранный платный тариф,
        он должен быть использован вместо key.tariff_id.
        """
        # Arrange: триальный ключ
        mock_key = make_mock_key(
            email="trial@example.com",
            tariff_id=TRIAL_TARIFF_ID,  # 10 - триальный тариф
            name_tariff="Trial",
            expiry_time=int((datetime.now() + timedelta(days=3)).timestamp() * 1000),
            total_gb=10 * (2**30),
        )

        # Платный тариф, который пользователь выбрал для продления
        mock_paid_tariff = make_mock_tariff(
            tariff_id=2,  # Платный тариф
            name="Pro",
            amount=100.0,
            period=30,  # 30 дней
            traffic_limit=100,
        )

        # Setup processor
        processor = MagicMock(spec=PaymentProcessor)
        processor.tg_id = 123456789
        processor.number_of_months = 1
        processor.amount = 100.0
        processor._conn = AsyncMock()

        # Моки для загрузки данных
        processor._model_service = AsyncMock()
        processor._model_service.keys.get_data = AsyncMock(return_value=mock_key)
        processor._model_service.tariffs.get_data = AsyncMock(return_value=mock_paid_tariff)
        processor._model_service.users.get_data = AsyncMock(
            return_value=MagicMock(server_id=1)
        )
        processor._model_service.servers.get_data = AsyncMock(return_value=MagicMock())

        # Кеш возвращает выбранный платный тариф (tariff_id=2)
        processor._cache = AsyncMock()
        processor._cache.tariffs.temporary_get = AsyncMock(
            return_value={"tariff_id": 2}  # Выбран платный тариф, а не триальный!
        )
        processor._cache.tariffs.delete = AsyncMock()

        # Key manager mock
        key_manager = AsyncMock()
        key_manager.extension_key = AsyncMock(return_value=mock_key)

        # Act
        service = KeyRenewalService(processor, key_manager)
        await service.process(email="trial@example.com")

        # Assert: тариф загружен по tariff_id из кеша (2), а не из ключа (10)
        processor._model_service.tariffs.get_data.assert_called_once()
        call_args = processor._model_service.tariffs.get_data.call_args
        assert call_args.args[0] == 2, "Должен использоваться tariff_id=2 из кеша"

        # Assert: extension_key вызван с платным тарифом
        call_args = key_manager.extension_key.call_args
        assert call_args.kwargs["tariff"].id == 2, "Должен использоваться платный тариф из кеша"
        assert call_args.kwargs["tariff"].period == 30, "Период платного тарифа = 30 дней"

        # Assert: кеш очищен после успешного продления
        processor._cache.tariffs.delete.assert_called_once_with(
            "renewal_tariff_trial@example.com"
        )

    @pytest.mark.asyncio
    @patch("services.core.payment.renewal_service.bot", new_callable=AsyncMock)
    async def test_renewal_fallback_to_key_tariff_when_cache_empty(self, mock_bot):
        """Если кеш пуст, fallback на key.tariff_id (для обычного продления)."""
        mock_key = make_mock_key(
            email="regular@example.com",
            tariff_id=2,  # Платный тариф
            name_tariff="Pro",
            expiry_time=int((datetime.now() + timedelta(days=10)).timestamp() * 1000),
            total_gb=100 * (2**30),
        )

        mock_tariff = make_mock_tariff(
            tariff_id=2,
            name="Pro",
            amount=100.0,
            period=30,
            traffic_limit=100,
        )

        processor = MagicMock(spec=PaymentProcessor)
        processor.tg_id = 123456789
        processor.number_of_months = 1
        processor.amount = 100.0
        processor._conn = AsyncMock()

        processor._model_service = AsyncMock()
        processor._model_service.keys.get_data = AsyncMock(return_value=mock_key)
        processor._model_service.tariffs.get_data = AsyncMock(return_value=mock_tariff)
        processor._model_service.users.get_data = AsyncMock(
            return_value=MagicMock(server_id=1)
        )
        processor._model_service.servers.get_data = AsyncMock(return_value=MagicMock())

        # Кеш пуст - fallback на key.tariff_id
        processor._cache = AsyncMock()
        processor._cache.tariffs.temporary_get = AsyncMock(return_value=None)

        key_manager = AsyncMock()
        key_manager.extension_key = AsyncMock(return_value=mock_key)

        service = KeyRenewalService(processor, key_manager)
        await service.process(email="regular@example.com")

        # Assert: тариф загружен по key.tariff_id (fallback)
        processor._model_service.tariffs.get_data.assert_called_once()
        call_args = processor._model_service.tariffs.get_data.call_args
        assert call_args.args[0] == 2, "Должен использоваться tariff_id=2 из ключа"

        # Assert: кеш не очищался (не было selected_tariff_id)
        processor._cache.tariffs.delete.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.core.payment.renewal_service.bot", new_callable=AsyncMock)
    async def test_trial_renewal_same_tariff_uses_calculator_logic(self, mock_bot):
        """Продление триала на тот же триальный тариф (edge case)."""
        current_expiry = int((datetime.now() + timedelta(days=5)).timestamp() * 1000)
        mock_key = make_mock_key(
            email="trial2@example.com",
            tariff_id=TRIAL_TARIFF_ID,
            name_tariff="Trial",
            expiry_time=current_expiry,
            total_gb=10 * (2**30),
        )

        mock_trial_tariff = make_mock_tariff(
            tariff_id=TRIAL_TARIFF_ID,
            name="Trial",
            amount=0.0,
            period=TRIAL_PERIOD_DAYS,  # 7 дней
            traffic_limit=10,
        )

        processor = MagicMock(spec=PaymentProcessor)
        processor.tg_id = 123456789
        processor.number_of_months = 1
        processor.amount = 0.0
        processor._conn = AsyncMock()

        processor._model_service = AsyncMock()
        processor._model_service.keys.get_data = AsyncMock(return_value=mock_key)
        processor._model_service.tariffs.get_data = AsyncMock(return_value=mock_trial_tariff)
        processor._model_service.users.get_data = AsyncMock(
            return_value=MagicMock(server_id=1)
        )
        processor._model_service.servers.get_data = AsyncMock(return_value=MagicMock())

        # Кеш пуст - продление на тот же триальный тариф
        processor._cache = AsyncMock()
        processor._cache.tariffs.temporary_get = AsyncMock(return_value=None)

        key_manager = AsyncMock()
        key_manager.extension_key = AsyncMock(return_value=mock_key)

        service = KeyRenewalService(processor, key_manager)
        await service.process(email="trial2@example.com")

        # Assert: продление на триальный тариф
        processor._model_service.tariffs.get_data.assert_called_once()
        call_args = processor._model_service.tariffs.get_data.call_args
        assert call_args.args[0] == TRIAL_TARIFF_ID, "Должен использоваться TRIAL_TARIFF_ID=10"


class TestExpiryCalculatorForTrialRenewal:
    """Тесты калькулятора срока для триальных ключей."""

    @pytest.mark.asyncio
    async def test_trial_to_paid_adds_paid_period_only(self):
        """При переходе с триала на платный тариф добавляется только период платного тарифа.

        Логика калькулятора:
        - Если key.tariff_id == TRIAL_TARIFF_ID и days == TRIAL_PERIOD_DAYS → триал + остаток
        - Если key.tariff_id == TRIAL_TARIFF_ID и days != TRIAL_PERIOD_DAYS → платный тариф

        Для платного тарифа: new_expiry = max(now, expiry) + paid_period
        """
        from services.core.keys.utils.calculator import ExpiryCalculator

        calculator = ExpiryCalculator()

        # Триальный ключ с 4 днями до истечения
        mock_key = MagicMock()
        mock_key.tariff_id = TRIAL_TARIFF_ID
        mock_key.expiry_time = int((datetime.now() + timedelta(days=4)).timestamp() * 1000)

        # Платный тариф с периодом 30 дней (days != TRIAL_PERIOD_DAYS)
        paid_period = 30  # дней

        # Act: продление на платный тариф
        new_expiry = calculator.key_duration(mock_key, days=paid_period, number_of_months=1)

        # Assert: должно добавить paid_period дней к expiry (не к now)
        # expiry + 30 дней
        expected_expiry = mock_key.expiry_time + (paid_period * 86_400_000)
        tolerance_ms = 60_000  # 1 минута погрешности

        assert abs(new_expiry - expected_expiry) < tolerance_ms, (
            f"Ожидалось {expected_expiry / 1000 / 86400:.1f} дней от epoch, "
            f"получено {new_expiry / 1000 / 86400:.1f} дней"
        )

    @pytest.mark.asyncio
    async def test_trial_to_trial_adds_period_plus_remaining(self):
        """При продлении триала на тот же триальный тариф добавляется период + остаток."""
        from services.core.keys.utils.calculator import ExpiryCalculator

        calculator = ExpiryCalculator()

        # Триальный ключ, созданный 2 дня назад (осталось 5 дней триала)
        created_at = datetime.now() - timedelta(days=2)
        mock_key = MagicMock()
        mock_key.tariff_id = TRIAL_TARIFF_ID
        mock_key.created_at = int(created_at.timestamp() * 1000)
        mock_key.expiry_time = int((datetime.now() + timedelta(days=5)).timestamp() * 1000)

        # Триальный тариф с периодом 7 дней
        trial_period = TRIAL_PERIOD_DAYS  # 7 дней

        # Act: продление на тот же триальный тариф (days == TRIAL_PERIOD_DAYS)
        new_expiry = calculator.key_duration(mock_key, days=trial_period, number_of_months=1)

        # Assert: 7 дней (период) + 5 дней (остаток) = 12 дней от now
        expected_days = trial_period + (TRIAL_PERIOD_DAYS - 2)  # 7 + 5 = 12
        expected_min = int((datetime.now() + timedelta(days=expected_days - 1)).timestamp() * 1000)
        expected_max = int((datetime.now() + timedelta(days=expected_days + 1)).timestamp() * 1000)

        assert expected_min <= new_expiry <= expected_max, (
            f"Ожидалось ~{expected_days} дней (период + остаток), "
            f"получено {(new_expiry - int(datetime.now().timestamp() * 1000)) / 1000 / 86400:.1f} дней"
        )
