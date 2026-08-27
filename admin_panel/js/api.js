import { getCredentials, logout } from "./auth.js";
import { toast } from "./components/toast.js";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

function formatDetail(data) {
  if (data == null) return null;
  const detail = data.detail;
  if (detail == null) return null;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((e) => e.msg || JSON.stringify(e)).join("; ");
  }
  if (typeof detail === "object") {
    return detail.detail ? String(detail.detail) : JSON.stringify(detail);
  }
  return String(detail);
}

async function request(path, { method = "GET", body, query, destructive = false } = {}) {
  const { apiKey, adminTgId } = getCredentials();
  const headers = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;
  if (destructive && adminTgId) headers["X-Admin-Tg-Id"] = String(adminTgId);

  const url = new URL(BASE + path, location.origin);
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
    });
  }

  let res;
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    toast.error("Сеть недоступна: не удалось выполнить запрос");
    throw new ApiError(0, "Network error");
  }

  if (res.status === 401) {
    logout();
    toast.error("Сессия недействительна — войдите заново");
    location.hash = "#/login";
    throw new ApiError(401, "Unauthorized");
  }

  if (!res.ok) {
    let detail = res.statusText;
    let payload = null;
    try {
      payload = await res.json();
      detail = formatDetail(payload) || detail;
    } catch {
      /* no JSON body */
    }
    if (res.status === 403) toast.error(`Forbidden: ${detail}`);
    throw new ApiError(res.status, detail, payload);
  }

  if (res.status === 204) return null;

  const totalCount = res.headers.get("X-Total-Count");
  const data = await res.json();
  return totalCount != null ? { data, totalCount: Number(totalCount) } : data;
}

const PAGE_SIZE = 25;

export const api = {
  stats: () => request("/admin/stats"),
  dashboardMetrics: () => request("/admin/dashboard-metrics"),
  graceBonusStats: () => request("/admin/grace-bonus-stats"),
  schedulerStatus: () => request("/admin/scheduler/status"),

  maintenanceMode: {
    get: () => request("/admin/maintenance-mode"),
    set: (enabled, reason) =>
      request("/admin/maintenance-mode", { method: "POST", body: { enabled, reason }, destructive: true }),
  },

  users: {
    list: ({ limit = PAGE_SIZE, offset = 0 } = {}) =>
      request("/admin/users", { query: { limit, offset } }),
    get: (tgId) => request(`/admin/users/${tgId}`),
    stock: (tgId) => request(`/admin/users/${tgId}/stock`),
    update: (tgId, body) =>
      request(`/admin/users/${tgId}`, { method: "PATCH", body, destructive: true }),
    delete: (tgId) =>
      request(`/admin/users/${tgId}/delete`, { method: "POST", destructive: true }),
    inactive: {
      list: () => request("/admin/users/inactive"),
      deleteAll: () =>
        request("/admin/users/inactive/delete", { method: "POST", destructive: true }),
    },
  },

  keys: {
    list: ({ limit = PAGE_SIZE, offset = 0 } = {}) =>
      request("/admin/keys", { query: { limit, offset } }),
    delete: (email) =>
      request(`/admin/keys/${encodeURIComponent(email)}/delete`, { method: "POST", destructive: true }),
    generate: (body) =>
      request("/admin/keys/generate", { method: "POST", body, destructive: true }),
    massRenew: (body) =>
      request("/admin/keys/mass-renew", { method: "POST", body, destructive: true }),
    changeDate: (email, expiryTime) =>
      request(`/admin/keys/${encodeURIComponent(email)}/change-date`, {
        method: "POST",
        body: { expiry_time: expiryTime },
        destructive: true,
      }),
    changeTariff: (email, tariffId) =>
      request(`/admin/keys/${encodeURIComponent(email)}/change-tariff`, {
        method: "POST",
        body: { tariff_id: tariffId },
        destructive: true,
      }),
    panelMeta: (email, body) =>
      request(`/admin/keys/${encodeURIComponent(email)}/panel-meta`, {
        method: "POST",
        body,
        destructive: true,
      }),
  },

  payments: {
    list: ({ limit = PAGE_SIZE, offset = 0 } = {}) =>
      request("/admin/payments", { query: { limit, offset } }),
  },

  gifts: {
    list: (senderTgId) => request("/admin/gifts", { query: { sender_tg_id: senderTgId } }),
    get: (token) => request(`/admin/gifts/${encodeURIComponent(token)}`),
  },

  tariffs: {
    list: () => request("/admin/tariffs"),
    get: (id) => request(`/admin/tariffs/${id}`),
  },

  referrals: {
    getLink: (tgId) => request(`/admin/referrals/links/${tgId}`),
    createLink: (tgId) => request("/admin/referrals/links", { method: "POST", query: { tg_id: tgId } }),
    getLinkByToken: (token) => request(`/admin/referrals/links/by-token/${encodeURIComponent(token)}`),
    stats: (tgId) => request(`/admin/referrals/stats/${tgId}`),
  },

  sync: {
    start: () => request("/admin/sync", { method: "POST", destructive: true }),
    status: (jobId) => request(`/admin/sync/${encodeURIComponent(jobId)}`, { destructive: true }),
  },

  apiClients: {
    list: () => request("/admin/api-clients", { destructive: true }),
    create: (name, scopes) =>
      request("/admin/api-clients", { method: "POST", body: { name, scopes }, destructive: true }),
    revoke: (id) => request(`/admin/api-clients/${id}/revoke`, { method: "POST", destructive: true }),
    rotate: (id) => request(`/admin/api-clients/${id}/rotate`, { method: "POST", destructive: true }),
  },
};
