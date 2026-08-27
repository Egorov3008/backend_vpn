import { api } from "../api.js";
import { toast } from "../components/toast.js";
import { confirmAction } from "../components/modal.js";

export async function deleteKey(email, reload) {
  const ok = await confirmAction({
    title: `Удалить ключ ${email}?`,
    message: "Ключ будет удалён из панели 3x-ui и БД. Это необратимо.",
    confirmLabel: "Удалить",
  });
  if (!ok) return;
  try {
    await api.keys.delete(email);
    toast.success(`Ключ ${email} удалён`);
    reload();
  } catch (e) {
    toast.error(`Не удалось удалить: ${e.message}`);
  }
}

export async function changeDate(email, reload) {
  const dateStr = window.prompt("Новая дата истечения (YYYY-MM-DD):");
  if (!dateStr) return;
  const ts = Date.parse(dateStr);
  if (Number.isNaN(ts)) {
    toast.error("Некорректная дата");
    return;
  }
  const ok = await confirmAction({
    title: `Изменить дату истечения ${email}?`,
    message: `Новая дата: ${new Date(ts).toLocaleDateString()}`,
    confirmLabel: "Изменить",
    danger: false,
  });
  if (!ok) return;
  try {
    await api.keys.changeDate(email, ts);
    toast.success("Дата истечения изменена");
    reload();
  } catch (e) {
    toast.error(`Не удалось изменить дату: ${e.message}`);
  }
}

export async function changeTariff(email, reload) {
  const tariffIdStr = window.prompt("ID нового тарифа:");
  if (!tariffIdStr) return;
  const tariffId = parseInt(tariffIdStr, 10);
  if (Number.isNaN(tariffId)) {
    toast.error("Некорректный ID тарифа");
    return;
  }
  const ok = await confirmAction({
    title: `Изменить тариф ключа ${email}?`,
    message: `Новый тариф: ${tariffId}`,
    confirmLabel: "Изменить",
    danger: false,
  });
  if (!ok) return;
  try {
    await api.keys.changeTariff(email, tariffId);
    toast.success("Тариф изменён");
    reload();
  } catch (e) {
    toast.error(`Не удалось изменить тариф: ${e.message}`);
  }
}

export async function panelMeta(email, reload) {
  const group = window.prompt("Группа в панели (оставьте пустым, чтобы не менять):") || undefined;
  const comment = window.prompt("Комментарий в панели (оставьте пустым, чтобы не менять):") || undefined;
  if (!group && !comment) return;
  const ok = await confirmAction({
    title: `Обновить метаданные ключа ${email} в панели?`,
    message: `group: ${group ?? "(без изменений)"}, comment: ${comment ?? "(без изменений)"}`,
    confirmLabel: "Обновить",
    danger: false,
  });
  if (!ok) return;
  try {
    await api.keys.panelMeta(email, { group, comment });
    toast.success("Метаданные обновлены");
    reload();
  } catch (e) {
    toast.error(`Не удалось обновить: ${e.message}`);
  }
}

export function renderKeyActions(email, reload) {
  const wrap = document.createElement("div");
  wrap.className = "row";

  const mk = (label, fn) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.addEventListener("click", () => fn(email, reload));
    return b;
  };

  wrap.append(
    mk("Дата", changeDate),
    mk("Тариф", changeTariff),
    mk("Meta", panelMeta),
  );
  const delBtn = mk("Удалить", deleteKey);
  delBtn.className = "danger";
  wrap.appendChild(delBtn);

  return wrap;
}
