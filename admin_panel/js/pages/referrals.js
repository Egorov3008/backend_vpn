import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export function render(container) {
  container.innerHTML = `
    <h1>Рефералы</h1>
    <div class="card">
      <h3 style="margin-top:0">По tg_id пользователя</h3>
      <div class="field">
        <label>tg_id</label>
        <input id="tgId" type="number" />
      </div>
      <div class="row">
        <button id="lookupBtn">Показать</button>
        <button id="createBtn">Создать/получить ссылку</button>
      </div>
      <div id="tgResult" style="margin-top:12px"></div>
    </div>
    <div class="card">
      <h3 style="margin-top:0">По токену ссылки</h3>
      <div class="field">
        <label>Токен</label>
        <input id="token" type="text" />
      </div>
      <button id="tokenLookupBtn">Показать</button>
      <div id="tokenResult" style="margin-top:12px"></div>
    </div>
  `;

  async function showForTgId() {
    const tgId = parseInt(container.querySelector("#tgId").value, 10);
    const box = container.querySelector("#tgResult");
    if (!tgId) return;
    box.innerHTML = '<p class="muted">Загрузка…</p>';
    try {
      const [link, stats] = await Promise.all([api.referrals.getLink(tgId), api.referrals.stats(tgId)]);
      box.innerHTML = `
        <p>Токен ссылки: ${link.token ? `<span class="mono">${link.token}</span>` : "нет"}</p>
        <p>Приглашено: ${stats.referral_count}</p>
        <p>Наград: ${stats.rewards_count} на сумму ${stats.rewards_total.toFixed(2)}</p>
        <p>Баланс: ${stats.balance.toFixed(2)}</p>
      `;
    } catch (e) {
      box.innerHTML = `<p class="error-text">${e.message}</p>`;
    }
  }

  container.querySelector("#lookupBtn").addEventListener("click", showForTgId);

  container.querySelector("#createBtn").addEventListener("click", async () => {
    const tgId = parseInt(container.querySelector("#tgId").value, 10);
    if (!tgId) {
      toast.error("Укажите tg_id");
      return;
    }
    const ok = await confirmAction({
      title: `Получить/создать реферальную ссылку для ${tgId}?`,
      message: "Если ссылка уже существует — вернётся существующая.",
      confirmLabel: "Продолжить",
      danger: false,
    });
    if (!ok) return;
    try {
      const link = await api.referrals.createLink(tgId);
      toast.success(`Токен: ${link.token}`);
      showForTgId();
    } catch (e) {
      toast.error(`Не удалось: ${e.message}`);
    }
  });

  container.querySelector("#tokenLookupBtn").addEventListener("click", async () => {
    const token = container.querySelector("#token").value.trim();
    const box = container.querySelector("#tokenResult");
    if (!token) return;
    box.innerHTML = '<p class="muted">Загрузка…</p>';
    try {
      const link = await api.referrals.getLinkByToken(token);
      box.innerHTML = `
        <p>Реферер: ${link.referrer_tg_id}</p>
        <p>Создана: ${link.created_at ? new Date(link.created_at).toLocaleString() : "—"}</p>
      `;
    } catch (e) {
      box.innerHTML = `<p class="error-text">${e.message}</p>`;
    }
  });
}
