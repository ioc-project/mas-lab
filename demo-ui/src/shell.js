import { GoldenLayout } from "golden-layout";
import "golden-layout/dist/css/goldenlayout-base.css";
import "golden-layout/dist/css/themes/goldenlayout-dark-theme.css";
import { apiFetch, readJson } from "./http.js";
import { attachLaneHeaders, collectLaneTitles, compileLayout, pinLaneHeaders } from "./layout-compiler.js";
import { bindPanelInfo, resolvePanelInfo } from "./plugin-info.js";
import { expandStreamNames } from "./streams.js";
import { getPlugin, listPlugins, registerPlugin } from "./plugin-registry.js";
import { initialChromeMode, mergePluginConfig } from "./layout-props.js";
import { mergeRef, uniqueRefs } from "./ref.js";
import { normalizeNotes } from "./narrative.js";
import * as chromePlugins from "./chrome.js";
import * as playerPlugins from "./player.js";
import * as layoutPickerPlugins from "./layout-picker.js";
import * as plotHostPlugins from "./plot-host.js";
import * as notesOverlayPlugins from "./notes-overlay.js";

function asPlugin(value) {
  if (!value) return null;
  if (value.id && typeof value.mount === "function") return value;
  if (typeof value === "function") {
    try {
      const made = value();
      if (made && made.id && typeof made.mount === "function") return made;
    } catch {
      return null;
    }
  }
  return null;
}

const SHELL_MODULES = [
  chromePlugins,
  playerPlugins,
  layoutPickerPlugins,
  plotHostPlugins,
  notesOverlayPlugins,
];

function registerModule(mod) {
  for (const value of Object.values(mod)) {
    const plugin = asPlugin(value);
    if (plugin) registerPlugin(plugin);
  }
}

function registerAll() {
  for (const mod of SHELL_MODULES) registerModule(mod);
}


const SHELL_TOPICS = ["job.*", "spec.*", "result.*", "ui.error"];
const SHELL_GETS = ["spec"];

function pluginTopics(pluginDef, props) {
  if (typeof pluginDef?.topics === "function") {
    return pluginDef.topics({ config: props || {} });
  }
  const topics = [...(pluginDef?.subscribes || []), ...(pluginDef?.gets || []), "view.reset"];
  const stream = String(props?.stream || "").trim();
  const dirac = String(props?.dirac || "").trim();
  if (stream) topics.push(stream, `${stream}.*`);
  if (dirac) topics.push(dirac, `${dirac}.*`);
  return topics;
}

export function collectStreamDemand(node, into) {
  if (!node) return into;
  if (!into.gets) into.gets = new Set();
  if (node.plugin) {
    const def = getPlugin(node.plugin);
    for (const topic of pluginTopics(def, node.props)) {
      if (topic && topic !== "*") into.topics.add(topic);
    }
    for (const name of def?.gets || []) {
      if (name) into.gets.add(name);
    }
    for (const pull of def?.inspects || []) into.inspect.add(pull);
  }
  for (const child of node.children || []) collectStreamDemand(child, into);
  if (node.content) collectStreamDemand(node.content, into);
  if (node.layout) collectStreamDemand(node.layout, into);
  for (const item of node.left || []) collectStreamDemand(item, into);
  for (const item of node.right || []) collectStreamDemand(item, into);
  return into;
}

export function collectBoundRefs(node, inherited, into) {
  if (!node) return;
  const ref = mergeRef(inherited, node.dataSource);
  if (node.plugin || node.type === "panel") {
    const bound = { ...ref };
    if (node.pre) bound.pre = node.pre;
    into.push(bound);
  }
  for (const child of node.children || []) collectBoundRefs(child, ref, into);
  if (node.content) collectBoundRefs(node.content, ref, into);
  if (node.layout) collectBoundRefs(node.layout, ref, into);
}

function mountDeclared(host, items, ctx) {
  const handles = [];
  for (const item of items || []) {
    const plugin = getPlugin(item.plugin || item.type);
    if (!plugin) continue;
    handles.push(
      plugin.mount(host, { ...ctx, config: { ...(ctx.config || {}), ...(item.props || {}) } }),
    );
  }
  return handles;
}

function mountSlot(host, slot, ctx) {
  if (!slot) return [];
  if (slot.plugin || slot.type) return mountDeclared(host, [slot], ctx);
  if (slot.content) return mountDeclared(host, [slot.content], ctx);
  return [];
}

