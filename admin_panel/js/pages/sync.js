import { api, ApiError } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export function render(container) {
  container.innerHTML = `
    <h1>Синхронизация</h1>
    <div class="card">
      <p class="muted">Полная синхронизация БД/кэша с панелью 3x-ui. Может занять некоторое время.</p>
      <button id="startBtn" class="primary">Запустить синхронизацию</button>
      <div id="statusBox" style="margin-top:16px"></div>
    </div>
  `;

  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function renderStatus(status) {
    const box = container.querySelector("#statusBox");
    box.innerHTML = `
      <p>Статус: <span class="badge ${status.status === "running" ? "warn" : status.status === "failed" ? "warn" : "on"}">${status.status}</span></p>
      ${status.error ? `<p class="error-text">${status.error}</p>` : ""}
      ${status.result ? `<pre class="mono">${JSON.stringify(status.result, null, 2)}</pre>` : ""}
    `;
  }

  function poll(jobId) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const status = await api.sync.status(jobId);
        renderStatus(status);
        if (status.status !== "running") {
          stopPolling();
          toast.success(`Синхронизация завершена: ${status.status}`);
        }
      } catch (e) {
        stopPolling();
        toast.error(`Ошибка поллинга статуса: ${e.message}`);
      }
    }, 2000);
  }

  container.querySelector("#startBtn").addEventListener("click", async () => {
    const ok = await confirmAction({
      title: "Запустить полную синхронизацию сейчас?",
      message: "Это может занять некоторое время. Если синхронизация уже идёт — запрос будет отклонён (409).",
      confirmLabel: "Запустить",
      danger: false,
    });
    if (!ok) return;
    try {
      const result = await api.sync.start();
      toast.success(`Синхронизация запущена: ${result.job_id}`);
      renderStatus({ status: "running" });
      poll(result.job_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.payload?.detail?.job_id) {
        const jobId = e.payload.detail.job_id;
        toast.error("Синхронизация уже выполняется — отслеживаем существующую");
        renderStatus({ status: "running" });
        poll(jobId);
      } else {
        toast.error(`Не удалось запустить синхронизацию: ${e.message}`);
      }
    }
  });
}
