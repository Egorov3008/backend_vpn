import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction, infoModal } from "../components/modal.js";

async function showKeyOnce(title, apiKey) {
  const body = document.createElement("div");
  body.innerHTML = `
    <p class="error-text">Этот ключ больше никогда не будет показан — скопируйте его сейчас.</p>
    <div class="field">
      <input type="text" readonly value="${apiKey}" onclick="this.select()" class="mono" />
    </div>
  `;
  await infoModal({ title, bodyNode: body, closeLabel: "Я сохранил ключ" });
}

export async function render(container) {
  container.innerHTML = `
    <h1>API-клиенты</h1>
    <div class="card">
      <h3 style="margin-top:0">Создать клиента</h3>
      <div class="field">
        <label>Имя</label>
        <input id="newName" type="text" />
      </div>
      <div class="field">
        <label>Scopes (через запятую)</label>
        <input id="newScopes" type="text" placeholder="tariffs:read" />
      </div>
      <button id="createBtn" class="primary">Создать</button>
    </div>
    <div id="listBox" class="card"><p class="muted">Загрузка…</p></div>
  `;

  async function loadList() {
    const box = container.querySelector("#listBox");
    box.innerHTML = '<p class="muted">Загрузка…</p>';
    try {
      const res = await api.apiClients.list();
      if (!res.clients.length) {
        box.innerHTML = '<p class="muted">Клиентов нет</p>';
        return;
      }
      box.innerHTML = "";
      const table = document.createElement("table");
      table.innerHTML = `<thead><tr><th>ID</th><th>Имя</th><th>Префикс</th><th>Scopes</th><th>Статус</th><th></th></tr></thead>`;
      const tbody = document.createElement("tbody");
      res.clients.forEach((c) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${c.id}</td>
          <td>${c.name}</td>
          <td class="mono">${c.key_prefix}</td>
          <td>${c.scopes.join(", ") || "—"}</td>
          <td><span class="badge ${c.is_active ? "on" : "off"}">${c.is_active ? "активен" : "отозван"}</span></td>
        `;
        const actionsTd = document.createElement("td");
        const rotateBtn = document.createElement("button");
        rotateBtn.textContent = "Rotate";
        rotateBtn.addEventListener("click", async () => {
          const ok = await confirmAction({
            title: `Ротировать ключ клиента "${c.name}"?`,
            message: "Старый ключ перестанет работать немедленно.",
            confirmLabel: "Ротировать",
          });
          if (!ok) return;
          try {
            const res2 = await api.apiClients.rotate(c.id);
            await showKeyOnce(`Новый ключ для "${c.name}"`, res2.api_key);
            loadList();
          } catch (e) {
            toast.error(`Не удалось ротировать: ${e.message}`);
          }
        });

        const revokeBtn = document.createElement("button");
        revokeBtn.className = "danger";
        revokeBtn.textContent = "Revoke";
        revokeBtn.disabled = !c.is_active;
        revokeBtn.addEventListener("click", async () => {
          const ok = await confirmAction({
            title: `Отозвать клиента "${c.name}"?`,
            message: "Его ключ перестанет работать немедленно.",
            confirmLabel: "Отозвать",
          });
          if (!ok) return;
          try {
            await api.apiClients.revoke(c.id);
            toast.success("Клиент отозван");
            loadList();
          } catch (e) {
            toast.error(`Не удалось отозвать: ${e.message}`);
          }
        });

        actionsTd.className = "row";
        actionsTd.append(rotateBtn, revokeBtn);
        tr.appendChild(actionsTd);
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      box.appendChild(table);
    } catch (e) {
      box.innerHTML = `<p class="error-text">${e.message}</p>`;
    }
  }

  container.querySelector("#createBtn").addEventListener("click", async () => {
    const name = container.querySelector("#newName").value.trim();
    const scopes = container.querySelector("#newScopes").value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!name) {
      toast.error("Укажите имя клиента");
      return;
    }
    const ok = await confirmAction({
      title: `Создать API-клиента "${name}"?`,
      message: `Scopes: ${scopes.join(", ") || "(нет)"}. Ключ будет показан один раз.`,
      confirmLabel: "Создать",
      danger: false,
    });
    if (!ok) return;
    try {
      const res = await api.apiClients.create(name, scopes);
      await showKeyOnce(`Ключ клиента "${name}"`, res.api_key);
      loadList();
    } catch (e) {
      toast.error(`Не удалось создать клиента: ${e.message}`);
    }
  });

  await loadList();
}