export async function startShell({ app, bus, layoutName, layoutDoc }) {
  await registerAll();
  let doc = layoutDoc;
  if (!doc) {
    const res = await apiFetch(`/api/layouts/${encodeURIComponent(layoutName)}`);
    doc = await readJson(res, `layout ${layoutName}`);
  }
  const rootRef = doc.dataSource || {};
  const bound = [];
  collectBoundRefs(doc, rootRef, bound);

  bus.chromeMode = initialChromeMode(doc.modes, rootRef.mode, doc.mode);
  const ctxBase = {
    dataSource: rootRef,
    bus,
    layout: doc,
    layoutName,
    send: (command) => bus.send(command),
    targets: () => uniqueRefs(bound),
    config: { title: doc.title || "", modes: doc.modes },
  };

  app.replaceChildren();
  const chromeHandles = [];
  if (doc.navbar) {
    const nav = document.createElement("div");
    nav.className = "navbar";
    const left = document.createElement("div");
    left.className = "navbar-side";
    const right = document.createElement("div");
    right.className = "navbar-side";
    nav.append(left, right);
    chromeHandles.push(...mountDeclared(left, doc.navbar.left, ctxBase));
    chromeHandles.push(...mountDeclared(right, doc.navbar.right, ctxBase));
    app.appendChild(nav);
  }

  const shell = document.createElement("div");
  shell.className = "shell";
  const columns = [];
  if (doc.leftPanel) columns.push("auto");
  columns.push("1fr");
  if (doc.rightPanel) columns.push("auto");
  shell.style.gridTemplateColumns = columns.join(" ");

  const main = document.createElement("div");
  main.className = "main-gl";

  if (doc.leftPanel) {
    const leftPanel = document.createElement("div");
    leftPanel.className = "side-panel";
    leftPanel.style.width = `${doc.leftPanel.width || 240}px`;
    if (doc.leftPanel.defaultCollapsed) leftPanel.classList.add("collapsed");
    chromeHandles.push(...mountSlot(leftPanel, doc.leftPanel, ctxBase));
    shell.appendChild(leftPanel);
  }
  shell.appendChild(main);
  if (doc.rightPanel) {
    const rightPanel = document.createElement("div");
    rightPanel.className = "side-panel right";
    rightPanel.style.width = `${doc.rightPanel.width || 280}px`;
    if (doc.rightPanel.defaultCollapsed) rightPanel.classList.add("collapsed");
    chromeHandles.push(...mountSlot(rightPanel, doc.rightPanel, ctxBase));
    shell.appendChild(rightPanel);
  }
  app.appendChild(shell);

  const gl = new GoldenLayout(main);
  const handles = [];
  for (const plugin of listPlugins()) {
    gl.registerComponentFactoryFunction(plugin.id, (container, state) => {
      const el = container.element;
      const dataSource = mergeRef(rootRef, state?.dataSource);
      const pluginDef = getPlugin(state?.plugin || plugin.id);
      if (!pluginDef?.mount) return;
      const windowed = state?.windowed !== false && pluginDef.windowed !== false;
      if (!windowed) el.classList.add("plugin-unwindowed");
      el.dataset.plugin = pluginDef.id;
      if (state?.props?.kind) el.dataset.kind = String(state.props.kind);
      if (windowed) {
        const info = resolvePanelInfo(pluginDef, state?.props);
        if (info) {
          requestAnimationFrame(() =>
            bindPanelInfo(el, { title: state?.title || pluginDef.title || pluginDef.id, info }),
          );
        }
      }
      if (dataSource?.scenario) el.dataset.scenario = dataSource.scenario;
      const handle = pluginDef.mount(el, {
        dataSource,
        bus,
        send: (command) => bus.send({ ...command, ref: command.ref || dataSource }),
        config: mergePluginConfig(pluginDef.id, state?.props, doc),
        layout: doc,
        targets: () => uniqueRefs(bound),
      });
      const topics = expandStreamNames(pluginTopics(pluginDef, state?.props));
      const matchRef = pluginDef.matchIgnoreScenario
        ? { ...dataSource, scenario: "" }
        : dataSource;
      const off = bus.subscribe((envelope) => {
        if (!handle.update) return;
        if (!bus.match(envelope, topics, matchRef)) return;
        handle.update(envelope);
      });
      handles.push(handle);
      container.on("resize", () => handle.resize?.());
      container.on("destroy", () => {
        off();
        handle.destroy();
      });
    });
  }

  const config = compileLayout(doc);
  gl.loadLayout(config);
  const laneTitles = collectLaneTitles(doc.layout);
  let lastW = 0;
  let lastH = 0;
  const fit = () => {
    const width = Math.round(main.clientWidth);
    const height = Math.round(main.clientHeight);
    if (width <= 0 || height <= 0) return;
    if (width !== lastW || height !== lastH) {
      lastW = width;
      lastH = height;
      gl.setSize(width, height);
    }
    attachLaneHeaders(gl, laneTitles);
    pinLaneHeaders(main);
  };
  const ro = new ResizeObserver(fit);
  ro.observe(main);
  requestAnimationFrame(fit);

  const offChrome = bus.subscribe((envelope) => {
    for (const handle of chromeHandles) handle.update?.(envelope);
  });

  const narrative = normalizeNotes(doc);
  const overlayPlugin = listPlugins().find((plugin) => plugin.overlay);
  if (narrative.overlay && overlayPlugin) {
    chromeHandles.push(overlayPlugin.mount(app, ctxBase));
  }

  const demand = { topics: new Set(SHELL_TOPICS), gets: new Set(SHELL_GETS), inspect: new Set() };
  collectStreamDemand(doc, demand);
  collectStreamDemand(doc.navbar, demand);
  collectStreamDemand(doc.leftPanel, demand);
  collectStreamDemand(doc.rightPanel, demand);
  if (narrative.overlay && overlayPlugin) {
    for (const topic of overlayPlugin.subscribes || []) demand.topics.add(topic);
  }
  return {
    topics: [...demand.topics],
    subscribe: [...demand.topics],
    get: [...demand.gets],
    inspect: [...demand.inspect],
    mode: rootRef.mode || "live",
    destroy() {
      offChrome();
      ro.disconnect();
      for (const handle of [...chromeHandles, ...handles]) {
        try {
          handle.destroy();
        } catch {
          /* already gone */
        }
      }
      gl.destroy();
      main.replaceChildren();
    },
  };
}
