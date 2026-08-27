export async function renderPaginatedTable(container, { columns, fetchPage, pageSize = 25, rowActions, emptyText = "Пусто" }) {
  let offset = 0;

  const wrap = document.createElement("div");
  const tableHost = document.createElement("div");
  const pager = document.createElement("div");
  pager.className = "pager";
  wrap.append(tableHost, pager);
  container.appendChild(wrap);

  async function load() {
    tableHost.innerHTML = '<p class="muted">Загрузка…</p>';
    let result;
    try {
      result = await fetchPage({ limit: pageSize, offset });
    } catch (e) {
      tableHost.innerHTML = `<p class="error-text">Не удалось загрузить: ${e.message}</p>`;
      return;
    }
    const rows = result.data ?? result;
    const total = result.totalCount ?? rows.length;
    renderTable(rows, total);
  }

  function renderTable(rows, total) {
    tableHost.innerHTML = "";
    if (!rows.length) {
      tableHost.innerHTML = `<p class="muted">${emptyText}</p>`;
    } else {
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headRow = document.createElement("tr");
      columns.forEach((c) => {
        const th = document.createElement("th");
        th.textContent = c.label;
        headRow.appendChild(th);
      });
      if (rowActions) headRow.appendChild(document.createElement("th"));
      thead.appendChild(headRow);

      const tbody = document.createElement("tbody");
      rows.forEach((row) => {
        const tr = document.createElement("tr");
        columns.forEach((c) => {
          const td = document.createElement("td");
          const val = c.render ? c.render(row) : row[c.key];
          if (val instanceof Node) td.appendChild(val);
          else td.textContent = val ?? "";
          tr.appendChild(td);
        });
        if (rowActions) {
          const td = document.createElement("td");
          const node = rowActions(row, () => load());
          if (node) td.appendChild(node);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      });

      table.append(thead, tbody);
      tableHost.appendChild(table);
    }

    pager.innerHTML = "";
    const from = total === 0 ? 0 : offset + 1;
    const to = Math.min(offset + pageSize, total);
    const info = document.createElement("span");
    info.textContent = `Показано ${from}-${to} из ${total}`;

    const prevBtn = document.createElement("button");
    prevBtn.textContent = "← Назад";
    prevBtn.disabled = offset === 0;
    prevBtn.addEventListener("click", () => {
      offset = Math.max(0, offset - pageSize);
      load();
    });

    const nextBtn = document.createElement("button");
    nextBtn.textContent = "Вперёд →";
    nextBtn.disabled = offset + pageSize >= total;
    nextBtn.addEventListener("click", () => {
      offset += pageSize;
      load();
    });

    pager.append(prevBtn, info, nextBtn);
  }

  await load();
  return { reload: load };
}
