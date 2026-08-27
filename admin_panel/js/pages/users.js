import { api } from "../api.js";
import { renderPaginatedTable } from "../components/table.js";

export async function render(container) {
  container.innerHTML = "<h1>Пользователи</h1>";

  const card = document.createElement("div");
  card.className = "card";
  container.appendChild(card);

  await renderPaginatedTable(card, {
    columns: [
      {
        label: "tg_id",
        render: (u) => {
          const a = document.createElement("a");
          a.href = `#/users/${u.tg_id}`;
          a.textContent = u.tg_id;
          return a;
        },
      },
      { label: "Username", key: "username" },
      { label: "Имя", key: "first_name" },
      { label: "Баланс", render: (u) => u.balance.toFixed(2) },
      { label: "Trial", key: "trial" },
      { label: "Сервер", key: "server_id" },
      {
        label: "Флаги",
        render: (u) => {
          const parts = [];
          if (u.is_admin) parts.push("admin");
          if (u.is_blocked) parts.push("blocked");
          return parts.join(", ") || "—";
        },
      },
    ],
    fetchPage: ({ limit, offset }) => api.users.list({ limit, offset }),
  });
}
