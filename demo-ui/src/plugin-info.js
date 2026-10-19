/**
 * Panel about-text. The registered plugin object is the manifest
 * (`id`, `info` / `description`). A layout node may override with
 * `props.info`. Windowed cards show this next to maximise.
 */

export function resolvePanelInfo(plugin, props) {
  const fromProps = String(props?.info || "").trim();
  if (fromProps) return fromProps;
  return String(plugin?.info || plugin?.description || "").trim();
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function closeOpen(root) {
  root?.querySelectorAll(".plugin-info-pop").forEach((node) => node.remove());
}

export function bindPanelInfo(containerEl, { title, info } = {}) {
  const text = String(info || "").trim();
  if (!text || !containerEl) return null;
  const stack = containerEl.closest(".lm_stack");
  const controls = stack?.querySelector(":scope > .lm_header .lm_controls");
  if (!controls || controls.querySelector(".lm_info")) return null;
  const btn = document.createElement("div");
  btn.className = "lm_info";
  btn.setAttribute("role", "button");
  btn.setAttribute("tabindex", "0");
  btn.setAttribute("aria-label", `About ${title || "this panel"}`);
  btn.title = "About this panel";
  btn.textContent = "i";
  controls.insertBefore(btn, controls.firstChild);
  const toggle = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const open = stack.querySelector(".plugin-info-pop");
    if (open) {
      open.remove();
      return;
    }
    closeOpen(stack.closest(".lm_goldenlayout") || stack);
    const pop = document.createElement("div");
    pop.className = "plugin-info-pop";
    pop.setAttribute("role", "dialog");
    pop.innerHTML = `<header>${esc(title || "Panel")}</header><p>${esc(text)}</p>`;
    stack.appendChild(pop);
  };
  btn.addEventListener("click", toggle);
  btn.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") toggle(event);
  });
  if (typeof document !== "undefined" && !document.documentElement.dataset.masInfoCloser) {
    document.documentElement.dataset.masInfoCloser = "1";
    document.addEventListener("click", () => closeOpen(document));
  }
  return btn;
}

export function closePanelInfo(rootEl) {
  closeOpen(rootEl);
}
