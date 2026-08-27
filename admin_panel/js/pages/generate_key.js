import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export async function render(container) {
  container.innerHTML = "<h1>Выдать ключ</h1><p class=\"muted\">Загрузка тарифов…</p>";

  let tariffs;
  try {
    tariffs = (await api.tariffs.list()).tariffs;
  } catch (e) {
    container.innerHTML = `<h1>Выдать ключ</h1><p class="error-text">Не удалось загрузить тарифы: ${e.message}</p>`;
    return;
  }

  container.innerHTML = "<h1>Выдать ключ</h1>";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="field">
      <label>tg_id пользователя</label>
      <input id="tgId" type="number" />
    </div>
    <div class="field">
      <label>Тариф</label>
      <select id="tariffId">
        ${tariffs.map((t) => `<option value="${t.id}">${t.name_tariff} (${t.amount} ₽, ${t.period} дн.)</option>`).join("")}
      </select>
    </div>
    <div class="field">
      <label>Сервер</label>
      <input id="serverId" type="number" value="2" />
    </div>
    <div class="field">
      <label>Количество месяцев</label>
      <input id="months" type="number" value="1" />
    </div>
    <button id="genBtn" class="primary">Сгенерировать</button>
    <div id="resultBox" style="margin-top:16px"></div>
  `;
  container.appendChild(card);

  card.querySelector("#genBtn").addEventListener("click", async () => {
    const tgId = parseInt(card.querySelector("#tgId").value, 10);
    const tariffId = parseInt(card.querySelector("#tariffId").value, 10);
    const serverId = parseInt(card.querySelector("#serverId").value, 10);
    const months = parseInt(card.querySelector("#months").value, 10);
    if (!tgId) {
      toast.error("Укажите tg_id");
      return;
    }
    const tariff = tariffs.find((t) => t.id === tariffId);
    const ok = await confirmAction({
      title: `Сгенерировать ключ для tg_id ${tgId}?`,
      message: `Тариф: ${tariff?.name_tariff ?? tariffId}, сервер: ${serverId}, месяцев: ${months}`,
      confirmLabel: "Сгенерировать",
      danger: false,
    });
    if (!ok) return;
    try {
      const result = await api.keys.generate({
        tg_id: tgId,
        tariff_id: tariffId,
        server_id: serverId,
        number_of_months: months,
      });
      toast.success(`Ключ создан: ${result.email}`);
      card.querySelector("#resultBox").innerHTML = `
        <p>Email: <span class="mono">${result.email}</span></p>
        <p>Ссылка для подключения: <span class="mono">${result.link_to_connect}</span></p>
        <p>Публичная ссылка: <span class="mono">${result.public_link}</span></p>
      `;
    } catch (e) {
      toast.error(`Не удалось сгенерировать ключ: ${e.message}`);
    }
  });
}
