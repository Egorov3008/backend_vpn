"""Task 5: Delete safety — admin_delete_key (409) + admin_delete_user (partial).

Покрывает новые safe-delete семантики:
- delete_key: 409 если XUI не удалил (ключ остаётся в БД).
- delete_user: 200 с {deleted_user, keys_deleted, keys_failed}; failed-ключи
  остаются в БД как orphan'ы (sweep в фоновой задаче Task 8).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.auth import verify_admin_actor, AdminPrincipal
from app.dependencies import get_service_data
from app.main import app
from models import Key


def make_key(email="a@b.com", inbound_id=1, client_id="c"):
    return Key(
        tg_id=100,
        client_id=client_id,
        email=email,
        expiry_time=9999999999000,
        key="https://sub.example.com/k",
        inbound_id=inbound_id,
        tariff_id=9,
    )


def _override_principal(principal=None):
    app.dependency_overrides[verify_admin_actor] = lambda: (
        principal or AdminPrincipal(admin_tg_id=1)
    )


@pytest.mark.asyncio
async def test_delete_key_xui_fail_keeps_row(api_client, mock_service_data):
    """XUI delete_client=False → 409, key row NOT deleted from DB."""
    key = make_key(email="a@b.com")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.data_service.keys.delete = AsyncMock()
    mock_service_data.cache_service.keys.delete = AsyncMock()

    xui = MagicMock()
    xui.delete_client = AsyncMock(return_value=False)  # XUI fail

    _override_principal(AdminPrincipal(admin_tg_id=1))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)):
        resp = await api_client.post("/api/v1/admin/keys/a@b.com/delete")

    assert resp.status_code == 409
    assert "panel" in resp.json()["detail"].lower()
    # Row kept: data_service.keys.delete must NOT be called
    mock_service_data.data_service.keys.delete.assert_not_called()
    mock_service_data.cache_service.keys.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_key_xui_success_deletes_row(api_client, mock_service_data):
    """XUI delete_client=True → 204, key row + cache deleted."""
    key = make_key(email="ok@b.com")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.data_service.keys.delete = AsyncMock()
    mock_service_data.cache_service.keys.delete = AsyncMock()

    xui = MagicMock()
    xui.delete_client = AsyncMock(return_value=True)

    _override_principal(AdminPrincipal(admin_tg_id=1))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)):
        resp = await api_client.post("/api/v1/admin/keys/ok@b.com/delete")

    assert resp.status_code == 204
    mock_service_data.data_service.keys.delete.assert_called_once()
    mock_service_data.cache_service.keys.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_user_partial_keeps_failed_key(api_client, mock_service_data):
    """delete_user: 200, deleted_user=True, keys_deleted=1, keys_failed=[{email,error}].

    User row always deleted; failed-XUI key rows stay in DB (orphan → sweep).
    """
    user = MagicMock(tg_id=123)
    key_ok = make_key(email="ok@b.com", inbound_id=1, client_id="c1")
    key_fail = make_key(email="fail@b.com", inbound_id=2, client_id="c2")

    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.get_by = AsyncMock(return_value=[key_ok, key_fail])
    mock_service_data.data_service.keys.delete = AsyncMock()
    mock_service_data.data_service.users.delete = AsyncMock()
    mock_service_data.cache_service.keys.delete = AsyncMock()
    mock_service_data.cache_service.users.delete = AsyncMock()

    xui = MagicMock()

    async def _delete(email, inbound_id, client_id):
        return email != "fail@b.com"

    xui.delete_client = _delete

    _override_principal(AdminPrincipal(admin_tg_id=1))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)):
        resp = await api_client.post("/api/v1/admin/users/123/delete")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_user"] is True
    assert body["keys_deleted"] == 1
    assert len(body["keys_failed"]) == 1
    assert body["keys_failed"][0]["email"] == "fail@b.com"
    # User row always deleted
    mock_service_data.data_service.users.delete.assert_called_once()
    mock_service_data.cache_service.users.delete.assert_called_once()
    # Only the successful key row deleted; failed one kept
    assert mock_service_data.data_service.keys.delete.call_count == 1


@pytest.mark.asyncio
async def test_delete_key_audits_before_db_cleanup(api_client, mock_service_data):
    """Аудит пишется сразу после XUI-успеха — если DB/cache cleanup падает,
    деструктивная panel-операция всё равно остаётся в журнале."""
    key = make_key(email="ok@b.com")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.data_service.keys.delete = AsyncMock(side_effect=RuntimeError("db down"))
    mock_service_data.cache_service.keys.delete = AsyncMock()

    xui = MagicMock()
    xui.delete_client = AsyncMock(return_value=True)

    mock_audit = MagicMock()
    mock_audit.record = AsyncMock()

    _override_principal(AdminPrincipal(admin_tg_id=42))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)), \
         patch("api.v1.admin.AuditLogger", return_value=mock_audit):
        with pytest.raises(RuntimeError):
            await api_client.post("/api/v1/admin/keys/ok@b.com/delete")

    # DB cleanup raised, but audit was recorded BEFORE the raise — the
    # destructive panel op is captured in the journal regardless.
    mock_audit.record.assert_awaited_once_with(42, "delete_key", "ok@b.com")


@pytest.mark.asyncio
async def test_delete_user_audits_before_user_row_delete(api_client, mock_service_data):
    """Аудит пишется до users.delete — если удаление user-строки падает,
    деструктивные XUI-операции по ключам уже в журнале."""
    user = MagicMock(tg_id=123)
    key_ok = make_key(email="ok@b.com", inbound_id=1, client_id="c1")
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.get_by = AsyncMock(return_value=[key_ok])
    mock_service_data.data_service.keys.delete = AsyncMock()
    mock_service_data.data_service.users.delete = AsyncMock(side_effect=RuntimeError("db down"))
    mock_service_data.cache_service.keys.delete = AsyncMock()
    mock_service_data.cache_service.users.delete = AsyncMock()

    xui = MagicMock()
    xui.delete_client = AsyncMock(return_value=True)

    mock_audit = MagicMock()
    mock_audit.record = AsyncMock()

    _override_principal(AdminPrincipal(admin_tg_id=42))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)), \
         patch("api.v1.admin.AuditLogger", return_value=mock_audit):
        with pytest.raises(RuntimeError):
            await api_client.post("/api/v1/admin/users/123/delete")

    # users.delete raised, but audit was recorded BEFORE the raise — the
    # destructive per-key XUI ops are captured in the journal regardless.
    mock_audit.record.assert_awaited_once_with(42, "delete_user", "123")


@pytest.mark.asyncio
async def test_delete_user_no_keys(api_client, mock_service_data):
    """delete_user with no keys: deleted_user=True, keys_deleted=0, keys_failed=[]."""
    user = MagicMock(tg_id=123)
    mock_service_data.users.get_data = AsyncMock(return_value=user)
    mock_service_data.keys.get_by = AsyncMock(return_value=[])
    mock_service_data.data_service.users.delete = AsyncMock()
    mock_service_data.cache_service.users.delete = AsyncMock()

    xui = MagicMock()
    xui.delete_client = AsyncMock()

    _override_principal(AdminPrincipal(admin_tg_id=1))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)):
        resp = await api_client.post("/api/v1/admin/users/123/delete")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_user"] is True
    assert body["keys_deleted"] == 0
    assert body["keys_failed"] == []
    mock_service_data.data_service.users.delete.assert_called_once()

@pytest.mark.asyncio
async def test_change_key_date_audits_before_resetter(api_client, mock_service_data):
    """Аудит change-date пишется сразу после XUI+DB-мутации — если resetter
    падает, операция на панели уже совершена и остаётся в журнале."""
    key = make_key(email="ok@b.com")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.keys.update = AsyncMock()

    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=True)

    mock_audit = MagicMock()
    mock_audit.record = AsyncMock()

    _override_principal(AdminPrincipal(admin_tg_id=42))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)), \
         patch("api.v1.admin.AuditLogger", return_value=mock_audit), \
         patch("api.v1.admin.KeyResetter") as ResetterCls:
        ResetterCls.return_value.reset_key_after_renewal = AsyncMock(
            side_effect=RuntimeError("resetter boom")
        )
        with pytest.raises(RuntimeError):
            await api_client.post(
                "/api/v1/admin/keys/ok@b.com/change-date",
                json={"expiry_time": 1234},
            )

    mock_audit.record.assert_awaited_once_with(42, "change_date", "ok@b.com")


@pytest.mark.asyncio
async def test_change_key_tariff_audits_before_resetter(api_client, mock_service_data):
    """Аудит change-tariff пишется сразу после XUI+DB-мутации — если resetter
    падает, операция на панели уже совершена и остаётся в журнале."""
    key = make_key(email="ok@b.com")
    mock_service_data.keys.get_data = AsyncMock(return_value=key)
    mock_service_data.keys.update = AsyncMock()
    mock_service_data.tariffs.get_data = AsyncMock(
        return_value=MagicMock(id=5, limit_ip=2, name_tariff="pro")
    )

    xui = MagicMock()
    xui.extend_client_key = AsyncMock(return_value=True)

    mock_audit = MagicMock()
    mock_audit.record = AsyncMock()

    _override_principal(AdminPrincipal(admin_tg_id=42))
    with patch("api.v1.admin.build_key_services", return_value=(None, None, xui)), \
         patch("api.v1.admin.AuditLogger", return_value=mock_audit), \
         patch("api.v1.admin.KeyResetter") as ResetterCls:
        ResetterCls.return_value.reset_key_after_renewal = AsyncMock(
            side_effect=RuntimeError("resetter boom")
        )
        with pytest.raises(RuntimeError):
            await api_client.post(
                "/api/v1/admin/keys/ok@b.com/change-tariff",
                json={"tariff_id": 5},
            )

    mock_audit.record.assert_awaited_once_with(42, "change_tariff", "ok@b.com")
