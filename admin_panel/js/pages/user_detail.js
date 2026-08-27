import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export async function render(container, params) {
  const tgId = params.tgId;
  container.innerHTML = `<h1>Пользователь ${tgId}</h1><p class="muted">Загрузка…</p>`;

  let user, stock;
  try {
    [user, stock] = await Promise.all([api.users.get(tgId), api.users.stock(tgId)]);
  } catch (e) {
    container.innerHTML = `<h1>Пользователь ${tgId}</h1><p class="error-text">${e.message}</p>`;
    return;
  }

  container.innerHTML = `<h1>Пользователь ${tgId}</h1>`;

  const infoCard = document.createElement("div");
  infoCard.className = "card";
  infoCard.innerHTML = `
    <p>Username: <span class="mono">${user.username ?? "—"}</span></p>
    <p>Имя: ${user.first_name ?? "—"}</p>
    <p>Создан: ${user.created_at ? new Date(user.created_at).toLocaleString() : "—"}</p>
    <p>Канальный бонус получен: ${user.channel_bonus_claimed ? "да" : "нет"}</p>
    <p>Скидка: ${stock.has_discount ? `${stock.stock_type} · ${stock.value} (активна: ${stock.is_active ? "да" : "нет"})` : "нет"}</p>
  `;
  container.appendChild(infoCard);

  const editCard = document.createElement("div");
  editCard.className = "card";
  editCard.innerHTML = `
    <h3 style="margin-top:0">Редактирование</h3>
    <div class="field">
      <label>Баланс</label>
      <input id="balance" type="number" step="0.01" value="${user.balance}" />
    </div>
    <div class="field">
      <label>Сервер</label>
      <input id="serverId" type="number" value="${user.server_id ?? ""}" />
    </div>
    <div class="field">
      <label>Trial</label>
      <input id="trial" type="number" value="${user.trial}" />
    </div>
    <div class="row" style="margin-bottom:12px">
      <label style="display:flex;align-items:center;gap:6px;margin:0">
        <input id="isBlocked" type="checkbox" style="width:auto" ${user.is_blocked ? "checked" : ""} /> Заблокирован
      </label>
      <label style="display:flex;align-items:center;gap:6px;margin:0">
        <input id="isAdmin" type="checkbox" style="width:auto" ${user.is_admin ? "checked" : ""} /> Админ
      </label>
    </div>
    <div class="row">
      <button id="saveBtn" class="primary">Сохранить</button>
      <button id="deleteBtn" class="danger">Удалить пользователя</button>
    </div>
  `;
  container.appendChild(editCard);

  editCard.querySelector("#saveBtn").addEventListener("click", async () => {
    const body = {
      balance: parseFloat(editCard.querySelector("#balance").value),
      server_id: parseInt(editCard.querySelector("#serverId").value, 10) || null,
      trial: parseInt(editCard.querySelector("#trial").value, 10),
      is_blocked: editCard.querySelector("#isBlocked").checked,
      is_admin: editCard.querySelector("#isAdmin").checked,
    };
    const ok = await confirmAction({
      title: `Сохранить изменения пользователя ${tgId}?`,
      message: `Баланс: ${body.balance}, сервер: ${body.server_id}, trial: ${body.trial}, blocked: ${body.is_blocked}, admin: ${body.is_admin}`,
      confirmLabel: "Сохранить",
      danger: false,
    });
    if (!ok) return;
    try {
      await api.users.update(tgId, body);
      toast.success("Изменения сохранены");
      render(container, params);
    } catch (e) {
      toast.error(`Не удалось сохранить: ${e.message}`);
    }
  });

  editCard.querySelector("#deleteBtn").addEventListener("click", async () => {
    const ok = await confirmAction({
      title: `Удалить пользователя ${tgId}?`,
      message: "Будут удалены пользователь и ВСЕ его ключи (из панели и БД). Это необратимо.",
      confirmLabel: "Удалить",
    });
    if (!ok) return;
    try {
      const result = await api.users.delete(tgId);
      toast.success(`Пользователь удалён. Ключей удалено: ${result.keys_deleted}${result.keys_failed.length ? `, не удалось: ${result.keys_failed.length}` : ""}`);
      location.hash = "#/users";
    } catch (e) {
      toast.error(`Не удалось удалить: ${e.message}`);
    }
  });
}
