function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

export function layoutFromHash(hash = typeof location !== "undefined" ? location.hash : "") {
  const raw = String(hash || "")
    .replace(/^#/, "")
    .trim();
  if (!raw || raw === "/") return "";
  const name = raw.split("/").pop() || "";
  if (!name) return "";
  return name.endsWith(".yaml") ? name : `${name}.yaml`;
}

export function catalogRows(data) {
  if (Array.isArray(data?.catalog) && data.catalog.length) return data.catalog;
  return (data?.layouts || []).map((slug) => ({ slug, title: slug, group: "" }));
}

export function renderGallery(container, rows) {
  container.replaceChildren();
  document.documentElement.classList.add("layout-gallery-open");
  document.body.classList.add("layout-gallery-open");
  document.title = "Demo layouts";
  const page = el("div", "layout-gallery");
  const head = el("header", "layout-gallery-head");
  const title = el("h1");
  title.textContent = "Demo layouts";
  const hint = el("p");
  hint.textContent = "Pick an interface. Replay and live telemetry attach after it opens.";
  head.append(title, hint);
  const grid = el("div", "layout-gallery-grid");
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) {
    const empty = el("p", "layout-gallery-empty");
    empty.textContent = "No layouts found.";
    page.append(head, empty);
    container.append(page);
    return page;
  }
  for (const row of list) {
    const card = document.createElement("a");
    card.className = "layout-gallery-card";
    card.href = `#${row.slug}`;
    const group = el("span", "layout-gallery-group");
    group.textContent = row.group || "";
    const name = el("strong");
    name.textContent = row.title || row.slug;
    const slug = el("span", "layout-gallery-slug");
    slug.textContent = row.slug;
    card.append(group, name, slug);
    grid.append(card);
  }
  page.append(head, grid);
  container.append(page);
  return page;
}

export function hideGallery() {
  document.documentElement.classList.remove("layout-gallery-open");
  document.body.classList.remove("layout-gallery-open");
}
