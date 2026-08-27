import { api } from "../api.js";
import { renderPaginatedTable } from "../components/table.js";
import { renderKeyActions } from "./key_actions.js";

export async function render(container) {
  container.innerHTML = "<h1>Ключи</h1>";

  const card = document.createElement("div");
  card.className = "card";
  container.appendChild(card);

  await renderPaginatedTable(card, {
    columns: [
      { label: "Email", render: (k) => { const s = document.createElement("span"); s.className = "mono"; s.textContent = k.email; return s; } },
      { label: "tg_id", key: "tg_id" },
      { label: "Тариф", key: "name_tariff" },
      { label: "Истекает", render: (k) => new Date(k.expiry_time).toLocaleString() },
      { label: "Трафик", render: (k) => (k.used_traffic ?? 0).toFixed(2) },
    ],
    fetchPage: async ({ limit, offset }) => {
      const res = await api.keys.list({ limit, offset });
      return { data: res.data.keys, totalCount: res.totalCount };
    },
    rowActions: (key, reload) => renderKeyActions(key.email, reload),
  });
}
