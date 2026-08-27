import { login } from "../auth.js";

export function render(container) {
  container.innerHTML = "";

  const wrap = document.createElement("div");
  wrap.className = "login-wrap card";

  wrap.innerHTML = `
    <h1>Вход</h1>
    <div class="field">
      <label for="apiKey">API Key</label>
      <input id="apiKey" type="password" autocomplete="off" placeholder="X-API-Key" />
    </div>
    <div class="field">
      <label for="adminTgId">Ваш Telegram ID (для аудита)</label>
      <input id="adminTgId" type="text" inputmode="numeric" placeholder="например 123456789" />
    </div>
    <button id="submitBtn" class="primary">Войти</button>
    <p id="errorText" class="error-text" style="display:none"></p>
  `;

  container.appendChild(wrap);

  const apiKeyInput = wrap.querySelector("#apiKey");
  const tgIdInput = wrap.querySelector("#adminTgId");
  const submitBtn = wrap.querySelector("#submitBtn");
  const errorText = wrap.querySelector("#errorText");

  async function submit() {
    const apiKey = apiKeyInput.value.trim();
    const adminTgId = tgIdInput.value.trim();
    errorText.style.display = "none";
    if (!apiKey) {
      errorText.textContent = "Введите API-ключ";
      errorText.style.display = "block";
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = "Проверка…";
    try {
      await login(apiKey, adminTgId);
      location.hash = "#/dashboard";
    } catch (e) {
      errorText.textContent = e.message || "Не удалось войти";
      errorText.style.display = "block";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Войти";
    }
  }

  submitBtn.addEventListener("click", submit);
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });
}
