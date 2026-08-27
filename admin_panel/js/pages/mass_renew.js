import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export function render(container) {
  container.innerHTML = "<h1>Массовое продление</h1>";
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `
    <div class="field" style="max-width:520px">
      <label>Email ключей (по одному на строку)</label>
      <textarea id="emails" rows="8"></textarea>
    </div>
    <div class="field">
      <label>Дней продления</label>
      <input id="days" type="number" value="30" />
    </div>
    <button id="renewBtn" class="primary">Продлить</button>
    <div id="resultBox" style="margin-top:16px"></div>
  `;
  container.appendChild(card);

  card.querySelector("#renewBtn").addEventListener("click", async () => {
    const emails = card.querySelector("#emails").value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    const days = parseInt(card.querySelector("#days").value, 10);
    if (!emails.length) {
      toast.error("Укажите хотя бы один email");
      return;
    }
    const ok = await confirmAction({
      title: `Продлить ${emails.length} ключей на ${days} дней?`,
      message: emails.slice(0, 10).join(", ") + (emails.length > 10 ? `, … ещё ${emails.length - 10}` : ""),
      confirmLabel: "Продлить",
    });
    if (!ok) return;
    try {
      const result = await api.keys.massRenew({ emails, days });
      toast.success(`Успешно: ${result.success}/${result.total}`);
      const box = card.querySelector("#resultBox");
      box.innerHTML = `
        <table>
          <thead><tr><th>Email</th><th>Статус</th><th>Детали</th></tr></thead>
          <tbody>${result.results.map((r) => `
            <tr>
              <td class="mono">${r.email}</td>
              <td>${r.success ? "✅" : "❌"}</td>
              <td>${r.success ? new Date(r.new_expiry).toLocaleDateString() : r.error}</td>
            </tr>
          `).join("")}</tbody>
        </table>
      `;
    } catch (e) {
      toast.error(`Не удалось выполнить продление: ${e.message}`);
    }
  });
}
