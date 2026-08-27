import { api } from "../api.js";
import { toast } from "../components/toast.js";

export async function render(container) {
  container.innerHTML = `
    <h1>Подарки</h1>
    <div class="card">
      <div class="row">
        <div class="field" style="margin-bottom:0">
          <label>Фильтр по sender_tg_id (опционально)</label>
          <input id="senderFilter" type="number" />
        </div>
        <button id="filterBtn" style="margin-top:18px">Применить</button>
      </div>
    </div>
    <div class="card">
      <div class="row">
        <div class="field" style="margin-bottom:0">
          <label>Поиск по токену</label>
          <input id="tokenLookup" type="text" />
        </div>
        <button id="lookupBtn" style="margin-top:18px">Найти</button>
      </div>
      <div id="lookupResult"></div>
    </div>
    <div id="listBox" class="card"><p class="muted">Загрузка…</p></div>
  `;

  async function loadList(senderTgId) {
    const listBox = container.querySelector("#listBox");
    listBox.innerHTML = '<p class="muted">Загрузка…</p>';
    try {
      const res = await api.gifts.list(senderTgId || undefined);
      if (!res.gifts.length) {
        listBox.innerHTML = '<p class="muted">Подарков не найдено</p>';
        return;
      }
      listBox.innerHTML = `
        <table>
          <thead><tr><th>Токен</th><th>Отправитель</th><th>Тариф</th><th>Получатель</th><th>Использован</th></tr></thead>
          <tbody>${res.gifts.map((g) => `
            <tr>
              <td class="mono">${g.token}</td>
              <td>${g.sender_tg_id}</td>
              <td>${g.tariff_id}</td>
              <td>${g.recipient_tg_id ?? "—"}</td>
              <td>${g.used_at ? new Date(g.used_at).toLocaleString() : "нет"}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;
    } catch (e) {
      listBox.innerHTML = `<p class="error-text">${e.message}</p>`;
    }
  }

  container.querySelector("#filterBtn").addEventListener("click", () => {
    const v = container.querySelector("#senderFilter").value;
    loadList(v ? parseInt(v, 10) : undefined);
  });

  container.querySelector("#lookupBtn").addEventListener("click", async () => {
    const token = container.querySelector("#tokenLookup").value.trim();
    const box = container.querySelector("#lookupResult");
    if (!token) return;
    box.innerHTML = '<p class="muted">Поиск…</p>';
    try {
      const g = await api.gifts.get(token);
      box.innerHTML = `
        <p>Отправитель: ${g.sender_tg_id}, тариф: ${g.tariff_id}</p>
        <p>Получатель: ${g.recipient_tg_id ?? "—"} (${g.recipient_email ?? "—"})</p>
        <p>Использован: ${g.used_at ? new Date(g.used_at).toLocaleString() : "нет"}</p>
      `;
    } catch (e) {
      box.innerHTML = `<p class="error-text">${e.message}</p>`;
      toast.error("Подарок не найден");
    }
  });

  await loadList();
}
