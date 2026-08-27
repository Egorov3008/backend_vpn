import { logout } from "../auth.js";

const SECTIONS = [
  { hash: "#/dashboard", label: "Дашборд" },
  { hash: "#/users", label: "Пользователи" },
  { hash: "#/users-inactive", label: "Неактивные пользователи" },
  { hash: "#/keys", label: "Ключи" },
  { hash: "#/keys/generate", label: "Выдать ключ" },
  { hash: "#/keys/mass-renew", label: "Массовое продление" },
  { hash: "#/payments", label: "Платежи" },
  { hash: "#/gifts", label: "Подарки" },
  { hash: "#/tariffs", label: "Тарифы" },
  { hash: "#/referrals", label: "Рефералы" },
  { hash: "#/sync", label: "Синхронизация" },
  { hash: "#/api-clients", label: "API-клиенты" },
];

export function renderNav(currentHash) {
  const sidebar = document.getElementById("sidebar");

  if (currentHash === "#/login") {
    sidebar.classList.add("hidden");
    return;
  }
  sidebar.classList.remove("hidden");
  sidebar.innerHTML = "";

  const brand = document.createElement("div");
  brand.className = "brand";
  brand.textContent = "VPN Platform · Admin";
  sidebar.appendChild(brand);

  const [currentBase] = currentHash.split("?");

  SECTIONS.forEach((s) => {
    const a = document.createElement("a");
    a.href = s.hash;
    a.textContent = s.label;
    if (currentBase === s.hash || currentBase.startsWith(s.hash + "/")) {
      a.classList.add("active");
    }
    sidebar.appendChild(a);
  });

  const mailing = document.createElement("a");
  mailing.className = "disabled";
  mailing.title = "Рассылка доступна только из Telegram-бота — у backend нет эндпоинта для broadcast";
  mailing.textContent = "Рассылка (только в боте)";
  sidebar.appendChild(mailing);

  const logoutLink = document.createElement("div");
  logoutLink.className = "logout";
  logoutLink.textContent = "Выйти";
  logoutLink.addEventListener("click", () => {
    logout();
    location.hash = "#/login";
  });
  sidebar.appendChild(logoutLink);
}
