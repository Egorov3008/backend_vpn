import { requireAuth } from "./auth.js";
import { renderNav } from "./components/nav.js";

import * as login from "./pages/login.js";
import * as dashboard from "./pages/dashboard.js";
import * as users from "./pages/users.js";
import * as userDetail from "./pages/user_detail.js";
import * as inactiveUsers from "./pages/inactive_users.js";
import * as keys from "./pages/keys.js";
import * as generateKey from "./pages/generate_key.js";
import * as massRenew from "./pages/mass_renew.js";
import * as payments from "./pages/payments.js";
import * as gifts from "./pages/gifts.js";
import * as tariffs from "./pages/tariffs.js";
import * as referrals from "./pages/referrals.js";
import * as sync from "./pages/sync.js";
import * as apiClients from "./pages/api_clients.js";

const ROUTES = [
  { pattern: "#/login", page: login },
  { pattern: "#/dashboard", page: dashboard },
  { pattern: "#/users", page: users },
  { pattern: "#/users/:tgId", page: userDetail },
  { pattern: "#/users-inactive", page: inactiveUsers },
  { pattern: "#/keys", page: keys },
  { pattern: "#/keys/generate", page: generateKey },
  { pattern: "#/keys/mass-renew", page: massRenew },
  { pattern: "#/payments", page: payments },
  { pattern: "#/gifts", page: gifts },
  { pattern: "#/tariffs", page: tariffs },
  { pattern: "#/referrals", page: referrals },
  { pattern: "#/sync", page: sync },
  { pattern: "#/api-clients", page: apiClients },
];

function matchRoute(base) {
  const baseParts = base.split("/").filter(Boolean);
  for (const route of ROUTES) {
    const patternParts = route.pattern.split("/").filter(Boolean);
    if (patternParts.length !== baseParts.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < patternParts.length; i++) {
      const p = patternParts[i];
      if (p.startsWith(":")) {
        params[p.slice(1)] = baseParts[i];
      } else if (p !== baseParts[i]) {
        ok = false;
        break;
      }
    }
    if (ok) return { page: route.page, params };
  }
  return null;
}

function route() {
  const hash = location.hash || "#/dashboard";
  const [base] = hash.split("?");

  if (base !== "#/login" && !requireAuth()) return;

  const matched = matchRoute(base);
  const app = document.getElementById("app");
  app.innerHTML = "";

  renderNav(base);

  if (!matched) {
    app.innerHTML = `<h1>404</h1><p class="muted">Раздел не найден.</p>`;
    return;
  }
  matched.page.render(app, matched.params);
}

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
