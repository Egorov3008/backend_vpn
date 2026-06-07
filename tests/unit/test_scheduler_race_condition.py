"""
Regression-тест для race condition в scheduler.

Проблема: глобальный флаг _sync_in_progress проверялся ДО входа в lock,
что позволяло нескольким запускам выполняться параллельно.

Тест проверяет, что:
1. Два одновременных вызова sync_cache → выполняется только один
2. Второй вызов получает status='skipped'
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


class TestSchedulerRaceCondition:
    """Тесты на отсутствие race condition при конкурентных вызовах синхронизации."""

    @pytest.mark.asyncio
    async def test_concurrent_sync_runs_only_once(self):
        """Два одновременных вызова sync_cache → выполняется только один."""
        from background.scheduler import SyncScheduler

        # Создаем синхронизатор с моками
        service_data = MagicMock()
        pool = AsyncMock()
        scheduler = SyncScheduler(service_data=service_data, pool=pool)

        # Мокаем методы экземпляра
        async def mock_sync_panel():
            await asyncio.sleep(0.01)  # Имитируем работу
            return {"status": "success", "sync_time": "1.0s"}

        scheduler._sync_panel = mock_sync_panel

        # Запускаем два вызова параллельно
        task1 = asyncio.create_task(scheduler.sync_cache())
        task2 = asyncio.create_task(scheduler.sync_cache())

        # Ждем завершения обеих задач
        results = await asyncio.gather(task1, task2)

        # Проверяем результаты
        result_list = list(results)
        successful = sum(1 for r in result_list if r.get('status') == 'success')
        skipped = sum(1 for r in result_list if r.get('status') == 'skipped')

        # ТОЛЬКО один должен выполниться успешно
        assert successful == 1, f"Ожидался 1 успешный sync, но их было {successful}"
        # Второй должен быть пропущен
        assert skipped >= 1, f"Ожидался хотя бы 1 skipped sync, но их было {skipped}"

    @pytest.mark.asyncio
    async def test_sync_state_reset_after_exception(self):
        """При исключении во время sync состояние должно сбрасываться."""
        from background.scheduler import SyncScheduler

        service_data = MagicMock()
        pool = AsyncMock()
        scheduler = SyncScheduler(service_data=service_data, pool=pool)

        # Мокаем loading с исключением
        mock_loader = AsyncMock()
        mock_loader.loading = AsyncMock(side_effect=Exception("Simulated failure"))

        with patch('database.service.DataService'), \
             patch('services.cache.loader.LoadingService', return_value=mock_loader):

            # Первый вызов падает - исключение ловится внутри, возвращается error status
            result = await scheduler.sync_cache()

            # Должен вернуться error status
            assert result.get('status') == 'error'

        # Проверяем, что флаг сбросился после исключения
        assert scheduler._sync_in_progress == False, "Флаг должен сброситься после исключения"

        # Второй вызов должен выполниться нормально (состояние сбросилось)
        mock_loader2 = AsyncMock()
        mock_loader2.loading = AsyncMock()

        async def mock_sync_panel_success():
            return {"status": "success", "sync_time": "1.0s"}

        scheduler._sync_panel = mock_sync_panel_success

        with patch('database.service.DataService'), \
             patch('services.cache.loader.LoadingService', return_value=mock_loader2):

            result = await scheduler.sync_cache()

            # Второй вызов должен выполниться
            assert result.get('status') == 'success'

    @pytest.mark.asyncio
    async def test_rapid_sequential_calls(self):
        """Быстрые последовательные вызовы → каждый следующий skipped."""
        from background.scheduler import SyncScheduler

        service_data = MagicMock()
        pool = AsyncMock()
        scheduler = SyncScheduler(service_data=service_data, pool=pool)

        # Мокаем _sync_panel с небольшой задержкой
        call_count = 0

        async def mock_sync_panel():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.02)  # Имитируем работу
            return {"status": "success", "sync_time": "1.0s"}

        scheduler._sync_panel = mock_sync_panel

        # Запускаем 5 вызовов почти одновременно
        tasks = [asyncio.create_task(scheduler.sync_cache()) for _ in range(5)]
        results = await asyncio.gather(*tasks)

        # _sync_panel должен вызваться только 1 раз
        assert call_count == 1, f"_sync_panel вызван {call_count} раз, ожидался 1"

        # Подсчитываем результаты
        result_list = list(results)
        successful = sum(1 for r in result_list if r.get('status') == 'success')
        skipped = sum(1 for r in result_list if r.get('status') == 'skipped')

        assert successful == 1, f"Ожидался 1 успешный sync, но их было {successful}"
        assert skipped == 4, f"Ожидалось 4 skipped sync, но их было {skipped}"

    @pytest.mark.asyncio
    async def test_sync_stats_tracking(self):
        """SyncScheduler отслеживает статистику вызовов."""
        from background.scheduler import SyncScheduler

        service_data = MagicMock()
        pool = AsyncMock()
        scheduler = SyncScheduler(service_data=service_data, pool=pool)

        async def mock_sync_panel():
            return {"status": "success", "sync_time": "1.0s"}

        scheduler._sync_panel = mock_sync_panel

        # Initial stats
        stats = scheduler.stats
        assert stats['sync_count'] == 0
        assert stats['is_sync_in_progress'] == False

        # Первый вызов
        result1 = await scheduler.sync_cache()
        assert result1['status'] == 'success'
        assert scheduler.stats['sync_count'] == 1

        # Второй вызов (должен быть skipped)
        result2 = await scheduler.sync_cache()
        # Note: второй вызов выполнится успешно, т.к. первый уже завершился
        assert scheduler.stats['sync_count'] == 2
