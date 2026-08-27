import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export async function render(container) {
  container.innerHTML = "<h1>Неактивные пользователи</h1><p class=\"muted\">Загрузка…</p>";

  let result;
  try {
    result = await api.users.inactive.list();
  } catch (e) {
    container.innerHTML = `<h1>Неактивные пользователи</h1><p class="error-text">${e.message}</p>`;
    return;
  }

  container.innerHTML = "<h1>Неактивные пользователи</h1>";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<p>Заблокированные пользователи без единого ключа: <strong>${result.count}</strong></p>`;
  container.appendChild(card);

  if (result.count > 0) {
    const table = document.createElement("table");
    table.innerHTML = `
      <thead><tr><th>tg_id</th><th>Username</th><th>Создан</th></tr></thead>
      <tbody>${result.users.map((u) => `<tr><td>${u.tg_id}</td><td>${u.username ?? "—"}</td><td>${u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}</td></tr>`).join("")}</tbody>
    `;
    card.appendChild(table);

    const btn = document.createElement("button");
    btn.className = "danger";
    btn.style.marginTop = "12px";
    btn.textContent = `Удалить всех ${result.count}`;
    btn.addEventListener("click", async () => {
      const ok = await confirmAction({
        title: `Удалить всех ${result.count} неактивных пользователей?`,
        message: "Это необратимо.",
        confirmLabel: "Удалить всех",
      });
      if (!ok) return;
      try {
        const res = await api.users.inactive.deleteAll();
        toast.success(`Удалено пользователей: ${res.deleted}`);
        render(container);
      } catch (e) {
        toast.error(`Не удалось удалить: ${e.message}`);
      }
    });
    card.appendChild(btn);
  }
}
