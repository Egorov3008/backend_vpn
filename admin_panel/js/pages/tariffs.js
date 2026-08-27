import { api } from "../api.js";

export async function render(container) {
  container.innerHTML = "<h1>Тарифы</h1><p class=\"muted\">Загрузка…</p>";

  let tariffs;
  try {
    tariffs = (await api.tariffs.list()).tariffs;
  } catch (e) {
    container.innerHTML = `<h1>Тарифы</h1><p class="error-text">${e.message}</p>`;
    return;
  }

  container.innerHTML = "<h1>Тарифы</h1>";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <table>
      <thead><tr><th>ID</th><th>Название</th><th>Цена</th><th>Период (дн.)</th><th>Трафик (ГБ)</th><th>Лимит IP</th></tr></thead>
      <tbody>${tariffs.map((t) => `
        <tr>
          <td>${t.id}</td>
          <td>${t.name_tariff}</td>
          <td>${t.amount} ₽</td>
          <td>${t.period}</td>
          <td>${t.traffic_limit || "∞"}</td>
          <td>${t.limit_ip}</td>
        </tr>
      `).join("")}</tbody>
    </table>
  `;
  container.appendChild(card);
}
