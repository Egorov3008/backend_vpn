const KEY_STORAGE = "admin_panel_api_key";
const TG_ID_STORAGE = "admin_panel_admin_tg_id";

export function getCredentials() {
  return {
    apiKey: localStorage.getItem(KEY_STORAGE) || "",
    adminTgId: localStorage.getItem(TG_ID_STORAGE) || "",
  };
}

export function isAuthenticated() {
  return !!getCredentials().apiKey;
}

export function setCredentials(apiKey, adminTgId) {
  localStorage.setItem(KEY_STORAGE, apiKey);
  if (adminTgId) {
    localStorage.setItem(TG_ID_STORAGE, adminTgId);
  } else {
    localStorage.removeItem(TG_ID_STORAGE);
  }
}

export function logout() {
  localStorage.removeItem(KEY_STORAGE);
  localStorage.removeItem(TG_ID_STORAGE);
}

export async function login(apiKey, adminTgId) {
  const res = await fetch("/api/v1/admin/stats", {
    headers: { "X-API-Key": apiKey },
  });
  if (!res.ok) {
    throw new Error(res.status === 401 ? "Неверный API-ключ" : `Ошибка проверки ключа (${res.status})`);
  }
  setCredentials(apiKey, adminTgId);
}

export function requireAuth() {
  if (!isAuthenticated()) {
    location.hash = "#/login";
    return false;
  }
  return true;
}
