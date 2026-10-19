import { apiFetch } from "./http.js";

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

export const GROUP_ORDER = [];

export const GROUP_LABELS = {};

export function currentLayoutSlug(layoutName) {
  const raw = String(layoutName || location.hash.replace(/^#/, "") || "").split("/").pop();
  if (!raw) return "";
  return raw.endsWith(".yaml") ? raw : `${raw}.yaml`;
}

export function catalogForGroup(catalog, group) {
  const rows = Array.isArray(catalog) ? catalog : [];
  const wanted = String(group || "").trim();
  if (!wanted) return rows;
  return rows.filter((row) => String(row?.group || "") === wanted);
}

export function catalogSections(catalog) {
  const rows = Array.isArray(catalog) ? catalog : [];
  const buckets = new Map();
  for (const row of rows) {
    const group = String(row?.group || "other");
    if (!buckets.has(group)) buckets.set(group, []);
    buckets.get(group).push(row);
  }
  const extra = [...buckets.keys()].filter((group) => !GROUP_ORDER.includes(group)).sort();
  return [...GROUP_ORDER, ...extra]
    .filter((group) => buckets.has(group))
    .map((group) => ({
      group,
      label: GROUP_LABELS[group] || group,
      rows: buckets.get(group),
    }));
}

export const layoutPickerPlugin = {
  id: "layout-picker",
  kind: "chrome",
  windowed: false,
  mount(container, ctx) {
    const wrap = el("div", "layout-picker");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "layout-picker-btn";
    button.setAttribute("aria-label", "Choose demo layout");
    button.setAttribute("aria-haspopup", "listbox");
    button.innerHTML =
      '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M2 3h12v2H2zm0 4h12v2H2zm0 4h12v2H2z"/></svg>';
    const menu = el("div", "layout-picker-menu");
    menu.hidden = true;
    wrap.append(button, menu);
    container.appendChild(wrap);

    const mine = currentLayoutSlug(ctx.layoutName);

    function close() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    function open() {
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
    }

    function addItem(row) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "layout-picker-item";
      item.setAttribute("role", "option");
      if (row.slug === mine) item.classList.add("active");
      const title = el("span", "layout-picker-item-title");
      title.textContent = row.title || row.slug;
      title.title = row.title || row.slug;
      const slug = el("span", "layout-picker-item-slug");
      slug.textContent = row.slug;
      item.append(title, slug);
      item.addEventListener("click", () => {
        close();
        if (!row.slug || row.slug === mine) return;
        location.hash = row.slug;
      });
      menu.appendChild(item);
    }

    function paint(rows) {
      menu.replaceChildren();
      const home = document.createElement("button");
      home.type = "button";
      home.className = "layout-picker-item";
      home.textContent = "All layouts";
      home.addEventListener("click", () => {
        close();
        location.hash = "";
      });
      menu.appendChild(home);
      const sections = catalogSections(rows);
      for (const section of sections) {
        const head = el("div", "layout-picker-group");
        head.textContent = section.label;
        menu.appendChild(head);
        for (const row of section.rows) addItem(row);
      }
      if (!sections.length) {
        const empty = el("div", "layout-picker-empty");
        empty.textContent = "No layouts";
        menu.appendChild(empty);
      }
    }

    apiFetch("/api/layouts")
      .then((res) => (res.ok ? res.json() : { catalog: [] }))
      .then((data) => {
        paint(data.catalog || (data.layouts || []).map((slug) => ({ slug, title: slug, group: "" })));
      })
      .catch(() => paint([]));

    button.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (menu.hidden) open();
      else close();
    });
    const onDoc = (ev) => {
      if (!wrap.contains(ev.target)) close();
    };
    document.addEventListener("click", onDoc);
    return {
      destroy() {
        document.removeEventListener("click", onDoc);
        wrap.remove();
      },
    };
  },
};
