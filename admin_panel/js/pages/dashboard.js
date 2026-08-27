import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

function statCard(label, value) {
  const d = document.createElement("div");
  d.className = "stat";
  d.innerHTML = `<div class="label"></div><div class="value"></div>`;
  d.querySelector(".label").textContent = label;
  d.querySelector(".value").textContent = value;
  return d;
}

export async function render(container) {
  container.innerHTML = "<h1>Дашборд</h1><p class=\"muted\">Загрузка…</p>";

  let stats, metrics, grace, scheduler, maintenance;
  try {
    [stats, metrics, grace, scheduler, maintenance] = await Promise.all([
      api.stats(),
      api.dashboardMetrics(),
      api.graceBonusStats(),
      api.schedulerStatus(),
      api.maintenanceMode.get(),
    ]);
  } catch (e) {
    container.innerHTML = `<h1>Дашборд</h1><p class="error-text">Не удалось загрузить данные: ${e.message}</p>`;
    return;
  }

  container.innerHTML = "<h1>Дашборд</h1>";

  const maintCard = document.createElement("div");
  maintCard.className = "card";
  maintCard.innerHTML = `
    <h3 style="margin-top:0">Maintenance mode</h3>
    <p>
      Статус: <span class="badge ${maintenance.enabled ? "warn" : "on"}">${maintenance.enabled ? "включён" : "выключен"}</span>
      ${maintenance.reason ? `<span class="muted"> — ${maintenance.reason}</span>` : ""}
    </p>
    <button id="toggleMaint">${maintenance.enabled ? "Выключить" : "Включить"}</button>
  `;
  container.appendChild(maintCard);

  maintCard.querySelector("#toggleMaint").addEventListener("click", async () => {
    const enabling = !maintenance.enabled;
    const ok = await confirmAction({
      title: enabling ? "Включить maintenance mode?" : "Выключить maintenance mode?",
      message: enabling
        ? "Пока режим включён, продления ключей и оплата новых будут заблокированы (503)."
        : "Продления и оплата снова станут доступны.",
      confirmLabel: enabling ? "Включить" : "Выключить",
      danger: enabling,
    });
    if (!ok) return;
    try {
      await api.maintenanceMode.set(enabling, enabling ? "Включено из admin-panel" : null);
      toast.success(enabling ? "Maintenance mode включён" : "Maintenance mode выключен");
      render(container);
    } catch (e) {
      toast.error(`Не удалось изменить режим: ${e.message}`);
    }
  });

  const grid = document.createElement("div");
  grid.className = "grid";
  grid.append(
    statCard("Пользователей всего", stats.total_users),
    statCard("Ключей всего", stats.total),
    statCard("Активных ключей", stats.active),
    statCard("Триал", stats.trial),
    statCard("Истекают за 24ч", stats.expiring_24h),
    statCard("Истекают за 7д", stats.expiring_7d),
    statCard("Истекают за 30д", stats.expiring_30d),
    statCard("Неиспользуемых", stats.unused),
    statCard("Истёкших", stats.expired),
  );
  container.appendChild(grid);

  const mrrCard = document.createElement("div");
  mrrCard.className = "card";
  mrrCard.innerHTML = `<h3 style="margin-top:0">Доход</h3>`;
  const mrrGrid = document.createElement("div");
  mrrGrid.className = "grid";
  mrrGrid.append(
    statCard("MRR текущий месяц", `${metrics.mrr_current_month.toFixed(0)} ₽`),
    statCard("MRR прошлый месяц", `${metrics.mrr_previous_month.toFixed(0)} ₽`),
    statCard("Рост MRR", `${metrics.mrr_growth.toFixed(1)}%`),
    statCard("Платящих сейчас", metrics.paying_users_current),
    statCard("ARPU", `${metrics.arpu_current.toFixed(0)} ₽`),
    statCard("Конверсия в ключ", `${metrics.conversion_to_keys_pct.toFixed(1)}%`),
    statCard("Конверсия в оплату", `${metrics.conversion_to_paid_pct.toFixed(1)}%`),
    statCard("Успешных платежей", `${metrics.succeeded_pct.toFixed(1)}%`),
  );
  mrrCard.appendChild(mrrGrid);
  container.appendChild(mrrCard);

  const graceCard = document.createElement("div");
  graceCard.className = "card";
  graceCard.innerHTML = `<h3 style="margin-top:0">Канальный бонус</h3>`;
  const graceGrid = document.createElement("div");
  graceGrid.className = "grid";
  graceGrid.append(
    statCard("Всего", grace.channel_bonus.cumulative),
    statCard("Сегодня", grace.channel_bonus.today),
    statCard("Вчера", grace.channel_bonus.yesterday),
  );
  graceCard.appendChild(graceGrid);
  container.appendChild(graceCard);

  const schedCard = document.createElement("div");
  schedCard.className = "card";
  schedCard.innerHTML = `
    <h3 style="margin-top:0">Планировщик</h3>
    <p>Контейнер: <span class="badge ${scheduler.container_alive ? "on" : "warn"}">${scheduler.container_alive ? "жив" : "недоступен"}</span></p>
    <p class="muted">Пользователей: ${scheduler.users}, заблокировано: ${scheduler.blocked}, ключей: ${scheduler.keys}</p>
  `;
  const segList = document.createElement("p");
  segList.className = "muted";
  segList.textContent = Object.entries(scheduler.segment_counts)
    .map(([k, v]) => `${k}: ${v}`)
    .join(" · ");
  schedCard.appendChild(segList);
  container.appendChild(schedCard);
}
