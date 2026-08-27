import { api } from "../api.js";
import { renderPaginatedTable } from "../components/table.js";

export async function render(container) {
  container.innerHTML = "<h1>Платежи</h1>";

  const card = document.createElement("div");
  card.className = "card";
  container.appendChild(card);

  await renderPaginatedTable(card, {
    columns: [
      { label: "ID платежа", render: (p) => { const s = document.createElement("span"); s.className = "mono"; s.textContent = p.payment_id; return s; } },
      { label: "tg_id", key: "tg_id" },
      { label: "Сумма", render: (p) => (p.amount ?? 0).toFixed(2) },
      { label: "Тип", key: "payment_type" },
      { label: "Статус", key: "status" },
      { label: "Создан", render: (p) => p.created_at ? new Date(p.created_at).toLocaleString() : "—" },
    ],
    fetchPage: async ({ limit, offset }) => {
      const res = await api.payments.list({ limit, offset });
      return { data: res.data.payments, totalCount: res.totalCount };
    },
  });
}
