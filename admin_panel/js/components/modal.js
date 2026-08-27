export function confirmAction({ title, message, confirmLabel = "Подтвердить", danger = true }) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    root.innerHTML = "";

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";

    const box = document.createElement("div");
    box.className = "modal-box";

    const h = document.createElement("h3");
    h.textContent = title;

    const p = document.createElement("p");
    p.className = "muted";
    p.style.whiteSpace = "pre-line";
    p.textContent = message;

    const actions = document.createElement("div");
    actions.className = "modal-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.textContent = "Отмена";

    const confirmBtn = document.createElement("button");
    confirmBtn.className = danger ? "danger" : "primary";
    confirmBtn.textContent = confirmLabel;

    function close(result) {
      root.innerHTML = "";
      resolve(result);
    }

    cancelBtn.addEventListener("click", () => close(false));
    confirmBtn.addEventListener("click", () => close(true));
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close(false);
    });

    actions.append(cancelBtn, confirmBtn);
    box.append(h, p, actions);
    overlay.append(box);
    root.append(overlay);
  });
}

export function infoModal({ title, bodyNode, closeLabel = "Закрыть" }) {
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    root.innerHTML = "";

    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";

    const box = document.createElement("div");
    box.className = "modal-box";

    const h = document.createElement("h3");
    h.textContent = title;

    const actions = document.createElement("div");
    actions.className = "modal-actions";

    const closeBtn = document.createElement("button");
    closeBtn.className = "primary";
    closeBtn.textContent = closeLabel;

    function close() {
      root.innerHTML = "";
      resolve();
    }

    closeBtn.addEventListener("click", close);

    actions.append(closeBtn);
    box.append(h, bodyNode, actions);
    overlay.append(box);
    root.append(overlay);
  });
}
