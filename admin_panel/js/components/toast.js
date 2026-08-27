function show(message, kind) {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

export const toast = {
  success: (msg) => show(msg, "success"),
  error: (msg) => show(msg, "error"),
};
