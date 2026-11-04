//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
const svg = document.getElementById("topology");
const timelineTree = document.getElementById("timeline-tree");
const timelineGraph = document.getElementById("timeline-graph");
const eventCount = document.getElementById("event-count");
const activeAgents = document.getElementById("active-agents");
const lastEvent = document.getElementById("last-event");
const activityLog = document.getElementById("activity-log");
const promptForm = document.getElementById("prompt-form");
const autoScrollToggle = document.getElementById("toggle-autoscroll");
const timelineAutoScrollToggle = document.getElementById("toggle-timeline-autoscroll");
const dockHandle = document.getElementById("dock-handle");
const logDock = document.querySelector(".log-dock");
const appSelect = document.getElementById("app-select");
const layoutSelect = document.getElementById("layout-select");
const scenarioSelect = document.getElementById("scenario-select");
const userModelSelect = document.getElementById("user-model-select");
const promptSelect = document.getElementById("prompt-select");
const promptInput = document.getElementById("prompt-input"); 
const promptClear = document.getElementById("prompt-clear");
const serviceList = document.getElementById("service-list");

// --- GOLDEN LAYOUT SETUP ---
const config = {
    settings: {
        hasHeaders: true,
        constrainDragToContainer: true,
        reorderEnabled: true,
        selectionEnabled: false,
        popoutWholeStack: false,
        blockedPopoutsThrowError: true,
        closePopoutsOnUnload: true,
        showPopoutIcon: false,
        showMaximiseIcon: true,
        showCloseIcon: false
    },
    dimensions: {
        borderWidth: 5,
        minItemHeight: 100,
        minItemWidth: 180,
        headerHeight: 30
    },
    content: [{
        type: 'column',
        content: [{
            type: 'row',
            content: [{
                type: 'component',
                componentName: 'timeline',
                width: 20,
                title: 'Timeline',
                isClosable: false
            },{
                type: 'component',
                componentName: 'topology',
                width: 60,
                title: 'Topology',
                isClosable: false
            },{
                type: 'component',
                componentName: 'metrics',
                width: 20,
                title: 'Metrics & Services',
                isClosable: false
            }]
        },{
            type: 'component',
            componentName: 'logs',
            height: 25,
            title: 'System Logs',
            isClosable: false
        }]
    }]
};

// Singleton DOM elements (moved from Templates into GL Layout)
const DOM_CACHE = {
    topology: document.getElementById('topology-template'),
    timeline: document.getElementById('timeline-template'), 
    metrics: document.getElementById('metrics-template'),
    logs: document.getElementById('logs-template') 
};

// Initialize Layout
const myLayout = new GoldenLayout(config, $('#layout-container'));

// Register Components
myLayout.registerComponent('topology', function(container, state) {
    if (DOM_CACHE.topology) {
        container.getElement().append(DOM_CACHE.topology);
        // Handle resize for D3
        container.on('resize', function() {
            if (typeof updateTopologySize === 'function') {
                 // Defer slightly to let layout settle
                 setTimeout(() => {
                    const el = container.getElement()[0];
                    if (el) {
                        const { width, height } = el.getBoundingClientRect();
                        updateTopologySize(width, height);
                    }
                 }, 50);
            }
        });
    }
});

myLayout.registerComponent('timeline', function(container, state) {
    if (DOM_CACHE.timeline) container.getElement().append(DOM_CACHE.timeline);
});

myLayout.registerComponent('metrics', function(container, state) {
    if (DOM_CACHE.metrics) container.getElement().append(DOM_CACHE.metrics);
});

myLayout.registerComponent('logs', function(container, state) {
    if (DOM_CACHE.logs) container.getElement().append(DOM_CACHE.logs);
});

myLayout.init();
// --- END GOLDEN LAYOUT ---

// --- Service Status Polling ---
async function updateInfrastructureStatus() {
    try {
        // Query the dedicated infrastructure endpoint
        const res = await fetch("/api/infrastructure");
        if (!res.ok) return;
        const data = await res.json();
        
        if (!serviceList) return;
        serviceList.innerHTML = ""; // clear
        
        (data.services || []).forEach(svc => {
            const card = document.createElement("div");
            card.className = "metric-card";
            
            // Status Color
            let statusColor = "#94a3b8"; // muted
            if (svc.status === "active") statusColor = "#10b981"; // green
            else if (svc.status === "mock") statusColor = "#f59e0b"; // orange
            else if (svc.status === "warn" || svc.status === "error") statusColor = "#ef4444"; // red
            
            card.innerHTML = `
                <span class="metric-label" style="display:flex; justify-content:space-between; width:100%">
                   ${svc.label}
                   <span style="width:8px; height:8px; border-radius:50%; background:${statusColor}; display:inline-block;"></span>
                </span>
                <span class="metric-unit">${svc.status.toUpperCase()}</span>
            `;
            serviceList.appendChild(card);
        });
        
    } catch (e) {
        console.warn("Failed to fetch infrastructure status", e);
    }
}

// Start polling
setInterval(updateInfrastructureStatus, 5000);
updateInfrastructureStatus(); // initial

if (userModelSelect) {
  userModelSelect.addEventListener("change", async () => {
    updateControlsState();
    // Stop user agent if switching to manual
    if (userModelSelect.value === "manual") {
        console.log("Switching to Manual: Stopping user agent...");
        try {
            await fetch("/api/stop_user", { method: "POST" });
        } catch (e) {
            console.warn("Failed to stop user agent on manual switch", e);
        }
    }
  });
}

const toggleOtel = document.getElementById("toggle-otel");
const controllerStatus = document.getElementById("controller-status");
const controllerWarn = document.getElementById("controller-warn");
const serviceStatusMenu = document.getElementById("service-status-menu");
const serviceStatusList = document.getElementById("service-status-list");
const serviceStatusSummary = document.getElementById("service-status-summary");
const protocolLensBadges = document.getElementById("protocol-lens-badges");

// Navigation Elements
const navAppTrigger = document.getElementById("nav-app-trigger");
const navAppDropdown = document.getElementById("nav-app-dropdown");
const navAppValue = document.getElementById("nav-app-value");
const navUserTrigger = document.getElementById("nav-user-trigger");
const navUserDropdown = document.getElementById("nav-user-dropdown");
const navUserValue = document.getElementById("nav-user-value");
const serviceStatusTrigger = document.getElementById("service-status-trigger");
const navDatasetBtn = document.getElementById("nav-dataset-btn");
const navDatasetDropdown = document.getElementById("nav-dataset-dropdown");

// Nav Dropdown Logic
function setupNavDropdowns() {
  function closeAllDropdowns() {
    [navAppDropdown, navUserDropdown, serviceStatusMenu, navDatasetDropdown].forEach(d => {
      if (d) d.setAttribute("aria-hidden", "true");
    });
    [navAppTrigger, navUserTrigger, serviceStatusTrigger, navDatasetBtn].forEach(t => {
      if (t) t.setAttribute("aria-expanded", "false");
    });
  }

  function toggleDropdown(trigger, dropdown) {
    if (!trigger || !dropdown) return;
    const isHidden = dropdown.getAttribute("aria-hidden") === "true";
    closeAllDropdowns();
    if (isHidden) {
      dropdown.setAttribute("aria-hidden", "false");
      trigger.setAttribute("aria-expanded", "true");
    }
  }

  if (navAppTrigger) {
    navAppTrigger.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleDropdown(navAppTrigger, navAppDropdown);
    });
  }

  if (navUserTrigger) {
    navUserTrigger.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleDropdown(navUserTrigger, navUserDropdown);
    });
  }

  if (serviceStatusTrigger && serviceStatusMenu) {
      serviceStatusTrigger.addEventListener("click", (e) => {
          e.stopPropagation();
          toggleDropdown(serviceStatusTrigger, serviceStatusMenu);
      });
  }

  // Dataset filter button
  if (navDatasetBtn && navDatasetDropdown) {
    navDatasetBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const isHidden = navDatasetDropdown.getAttribute("aria-hidden") === "true";
      closeAllDropdowns();
      if (isHidden) {
        navDatasetDropdown.setAttribute("aria-hidden", "false");
        navDatasetBtn.setAttribute("aria-expanded", "true");
      }
    });
    navDatasetDropdown.addEventListener("click", async (e) => {
      e.stopPropagation();
      const item = e.target.closest(".nav-dataset-item");
      if (!item) return;
      // Switch to selected dataset file
      const file = item.dataset.file;
      navDatasetDropdown.querySelectorAll(".nav-dataset-item").forEach(
        el => el.classList.toggle("is-active", el === item)
      );
      navDatasetDropdown.setAttribute("aria-hidden", "true");
      navDatasetBtn.setAttribute("aria-expanded", "false");
      if (file) {
        const loaded = await fetchJson(`/api/dataset?file=${encodeURIComponent(file)}`).catch(() => null);
        if (loaded) {
          currentDataset = loaded;
          const label = document.getElementById("nav-dataset-label");
          if (label) label.textContent = loaded.dataset || file.replace(".json", "");
          await hydratePromptSelect(loaded.items || [], scenarioSelect?.value || null);
          updateControlsState();
        }
      }
    });
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".nav-group") && !e.target.closest(".service-status")) {
      closeAllDropdowns();
    }
  });

  // Stop propagation inside dropdowns
  [navAppDropdown, navUserDropdown, serviceStatusMenu, navDatasetDropdown].forEach(d => {
      if (d) {
          d.addEventListener("click", (e) => {
             e.stopPropagation();
          });
      }
  });
}
setupNavDropdowns();
const demoModeButton = document.getElementById("demo-mode");


// Function to stop session (reused/shared)
async function stopSession() {
    await fetch("/api/stop", { method: "POST" }).catch(console.error);
    isScenarioRunning = false;
    sessionEntryAgent = "";
    sessionStartTime = 0;
    if (abortController) {
        abortController.abort();
        abortController = null;
    }
    isWaitingForResponse = false;
    isExecutionActive = false;
    setLoadingState(false);
    updateControlsState();
    console.log("Session stopped by user.");
}

const sidePanel = document.querySelector(".side-panel");
const board = document.querySelector(".board");
const loadingOverlay = document.getElementById("loading-overlay");
const btnInitScenario = document.getElementById("btn-init-scenario");
const btnStopScenario = document.getElementById("btn-stop-scenario");
const btnStopUser = document.getElementById("btn-stop-user");
const btnSendPrompt = document.getElementById("btn-send-prompt");
const elScenarioStatus = document.getElementById("scenario-status");
const elPromptStatus = document.getElementById("prompt-status");

let isWaitingForResponse = false;
// Tracks whether the server is actively executing a prompt.  Unlike
// isWaitingForResponse (which only covers the initial HTTP call), this
// stays true until the final user_response/agent_to_user event arrives or
// the server confirms running=false.  Controls the Interrupt / Send button
// and survives page refresh via recoverState().
let isExecutionActive = false;
let isScenarioRunning = false;
let abortController = null;
let isResyncing = false;
let servicesOk = true;

function setResyncing(value) {
  isResyncing = value;
  updateControlsState();
}

function isUiReady() {
  return controllerOk && servicesOk && !isResyncing && cachedTopology;
}

function setScenarioStatus(status) {
    if (!elScenarioStatus) return;
    elScenarioStatus.textContent = status;
    elScenarioStatus.className = `status-pill status-${status.toLowerCase()}`;
}

function setPromptStatus(status) {
    if (!elPromptStatus) return;
    elPromptStatus.textContent = status;
    elPromptStatus.className = `status-pill status-${status.toLowerCase()}`;
    
    // Update button text accordingly
    if (btnSendPrompt) {
        if (status === "ACTIVE") {
            btnSendPrompt.textContent = "Interrupt";
            btnSendPrompt.classList.add("is-stop");
            btnSendPrompt.disabled = false; // Always enabled to allow stop
            btnSendPrompt.title = "Interrupt current generation";
        } else {
            btnSendPrompt.textContent = "Send";
            btnSendPrompt.classList.remove("is-stop");
            // Validation is handled by updateControlsState
        }
    }
}

function setLoadingState(loading, message = "Processing...") {
  isWaitingForResponse = loading;
  updateControlsState();

  const overlayText = loadingOverlay.querySelector("p");
  if (overlayText) overlayText.textContent = message;

  if (loading) {
    // We only show overlay for initialization, not prompt processing (user wants to see flow)
    // But user request said "Run should init ... and spinner stops on ready"
    // For prompt, "see messages immediately flowing".
    // So overlay only if initing scenario
    if (!isScenarioRunning) {
        loadingOverlay.classList.remove("is-hidden");
        svg.classList.add("is-loading");
    }
  } else {
    loadingOverlay.classList.add("is-hidden");
    svg.classList.remove("is-loading");
  }
}

function updateControlsState() {
  const ready = isUiReady();
  const isAutonomous = userModelSelect ? userModelSelect.value === "autonomous" : false;

  // Update Nav Values
  if (navAppValue && appSelect) {
      const appText = appSelect.options[appSelect.selectedIndex] ? appSelect.options[appSelect.selectedIndex].text : (appSelect.value || "--");
      navAppValue.textContent = appText;
  }
  // Show scenario · dataset sub-label under the app name
  const navContextValue = document.getElementById("nav-context-value");
  if (navContextValue) {
      const scenario = scenarioSelect?.value || "";
      const dataset = currentDataset?.dataset || "";
      const parts = [scenario, dataset].filter(Boolean);
      navContextValue.textContent = parts.join(" · ");
  }
  if (navUserValue && userModelSelect) {
      const userText = userModelSelect.options[userModelSelect.selectedIndex] ? userModelSelect.options[userModelSelect.selectedIndex].text : "Manual";
      navUserValue.textContent = userText;
  }

  // Manage Prompt Form Visibility
  if (promptForm) {
      if (isAutonomous) {
         promptForm.style.display = "none";
      } else {
         // Because we moved promptForm to the navbar (flex row), use 'flex'
         promptForm.style.display = "flex";
      }
  }

  if (isScenarioRunning) {
      setScenarioStatus("RUNNING");
      
      // Update Scenario Button to Stop
      btnInitScenario.style.display = "none";
      if (btnStopScenario) {
        btnStopScenario.style.display = "flex"; 
        btnStopScenario.disabled = false;
      }
      
      // Stop User Button
      if (btnStopUser) {
         if (isAutonomous) {
             btnStopUser.style.display = "flex";
             btnStopUser.disabled = false;
         } else {
             btnStopUser.style.display = "none";
         }
      }
  } else {
      setScenarioStatus("STOPPED");
      
      // Update Scenario Button to Run
      btnInitScenario.style.display = "flex";
      if (btnStopScenario) btnStopScenario.style.display = "none";
      if (btnStopUser) btnStopUser.style.display = "none";

      if (isWaitingForResponse) {
          // If initializing...
          btnInitScenario.disabled = true;
          btnInitScenario.textContent = "Processing...";
      } else {
          btnInitScenario.disabled = !ready;
          btnInitScenario.textContent = "Run App";
      }
  }

  // Update Prompt Status driven by state
  const targetStatus = (isWaitingForResponse || isExecutionActive) ? "ACTIVE" : "IDLE";
  setPromptStatus(targetStatus);

  // Centralized Prompt Button Validation
  if (btnSendPrompt) {
      if (isWaitingForResponse) {
          // Interrupt mode - managed by setPromptStatus("ACTIVE") -> Always Enabled
          // Ensure title is correct
          btnSendPrompt.disabled = false;
          btnSendPrompt.title = "Interrupt the current generation";
      } else {
          // Send mode
          const hasInput = promptInput && promptInput.value.trim().length > 0;
          if (!ready) {
              btnSendPrompt.disabled = true;
              btnSendPrompt.title = "System is not ready (checking services...)";
          } else if (!hasInput) {
              btnSendPrompt.disabled = true;
              btnSendPrompt.title = "Please enter a prompt to send";
          } else {
              btnSendPrompt.disabled = false;
              btnSendPrompt.title = "Send prompt to agent";
          }
      }
  }
}


const panelMenuButton = document.getElementById("panel-menu-button");
const panelMenu = document.getElementById("panel-menu");
const toggleSignals = document.getElementById("toggle-signals");
const toggleLog = document.getElementById("toggle-log");
const hideSignals = document.getElementById("hide-signals");
const hideLog = document.getElementById("hide-log");
const sideHandle = document.getElementById("side-handle");
const resetLayoutButton = document.getElementById("reset-layout");
const toggleMetrics = document.getElementById("toggle-metrics");
const metricsPanel = document.getElementById("metrics-panel");
const hideMetrics = document.getElementById("hide-metrics");
const metricsHandle = document.getElementById("metrics-handle");

const palette = {
  agent: "#38bdf8",
  tool: "#f97316",
  memory: "#21c997",
  infra: "#60a5fa",
  user: "#facc15",
  llm: "#e879f9",
  unknown: "#94a3b8",
};

const visitedNodes = new Set();   // permanent trail — nodes ever active this page session
const nodeState = new Map();
const edgeState = new Map();
const nodeGroups = new Map();
const nodeActiveUntil = new Map();
const edgeActiveUntil = new Map();
const edgePulseTimers = new WeakMap();
const recentEventKeys = new Map();
let cachedTopology = null;
let showOtel = true;
let lastSeenTimestamp = 0;
let lastSeenIndex = -1;
let currentRunId = "";
let sessionStartTime = 0; // Tracks start of current session for duration metric
let promptExecutionActive = false; // Track if a prompt is currently executing
let lastMetricsSnapshot = null; // Store metrics to display after execution completes
let autoScrollEnabled = true;
let selectionToken = 0;
let runToken = 0;
let flowPulseQueue = Promise.resolve();
const protocolLensEvents = [];
let sessionEntryAgent = "";
let backendName = "unknown";

function enqueueFlowPulse(work) {
  // Always chain onto the FIFO queue — no parallel bypass; simultaneous pulses
  // on overlapping edges produce the "double bump" visual artifact.
  flowPulseQueue = flowPulseQueue.then(() => work()).catch(() => {});
  return flowPulseQueue;
}

function classifyProtocol(event) {
  const kind = event?.kind || "";
  if (kind.startsWith("otlp_") || kind.startsWith("otel_")) return "otel";
  if (kind.startsWith("tool_") || kind.startsWith("memory_")) return "tool";
  if (kind.startsWith("agent_to_agent") || kind.startsWith("agent_remote_") || kind === "routing") return "agent";
  return "core";
}

function shouldTrackProtocol(event) {
  if (!event?.kind || event.kind.startsWith("object_")) return false;
  if (!showOtel && (event.kind.startsWith("otlp_") || event.kind.startsWith("otel_"))) return false;
  if (event.kind === "ontology_alignment_skipped" || event.kind === "ontology_alignment") return false;
  return true;
}

function pushProtocolEvent(event) {
  if (!protocolLensBadges || !shouldTrackProtocol(event)) return;
  const label = classifyProtocol(event);
  const entry = {
    label,
    kind: event.kind,
    agent: event.agent_id || "",
  };
  protocolLensEvents.unshift(entry);
  if (protocolLensEvents.length > 5) protocolLensEvents.pop();
  renderProtocolLens();
  if (!sessionEntryAgent && event.agent_id) {
    sessionEntryAgent = event.agent_id;
  }
  if (!sessionStartTime && event.timestamp) {
    sessionStartTime = event.timestamp;
  }
}

function renderProtocolLens() {
  if (!protocolLensBadges) return;
  protocolLensBadges.innerHTML = "";
  protocolLensEvents.forEach((entry) => {
    const badge = document.createElement("span");
    badge.className = `protocol-badge protocol-badge--${entry.label}`;
    badge.textContent = entry.label.toUpperCase();
    const detail = entry.agent ? `${entry.kind} · ${entry.agent}` : entry.kind;
    badge.title = detail || entry.label;
    protocolLensBadges.appendChild(badge);
  });
}

function selectOption(select, predicate) {
  if (!select) return false;
  for (let i = 0; i < select.options.length; i += 1) {
    const option = select.options[i];
    if (predicate(option)) {
      select.selectedIndex = i;
      return true;
    }
  }
  return false;
}

async function applyDemoMode() {
  localStorage.setItem("mas.showSignals", "true");
  localStorage.setItem("mas.showLog", "true");
  localStorage.setItem("mas.showMetrics", "true");
  applyPanelState();

  if (appSelect && appSelect.options.length > 0) {
    appSelect.selectedIndex = 0;
    await syncSelections();
  }

  selectOption(scenarioSelect, (option) => option.value === "baseline");
  await setScenario(scenarioSelect.value);

  if (promptSelect && promptSelect.options.length > 0) {
    promptSelect.selectedIndex = 0;
  }

  if (layoutSelect) {
    const appId = getCurrentAppId();
    const storedLayout = appId ? localStorage.getItem(`mas.layoutMode.${appId}`) : null;
    if (!storedLayout) {
      layoutSelect.value = "tiered";
      setLayoutMode(appId, "tiered");
    } else {
      layoutSelect.value = getLayoutMode(appId);
    }
  }

  resetUiState({ clearTopology: false });
  if (cachedTopology) {
    drawTopology(cachedTopology);
  }
  updateControlsState();
}

// ============================================================================
// Plugin System Initialization
// ============================================================================

if (typeof PluginRegistry === 'undefined') {
  console.error('PluginRegistry not loaded - plugin-system.js may not have loaded');
}

const pluginRegistry = new PluginRegistry();

// Shared context accessible to all plugins
const sharedContext = {
  // Topology
  topology: () => cachedTopology,
  drawTopology: (topo) => drawTopology(topo),
  ensureNode,
  ensureEdge,
  
  // Node state management
  setNodeActive,
  setNodeStatus,
  setNodePersistentClass,
  clearNodePersistentClass,
  setEdgeActive,
  clearEdgeActive,
  
  // Animation
  triggerFlowPulse,
  triggerFreePulse,
  enqueueFlowPulse,
  
  // Metrics
  metricsState: () => metricsState,
  updateMetricDisplay: (id, value, statusClass) => {
    const el = document.getElementById(id);
    if (el) {
      el.textContent = value;
      el.classList.remove("status-ok", "status-warn", "status-error");
      if (statusClass) el.classList.add(statusClass);
    }
  },
  
  // OTEL control
  showOtel: () => showOtel,
  toggleOtelElement: document.getElementById("toggle-otel"),
  
  // Timeline
  addToTimeline: (event) => {
    timelineEvents.push(event);
    renderTimeline(false);
  },
  
  // Activity log
  addToLog: (event, isNew) => {
    appendLog(event, isNew);
  },
  
  // SVG reference
  svg: () => document.getElementById("topology"),
  
  // Utility
  resolveAgentId,
  resolveTarget,
  formatTimestamp,
  buildLogParts,
  estimatePulseCount,
};

// Register plugins (only if classes are defined)
if (typeof EventsPlugin !== 'undefined') {
  pluginRegistry.register(new EventsPlugin());
} else {
  console.error('EventsPlugin not loaded');
}

if (typeof HITLPlugin !== 'undefined') {
  pluginRegistry.register(new HITLPlugin());
} else {
  console.error('HITLPlugin not loaded');
}

if (typeof MetricsPlugin !== 'undefined') {
  // Metrics are managed in app.js to allow pause-on-idle behavior.
  pluginRegistry.register(new MetricsPlugin({ enabled: false }));
} else {
  console.error('MetricsPlugin not loaded');
}

if (typeof ObservabilityPlugin !== 'undefined') {
  pluginRegistry.register(new ObservabilityPlugin());
} else {
  console.error('ObservabilityPlugin not loaded');
}

// ---------------------------------------------------------------------------
// Topology display preferences
// ---------------------------------------------------------------------------
const _TOPO_PREFS_KEY = 'mas.topoPrefs';
function _loadTopoPrefs() {
  try { return JSON.parse(localStorage.getItem(_TOPO_PREFS_KEY) || '{}'); } catch { return {}; }
}
function getTopoPref(key, defaultVal) {
  const p = _loadTopoPrefs(); return key in p ? p[key] : defaultVal;
}
function setTopoPref(key, value) {
  const p = _loadTopoPrefs(); p[key] = value;
  localStorage.setItem(_TOPO_PREFS_KEY, JSON.stringify(p));
}

let currentDataset = { items: [], dataset: "default" };
let activeDatasetFilter = "all";
let currentPositions = new Map();
let dragState = null;
let dragFrame = null;
let panState = null;
let viewBoxState = null;
let defaultViewBox = null;
let dockHeight = 140;
let timelineEvents = [];
let expandedGroups = new Set();
let isInitialLoad = true;
let isRefreshing = false;
let timelineAutoScroll = true;
let timelineViewMode = "graph";

if (svg) {
  initViewBox();
  svg.addEventListener("pointerdown", startPan);
  svg.addEventListener("pointermove", handlePanMove);
  svg.addEventListener("pointerup", endPan);
  svg.addEventListener("pointercancel", endPan);
  svg.addEventListener("wheel", handleZoom, { passive: false });
}
const LAYOUT_OPTIONS = [
  { id: "force", label: "Force-Directed" },
  { id: "tiered", label: "Hierarchical (Top-Down)" },
  { id: "hierarchical-lr", label: "Hierarchical (Left-Right)" },
  { id: "radial", label: "Radial" },
  { id: "concentric", label: "Concentric" },
  { id: "circular", label: "Circular" },
  { id: "grid", label: "Grid" },
  { id: "orthogonal", label: "Orthogonal" },
  { id: "sugiyama", label: "Sugiyama" },
  { id: "clustered", label: "Clustered" },
  { id: "swimlane", label: "Swimlane" },
  { id: "custom", label: "Custom" },
];
const ACTIVE_WINDOW_MS = 2000;
const DEDUPE_WINDOW_MS = 1200;
const highlightOrder = ["user", "agent", "tool", "memory", "llm", "infra"];
const typeOrder = ["user", "agent", "tool", "memory", "llm", "infra", "unknown"];

function resolveAgentId(event) {
  if (event.agent_id) {
    return event.agent_id;
  }
  const payload = event.payload || {};
  if (payload.agent_id) {
    return payload.agent_id;
  }
  // For outgoing events (agent_to_user, user_response, routing) the sender
  // is often stored in source / from / source_agent_id
  if (event.source_agent_id) return event.source_agent_id;
  if (event.source && event.source !== 'user') return event.source;
  if (event.from && event.from !== 'user') return event.from;
  if (payload.source_agent_id) return payload.source_agent_id;
  if (payload.from_agent_id) return payload.from_agent_id;
  return payload.task?.agent_id || null;
}

function resolvePrompt(event) {
  const payload = event.payload || {};
  if (typeof payload.prompt === "string") {
    return payload.prompt;
  }
  if (typeof payload.task?.prompt === "string") {
    return payload.task.prompt;
  }
  return "";
}

function groupNodesByType(nodes) {
  const groups = {};
  nodes.forEach((node) => {
    const type = node.type || "unknown";
    if (!groups[type]) {
      groups[type] = [];
    }
    groups[type].push(node);
  });
  return groups;
}

function distributeInRing(nodes, centerX, centerY, radius, positions) {
  const count = nodes.length || 1;
  nodes.forEach((node, index) => {
    const angle = (index / count) * Math.PI * 2 - Math.PI / 2;
    positions.set(node.id, {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
      type: node.type,
    });
  });
}

function applyForceLayout(topology, positions, width, height) {
  const nodes = topology.nodes;
  const edges = topology.edges;
  
  // Initial placement: Circular
  const cx = width / 2;
  const cy = height / 2;
  const initialRadius = Math.min(width, height) * 0.3;
  
  nodes.forEach((n, i) => {
    const angle = (i / nodes.length) * 2 * Math.PI;
    positions.set(n.id, {
      x: cx + Math.cos(angle) * initialRadius,
      y: cy + Math.sin(angle) * initialRadius,
      type: n.type
    });
  });

  if (nodes.length < 2) return;

  // Simulation parameters
  const iterations = 100;
  // Optimal distance
  const k = Math.min(width, height) / (1 + Math.sqrt(nodes.length));
  
  for (let iter = 0; iter < iterations; iter++) {
    // Temperature for annealing
    const temp = (width / 10) * (1 - iter / iterations);
    
    // Forces Map
    const displacements = new Map();
    nodes.forEach(n => displacements.set(n.id, { x: 0, y: 0 }));

    // 1. Repulsion (between all pairs)
    for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) { // Symmetric
            const u = nodes[i];
            const v = nodes[j];
            const posU = positions.get(u.id);
            const posV = positions.get(v.id);
            
            let dx = posU.x - posV.x;
            let dy = posU.y - posV.y;
            let dist = Math.sqrt(dx*dx + dy*dy);
            if (dist < 0.1) {
                dx = (Math.random() - 0.5); 
                dy = (Math.random() - 0.5);
                dist = Math.sqrt(dx*dx + dy*dy);
            }
            
            // Fr = k^2 / d
            const force = (k * k) / dist;
            const dispX = (dx / dist) * force;
            const dispY = (dy / dist) * force;
            
            displacements.get(u.id).x += dispX;
            displacements.get(u.id).y += dispY;
            displacements.get(v.id).x -= dispX;
            displacements.get(v.id).y -= dispY;
        }
    }

    // 2. Attraction (along edges)
    edges.forEach(e => {
        const u = positions.get(e.from);
        const v = positions.get(e.to);
        if (!u || !v) return; // Should not happen if validated
        
        let dx = u.x - v.x;
        let dy = u.y - v.y;
        let dist = Math.sqrt(dx*dx + dy*dy);
        if (dist < 0.1) dist = 0.1;
        
        // Fa = d^2 / k
        const force = (dist * dist) / k;
        const dispX = (dx / dist) * force;
        const dispY = (dy / dist) * force;
        
        displacements.get(e.from).x -= dispX;
        displacements.get(e.from).y -= dispY;
        displacements.get(e.to).x += dispX;
        displacements.get(e.to).y += dispY;
    });

    // 3. Apply displacement (capped by temp) and Gravity
    nodes.forEach(n => {
        const disp = displacements.get(n.id);
        const dist = Math.sqrt(disp.x*disp.x + disp.y*disp.y);
        
        // Gravity to center
        disp.x -= (positions.get(n.id).x - cx) * 0.05 * (iter / iterations);
        disp.y -= (positions.get(n.id).y - cy) * 0.05 * (iter / iterations);

        if (dist > 0) {
            const limitedDist = Math.min(dist, temp);
            positions.get(n.id).x += (disp.x / dist) * limitedDist;
            positions.get(n.id).y += (disp.y / dist) * limitedDist;
        }

        // Constraints
        const pad = 40;
        positions.get(n.id).x = Math.max(pad, Math.min(width - pad, positions.get(n.id).x));
        positions.get(n.id).y = Math.max(pad, Math.min(height - pad, positions.get(n.id).y));
    });
  }
}

function distributeInGrid(nodes, startX, startY, width, height, positions) {
  const count = nodes.length || 1;
  const cols = Math.ceil(Math.sqrt(count));
  const rows = Math.ceil(count / cols);
  const cellX = width / Math.max(cols, 1);
  const cellY = height / Math.max(rows, 1);
  nodes.forEach((node, index) => {
    const col = index % cols;
    const row = Math.floor(index / cols);
    positions.set(node.id, {
      x: startX + cellX * (col + 0.5),
      y: startY + cellY * (row + 0.5),
      type: node.type,
    });
  });
}

function snapToGrid(value, step) {
  return Math.round(value / step) * step;
}

/** Deterministic hash: maps any string to an integer in [0, range). */
function hashNum(str, range) {
  let h = 5381;
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h) ^ str.charCodeAt(i);
    h = h & 0x7fffffff;
  }
  return h % range;
}

/**
 * Augment a bare topology (agents + user) with synthetic LLM and tool nodes
 * so all three tier bands are always populated in the tiered layout.
 * Safe to call multiple times — returns data unchanged if already augmented.
 */
function augmentTopologyWithSyntheticNodes(data) {
  if (!data || !data.nodes || data._augmented) return data;
  const nodes = [...data.nodes];
  const edges = [...(data.edges || [])];
  const agents = data.nodes.filter(n => n.type === 'agent');
  // One LLM node per agent — label starts as 'LLM', updated by telemetry events
  agents.forEach(agent => {
    const llmId = `${agent.id}__llm`;
    if (!nodes.find(n => n.id === llmId)) {
      const rawModel = agent.llm_model || '';
      const shortModel = rawModel ? rawModel.split('/').pop() : 'LLM';
      nodes.push({ id: llmId, type: 'llm', label: shortModel, _for_agent: agent.id });
      edges.push({ from: agent.id, to: llmId, type: 'llm-call' });
    }
  });
  // Tool and memory nodes come from the backend config only — no synthetic hub injected here.
  return { ...data, nodes, edges, _augmented: true };
}

function drawTopology(topology) {
  // Persist topology to localStorage for instant restore on page refresh
  try {
    const _topoKey = `mas.topology.${getCurrentAppId?.() || 'default'}`;
    localStorage.setItem(_topoKey, JSON.stringify(topology));
  } catch(e) {}

  svg.innerHTML = "";

  // Direction markers for edges.
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
  marker.setAttribute("id", "arrowhead");
  marker.setAttribute("markerWidth", "8");
  marker.setAttribute("markerHeight", "8");
  marker.setAttribute("refX", "8");
  marker.setAttribute("refY", "4");
  marker.setAttribute("orient", "auto");
  const arrowPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
  arrowPath.setAttribute("d", "M 0 0 L 8 4 L 0 8 z");
  arrowPath.setAttribute("fill", "rgba(124, 92, 255, 0.75)");
  marker.appendChild(arrowPath);
  defs.appendChild(marker);
  svg.appendChild(defs);

  const positions = new Map();
  const radius = 26;
  const width = svg.viewBox.baseVal.width || 1400;
  const height = svg.viewBox.baseVal.height || 900;
  const margin = 140;
  const layoutMode = getLayoutMode(getCurrentAppId());
  const useSaved = layoutMode === "custom";
  nodeState.clear();
  edgeState.clear();
  nodeGroups.clear();

  const layerConfig = [
    { name: "Tools", types: ["tool", "memory"], y: 155, bandH: 100, band: "rgba(47,107,255,0.07)" },
    { name: "Agentic Core", types: ["agent"], y: 430, bandH: 240, band: "rgba(56,189,248,0.09)" },
    { name: "LLM Layer", types: ["llm"], y: 730, bandH: 100, band: "rgba(232,121,249,0.08)" },
  ];

  if (layoutMode === "force") {
    applyForceLayout(topology, positions, width, height);
  } else if (["tiered", "hierarchical-lr", "sugiyama", "orthogonal"].includes(layoutMode)) {
    layerConfig.forEach((layer) => {
      const band = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const bH = layer.bandH || 110;
      band.setAttribute("x", "28");
      band.setAttribute("y", String(layer.y - bH / 2));
      band.setAttribute("width", String(width - 56));
      band.setAttribute("height", String(bH));
      band.setAttribute("rx", "22");
      band.setAttribute("fill", layer.band);
      svg.appendChild(band);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", "40");
      label.setAttribute("y", String(layer.y - (layer.bandH || 110) / 2 + 18));
      label.setAttribute("fill", "rgba(226,232,240,0.7)");
      label.setAttribute("font-size", "12");
      label.setAttribute("font-family", "IBM Plex Mono, monospace");
      label.textContent = layer.name;
      svg.appendChild(label);
    });

    const userNodes = topology.nodes.filter((node) => node.type === "user");
    userNodes.forEach((node, index) => {
      // Shift User nodes right (x: 160) to avoid overlap with lane title labels (x: 40)
      positions.set(node.id, { x: 160, y: 430 + index * 90, type: node.type });
    });

    layerConfig.forEach((layer) => {
      const nodes = topology.nodes.filter((node) => layer.types.includes(node.type));
      const layerWidth = width - margin * 2;
      nodes.forEach((node, index) => {
        // Shift lane nodes right (startX moved from margin+spacing)
        const spacing = layerWidth / (nodes.length + 1);
        // Ensure minimum left margin of 160px for first node in lane
        const x = Math.max(160, margin + spacing * (index + 1)) + ((index % 3) - 1) * 10;
        // Deterministic random y-spread within the band (stable across re-renders)
        const ySpread = (layer.bandH || 110) * 0.32;
        const yOffset = (hashNum(node.id, 100) / 100) * ySpread * 2 - ySpread;
        const y = layer.y + yOffset;
        positions.set(node.id, { x, y, type: node.type });
      });
    });

    const infraNodes = topology.nodes.filter((node) => node.type === "infra");
    
    // Separate observe/eval/explain from other infra nodes
    const observeTools = infraNodes.filter(n => ['observe', 'eval', 'explain'].includes(n.id));
    const otherInfra = infraNodes.filter(n => !['observe', 'eval', 'explain'].includes(n.id));
    
    // Position observe/eval/explain around user node in a circle
    const userNode = topology.nodes.find(n => n.id === 'user' || n.type === 'user');
    const userX = userNode ? 150 : 150; // user typically at x=150
    const userY = userNode ? (layoutMode === "hierarchical-lr" ? 250 : 280) : 280;
    const radius = 140; // distance from user
    observeTools.forEach((node, index) => {
      const angle = Math.PI + (index - 1) * (Math.PI / 4); // spread around left/bottom of user
      const x = userX + radius * Math.cos(angle);
      const y = userY + radius * Math.sin(angle);
      positions.set(node.id, { x, y, type: node.type });
    });
    
    // Position other infra nodes as before
    if (layoutMode === "hierarchical-lr") {
      otherInfra.forEach((node, index) => {
         // In LR mode, X and Y are swapped later (x=y_old, y=x_old).
         // "user" type is at y=280.. (which becomes x=280.. i.e. Left side)
         // We want infra nodes on the Left side too.
         
         const y = 90; // Becomes X (Low value = Left)
         const x = 500 + index * 120; // Becomes Y (Vertical spreading)
         positions.set(node.id, { x, y, type: node.type });
      });
    } else {
      otherInfra.forEach((node, index) => {
        const x = 120 + index * 140; // Leftish side
        const y = 700 + (index % 2 === 0 ? -20 : 20); // Bottom
        positions.set(node.id, { x, y, type: node.type });
      });
    }

    if (layoutMode === "hierarchical-lr") {
      positions.forEach((pos, id) => {
        positions.set(id, { x: pos.y, y: pos.x, type: pos.type });
      });
    }
    if (layoutMode === "orthogonal") {
      positions.forEach((pos, id) => {
        positions.set(id, {
          x: snapToGrid(pos.x, 40),
          y: snapToGrid(pos.y, 40),
          type: pos.type,
        });
      });
    }
  } else if (layoutMode === "radial") {
    const groups = groupNodesByType(topology.nodes);
    const centerX = width * 0.5;
    const centerY = height * 0.52;
    const rings = ["user", "agent", "tool", "memory", "llm", "infra", "unknown"];
    rings.forEach((type, index) => {
      const ringNodes = groups[type] || [];
      const radius = 70 + index * 70;
      distributeInRing(ringNodes, centerX, centerY, radius, positions);
    });
  } else if (layoutMode === "concentric") {
    const groups = groupNodesByType(topology.nodes);
    const centerX = width * 0.5;
    const centerY = height * 0.52;
    typeOrder.forEach((type, index) => {
      const ringNodes = groups[type] || [];
      const radius = 60 + index * 55;
      distributeInRing(ringNodes, centerX, centerY, radius, positions);
    });
  } else if (layoutMode === "circular") {
    const centerX = width * 0.5;
    const centerY = height * 0.52;
    const radius = Math.min(width, height) * 0.35;
    distributeInRing(topology.nodes, centerX, centerY, radius, positions);
  } else if (layoutMode === "grid") {
    distributeInGrid(topology.nodes, margin, margin, width - margin * 2, height - margin * 2, positions);
  } else if (layoutMode === "clustered") {
    const groups = groupNodesByType(topology.nodes);
    const centerX = width * 0.5;
    const centerY = height * 0.52;
    const clusterRadius = Math.min(width, height) * 0.28;
    const clusters = typeOrder.filter((type) => groups[type]?.length);
    clusters.forEach((type, index) => {
      const angle = (index / clusters.length) * Math.PI * 2 - Math.PI / 2;
      const clusterX = centerX + clusterRadius * Math.cos(angle);
      const clusterY = centerY + clusterRadius * Math.sin(angle);
      const clusterNodes = groups[type] || [];
      distributeInGrid(clusterNodes, clusterX - 60, clusterY - 40, 120, 80, positions);
    });
  } else if (layoutMode === "swimlane") {
    const groups = groupNodesByType(topology.nodes);
    const lanes = typeOrder.filter((type) => groups[type]?.length);
    const laneHeight = height / Math.max(lanes.length, 1);
    lanes.forEach((type, index) => {
      const nodes = groups[type] || [];
      const y = laneHeight * index + laneHeight * 0.5;
      const spacing = (width - margin * 2) / Math.max(nodes.length, 1);
      nodes.forEach((node, nodeIndex) => {
        positions.set(node.id, {
          x: margin + spacing * (nodeIndex + 0.5),
          y,
          type: node.type,
        });
      });
    });
  }

  if (useSaved) {
    const saved = loadNodeLayout(getCurrentAppId());
    const fallback = new Map();
    if (positions.size < topology.nodes.length) {
      applyForceLayout(topology, fallback, width, height);
    }
    topology.nodes.forEach((node) => {
      const savedPos = saved[node.id];
      if (savedPos) {
        positions.set(node.id, { x: savedPos.x, y: savedPos.y, type: node.type });
        return;
      }
      if (!positions.has(node.id) && fallback.size) {
        const fallbackPos = fallback.get(node.id);
        if (fallbackPos) {
          positions.set(node.id, { x: fallbackPos.x, y: fallbackPos.y, type: node.type });
        }
      }
    });
  }

  // Fallback: If layout failed to position nodes, use force layout
  if (positions.size === 0 && topology.nodes.length > 0) {
      applyForceLayout(topology, positions, width, height);
  }

  topology.edges.forEach((edge) => {
    const fromNode = topology.nodes.find((node) => node.id === edge.from);
    const toNode = topology.nodes.find((node) => node.id === edge.to);
    
    // Hide OTel static links (user request: only bubbles)
    // If either end is infrastructure, make the link invisible but present for structure if needed
    const isInfraLink = fromNode?.type === "infra" || toNode?.type === "infra";
    
    if (!showOtel && isInfraLink) {
      return;
    }
    
    const from = positions.get(edge.from);
    const to = positions.get(edge.to);
    if (!from || !to) {
      return;
    }
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", from.x);
    line.setAttribute("y1", from.y);
    line.setAttribute("x2", to.x);
    line.setAttribute("y2", to.y);
    line.setAttribute("class", "edge");

    // Detect tool/memory edges before setting visual attributes
    const isToolEdge = fromNode?.type === "tool" || toNode?.type === "tool" ||
                       fromNode?.type === "memory" || toNode?.type === "memory";

    // All edges: thin, no arrowheads — they only light up red when a bump propagates
    if (isInfraLink) {
        line.setAttribute("stroke-opacity", "0"); // Invisible
    }
    // No marker-end on any edge — the streak packet already conveys direction
    
    line.setAttribute("id", `edge-${edge.from}-${edge.to}`);

    if (edge.type === "instrumentation") {
      line.classList.add("edge--instrumentation");
    }
    // All non-instrumentation edges share the same thin light style
    if (isToolEdge) {
      line.classList.add("edge--tool");
    }
    svg.appendChild(line);
    edgeState.set(`${edge.from}->${edge.to}`, line);
  });

  topology.nodes.forEach((node) => {
    if (!showOtel && node.type === "infra") {
      return;
    }
    const pos = positions.get(node.id);
    if (!pos) {
      return;
    }
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    group.setAttribute("data-node", node.id);
    group.setAttribute("transform", `translate(${pos.x} ${pos.y})`);

    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", "0");
    circle.setAttribute("cy", "0");
    circle.setAttribute("r", radius);
    circle.setAttribute("fill", palette[node.type] || palette.unknown);
    circle.setAttribute("fill-opacity", "0.8");
    circle.setAttribute("stroke", "rgba(255,255,255,0.35)");
    circle.setAttribute("stroke-width", "2");
    circle.style.transformBox = "fill-box";
    circle.style.transformOrigin = "center";
    // Trail: flag circle so CSS can dim unvisited / brighten visited
    circle.classList.add('node-body');
    if (visitedNodes.has(node.id)) circle.classList.add('node-visited');
    // Tool sub-type tinting: skills/consult → green ; default stays orange
    if (node.type === 'tool') {
      const tid = (node.id || '').toLowerCase();
      if (tid.includes('skill') || tid.includes('consult'))
        circle.setAttribute('fill', '#22c55e');
    }

    // Opaque background disc — masks edge lines that pass through the node centre
    const bgCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    bgCircle.setAttribute("cx", "0");
    bgCircle.setAttribute("cy", "0");
    bgCircle.setAttribute("r", radius);
    bgCircle.setAttribute("fill", "#0b0f1a");
    bgCircle.setAttribute("stroke", "none");

    if (node.type === "user") {
      circle.classList.add("node-glow");
    }

    const icon = document.createElementNS("http://www.w3.org/2000/svg", "g");
    icon.setAttribute("fill", "none");
    icon.setAttribute("stroke", "rgba(15,23,42,0.85)");
    icon.setAttribute("stroke-width", "2");

    if (node.type === "agent") {
      const hex = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      hex.setAttribute("points", "0,-10 9,-5 9,5 0,10 -9,5 -9,-5");
      icon.appendChild(hex);
    } else if (node.type === "tool") {
      const square = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      square.setAttribute("x", "-8");
      square.setAttribute("y", "-8");
      square.setAttribute("width", "16");
      square.setAttribute("height", "16");
      square.setAttribute("rx", "3");
      icon.appendChild(square);
      const notch = document.createElementNS("http://www.w3.org/2000/svg", "line");
      notch.setAttribute("x1", "-8");
      notch.setAttribute("y1", "0");
      notch.setAttribute("x2", "8");
      notch.setAttribute("y2", "0");
      icon.appendChild(notch);
    } else if (node.type === "memory") {
      const top = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
      top.setAttribute("cx", "0");
      top.setAttribute("cy", "-6");
      top.setAttribute("rx", "10");
      top.setAttribute("ry", "4");
      const body = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      body.setAttribute("x", "-10");
      body.setAttribute("y", "-6");
      body.setAttribute("width", "20");
      body.setAttribute("height", "12");
      body.setAttribute("rx", "4");
      icon.appendChild(body);
      icon.appendChild(top);
    } else if (node.type === "infra") {
      const bar1 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bar1.setAttribute("x", "-10");
      bar1.setAttribute("y", "-8");
      bar1.setAttribute("width", "20");
      bar1.setAttribute("height", "5");
      const bar2 = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      bar2.setAttribute("x", "-10");
      bar2.setAttribute("y", "2");
      bar2.setAttribute("width", "20");
      bar2.setAttribute("height", "5");
      icon.appendChild(bar1);
      icon.appendChild(bar2);
    } else if (node.type === "llm") {
      const wave = document.createElementNS("http://www.w3.org/2000/svg", "path");
      wave.setAttribute(
        "d",
        "M -10 2 Q -5 -6 0 2 T 10 2"
      );
      icon.appendChild(wave);
    } else if (node.type === "user") {
      const head = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      head.setAttribute("cx", "0");
      head.setAttribute("cy", "-6");
      head.setAttribute("r", "5");
      const shoulders = document.createElementNS("http://www.w3.org/2000/svg", "path");
      shoulders.setAttribute(
        "d",
        "M -10 8 Q 0 -2 10 8"
      );
      icon.appendChild(head);
      icon.appendChild(shoulders);
    }

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", "0");
    label.setAttribute("y", "48");
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("fill", "#e2e8f0");
    label.setAttribute("font-size", "12");
    label.setAttribute("font-family", "IBM Plex Mono, monospace");
    label.textContent = node.label || formatNodeLabel(node.id);

    group.appendChild(bgCircle);
    group.appendChild(circle);
    group.appendChild(icon);
    group.appendChild(label);

    // DP badge — only for agent nodes when pref is on
    if (node.type === 'agent' && node.pattern && getTopoPref('showDP', true)) {
      const dp = node.pattern;
      const badgeG = document.createElementNS("http://www.w3.org/2000/svg", "g");
      const pill = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      const bw = dp.length * 6.2 + 10;
      pill.setAttribute("x", String(-bw / 2));
      pill.setAttribute("y", "54");
      pill.setAttribute("width", String(bw));
      pill.setAttribute("height", "14");
      pill.setAttribute("rx", "4");
      pill.setAttribute("fill", "rgba(124,92,255,0.13)");
      const dpText = document.createElementNS("http://www.w3.org/2000/svg", "text");
      dpText.setAttribute("x", "0");
      dpText.setAttribute("y", "64");
      dpText.setAttribute("text-anchor", "middle");
      dpText.setAttribute("font-size", "9");
      dpText.setAttribute("font-family", "IBM Plex Mono, monospace");
      dpText.setAttribute("fill", "rgba(167,139,250,0.9)");
      dpText.setAttribute("letter-spacing", "0.04em");
      dpText.textContent = dp.toUpperCase();
      badgeG.appendChild(pill);
      badgeG.appendChild(dpText);
      group.appendChild(badgeG);
    }

    // Hover tooltip wiring
    if (getTopoPref('hoverTooltips', true)) {
      const tooltip = document.getElementById('topo-tooltip');
      const svgContainer = svg.closest('#layout-container') || svg.parentElement;
      group.addEventListener('mouseenter', () => {
        if (!tooltip || !svgContainer) return;
        const rows = [['id', node.id], ['type', node.type]];
        if (node.label && node.label !== node.id) rows.push(['name', node.label]);
        if (node.pattern) rows.push(['pattern', node.pattern]);
        if (node.llm_model) rows.push(['model', node.llm_model.split('/').pop()]);
        if (node._for_agent) rows.push(['agent', node._for_agent]);
        tooltip.innerHTML = rows.map(([k, v]) => `<div data-key="${k}">${v}</div>`).join('');
        tooltip.classList.add('is-visible');
        tooltip.setAttribute('aria-hidden', 'false');
      });
      group.addEventListener('mousemove', (e) => {
        if (!tooltip || !svgContainer) return;
        const rect = svgContainer.getBoundingClientRect();
        let tx = e.clientX - rect.left + 14;
        let ty = e.clientY - rect.top + 14;
        if (tx + 200 > rect.width) tx = e.clientX - rect.left - 160;
        tooltip.style.left = `${tx}px`;
        tooltip.style.top = `${ty}px`;
      });
      group.addEventListener('mouseleave', () => {
        if (!tooltip) return;
        tooltip.classList.remove('is-visible');
        tooltip.setAttribute('aria-hidden', 'true');
      });
    }

    group.setAttribute("data-node-id", node.id);
    svg.appendChild(group);
    nodeState.set(node.id, circle);
    nodeGroups.set(node.id, group);
    group.addEventListener("pointerdown", (event) => startDrag(event, node.id));
  }); // data.nodes.forEach

  currentPositions = new Map(positions);
} // drawTopology

// View-menu toggle wiring for topology prefs
(function setupTopoPrefToggles() {
  const dpToggle = document.getElementById('toggle-dp-badge');
  const hoverToggle = document.getElementById('toggle-hover-tooltips');
  if (dpToggle) {
    dpToggle.checked = getTopoPref('showDP', true);
    dpToggle.addEventListener('change', () => {
      setTopoPref('showDP', dpToggle.checked);
      if (cachedTopology) drawTopology(cachedTopology);
    });
  }
  if (hoverToggle) {
    hoverToggle.checked = getTopoPref('hoverTooltips', true);
    hoverToggle.addEventListener('change', () => {
      setTopoPref('hoverTooltips', hoverToggle.checked);
      if (cachedTopology) drawTopology(cachedTopology);
    });
  }
})();
// NODE_ACTIVE_MS: how long node stays hot — must be slightly longer than pulseDuration
// so the token visually clears as soon as the bump arrives; then spinner re-appears
const NODE_ACTIVE_MS = 700;

function setNodeActive(nodeId, now) {
  const circle = nodeState.get(nodeId);
  if (!circle) return;
  nodeActiveUntil.set(nodeId, now + NODE_ACTIVE_MS);
  circle.classList.add("node-active");
  // Suppress spinner while token is held — pruneActive will restore it on expiry
  circle.classList.remove("node-waiting");
}

const nodeWorkingUntil = new Map();
const nodeWaitingUntil = new Map();
const nodeUnavailable = new Set();
const persistentNodeClasses = new Map();

function setNodeStatus(nodeId, status, now, windowMs = ACTIVE_WINDOW_MS) {
  const circle = nodeState.get(nodeId);
  if (!circle) return;
  if (status === "working") {
    nodeWorkingUntil.set(nodeId, now + windowMs);
    circle.classList.add("node-working");
    circle.classList.remove("node-waiting");
    return;
  }
  if (status === "waiting") {
    nodeWaitingUntil.set(nodeId, now + windowMs);
    circle.classList.add("node-waiting");
    circle.classList.remove("node-working");
    return;
  }
  if (status === "unavailable") {
    nodeUnavailable.add(nodeId);
    circle.classList.add("node-unavailable");
  }
}

function setNodePersistentClass(nodeId, className) {
  const circle = nodeState.get(nodeId);
  if (!circle) return;
  circle.classList.add(className);
  // Trail: permanently mark this node as visited on first active token
  if (className === 'node-active') {
    visitedNodes.add(nodeId);
    circle.classList.add('node-visited');
  }
  const set = persistentNodeClasses.get(nodeId) || new Set();
  set.add(className);
  persistentNodeClasses.set(nodeId, set);
}

function clampNumber(value, min, max) {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function extractVolumeHint(event) {
  if (!event) return 1;
  if (Number.isFinite(event.volume)) return event.volume;

  const payload = event.payload || {};
  const sources = event.sources;
  if (Array.isArray(sources)) return sources.length;

  const candidates = [
    payload.items,
    payload.result?.items,
    payload.output?.items,
    payload.response?.items,
    event.result?.items,
    event.output?.items,
  ];
  for (const value of candidates) {
    if (Array.isArray(value)) return value.length;
  }

  const numericCandidates = [
    payload.count,
    payload.result?.count,
    payload.output?.count,
    payload.response?.count,
    event.result?.count,
    event.output?.count,
  ];
  for (const value of numericCandidates) {
    if (Number.isFinite(value)) return value;
  }

  return 1;
}

function estimatePulseCount(event) {
  const raw = extractVolumeHint(event);
  const volume = Math.max(1, Math.floor(raw));
  // Log scaling: volume 1 -> 1 pulse, 2-3 -> 2, 4-7 -> 3, 8-15 -> 4, etc.
  const pulses = volume <= 1 ? 1 : 1 + Math.floor(Math.log2(volume));
  return clampNumber(pulses, 1, 8);
}

function clearEdgePulse(line) {
  const timers = edgePulseTimers.get(line);
  if (timers) {
    timers.forEach((id) => clearTimeout(id));
  }
  edgePulseTimers.delete(line);
  line.classList.remove("edge-volume-pulse");
}

function pulseEdge(line, pulseCount) {
  if (!line) return;
  clearEdgePulse(line);

  const count = clampNumber(pulseCount, 1, 8);
  const timers = [];

  // A short burst of pulses; each pulse toggles a filter-based glow.
  const stepMs = 120;
  const onMs = 70;

  // Start immediately (same tick) so the pulse is visible
  // even when edge flow animation would not have progressed yet.
  line.classList.add("edge-volume-pulse");
  timers.push(
    setTimeout(() => {
      line.classList.remove("edge-volume-pulse");
    }, onMs)
  );

  for (let i = 1; i < count; i += 1) {
    const base = i * stepMs;
    timers.push(
      setTimeout(() => {
        line.classList.add("edge-volume-pulse");
      }, base),
      setTimeout(() => {
        line.classList.remove("edge-volume-pulse");
      }, base + onMs)
    );
  }

  edgePulseTimers.set(line, timers);
}

function clearNodePersistentClass(nodeId, className) {
  const circle = nodeState.get(nodeId);
  if (!circle) return;
  circle.classList.remove(className);
  const set = persistentNodeClasses.get(nodeId);
  if (!set) return;
  set.delete(className);
  if (!set.size) persistentNodeClasses.delete(nodeId);
}

function setEdgeActive(from, to, now, flow = "request", pulseCount = 1, activeMs = null) {
  const key = `${from}->${to}`;
  let line = edgeState.get(key);
  let resolvedKey = key;
  if (!line) {
    // Response flows travel in reverse — try the drawn edge in the opposite direction
    const reverseKey = `${to}->${from}`;
    line = edgeState.get(reverseKey);
    resolvedKey = reverseKey;
  }
  if (!line) return;

  const windowMs = Number.isFinite(activeMs) ? activeMs : ACTIVE_WINDOW_MS;
  edgeActiveUntil.set(resolvedKey, Math.max(edgeActiveUntil.get(resolvedKey) || 0, now + windowMs));
  line.classList.add("edge-active");
  line.classList.remove("edge-flow--request", "edge-flow--response");
  if (flow === "response") {
    line.classList.add("edge-flow--response");
  } else {
    line.classList.add("edge-flow--request");
  }

  // Per-event emphasis burst (1..8 pulses) to reflect volume.
  pulseEdge(line, pulseCount);
}

function clearEdgeActive(from, to) {
  // Clear both directions — response flows may activate the reverse edge
  for (const key of [`${from}->${to}`, `${to}->${from}`]) {
    edgeActiveUntil.delete(key);
    const line = edgeState.get(key);
    if (line) {
      line.classList.remove("edge-active", "edge-flow--request", "edge-flow--response", "edge-volume-pulse");
      clearEdgePulse(line);
    }
  }
}

function pruneActive(now) {
  for (const [nodeId, until] of nodeActiveUntil.entries()) {
    if (until <= now) {
      nodeActiveUntil.delete(nodeId);
      nodeState.get(nodeId)?.classList.remove("node-active");
    }
  }
  for (const [nodeId, until] of nodeWorkingUntil.entries()) {
    if (until <= now) {
      nodeWorkingUntil.delete(nodeId);
      nodeState.get(nodeId)?.classList.remove("node-working");
    }
  }
  for (const [nodeId, until] of nodeWaitingUntil.entries()) {
    if (until <= now) {
      nodeWaitingUntil.delete(nodeId);
      nodeState.get(nodeId)?.classList.remove("node-waiting");
    }
  }
  for (const nodeId of nodeUnavailable) {
    nodeState.get(nodeId)?.classList.add("node-unavailable");
  }
  for (const [nodeId, classSet] of persistentNodeClasses.entries()) {
    const circle = nodeState.get(nodeId);
    if (!circle) continue;
    // While a node holds the active token, suppress its waiting spinner
    // Suppress waiting spinner whenever persistent node-active is set (not just timer-based)
    const hasToken = (nodeActiveUntil.get(nodeId) ?? 0) > now || classSet.has('node-active');
    classSet.forEach((name) => {
      if (hasToken && name === "node-waiting") {
        circle.classList.remove("node-waiting"); // keep suppressed
        return;
      }
      circle.classList.add(name);
    });
  }
  for (const [edgeKey, until] of edgeActiveUntil.entries()) {
    if (until <= now) {
      edgeActiveUntil.delete(edgeKey);
      const line = edgeState.get(edgeKey);
      line?.classList.remove("edge-active", "edge-flow--request", "edge-flow--response", "edge-volume-pulse");
      if (line) clearEdgePulse(line);
    }
  }
}

// Run pruneActive on a dedicated fast timer so node glows and spinners expire
// promptly. NODE_ACTIVE_MS=700ms but refresh() only runs every 2000ms — without
// this interval nodes stay red for up to 2700ms and spinners never come back.
setInterval(() => pruneActive(Date.now()), 100);

function extractTextContent(event) {
  if (!event) return "";
  // Prefer human-readable strings over JSON blobs.
  const output = event.output ?? event.payload?.output;
  if (typeof output?.content === "string" && output.content.trim()) return output.content.trim();

  // LLM call_end: response content in event.response
  const response = event.response ?? event.payload?.response;
  if (typeof response?.content === "string" && response.content.trim()) return response.content.trim();
  if (typeof response === "string" && response.trim()) return response.trim();

  const result = event.result ?? event.payload?.result;
  if (result && typeof result === "object") {
    const status = result.status;
    if (typeof status === "string" && status.trim()) return status.trim();
  }

  const input = event.input ?? event.payload?.input;
  if (typeof input?.prompt === "string" && input.prompt.trim()) return input.prompt.trim();
  const taskPrompt = input?.task?.prompt;
  if (typeof taskPrompt === "string" && taskPrompt.trim()) return taskPrompt.trim();

  if ((event.kind === "tool_call" || event.kind === "tool_result" || event.kind === "memory_read" || event.kind === "memory_write" || event.kind === "memory_result") && event.target) {
    return String(event.target);
  }

  const maybe = event.payload?.request?.tool_name || event.payload?.path;
  if (typeof maybe === "string" && maybe.trim()) return maybe.trim();

  return "";
}

function resolveTarget(event) {
  const kind = event.kind || "";
  const target = event.target || event.to;

  // Already typed
  if (target?.includes(":")) return target;

  // Pattern-based resolution
  if (kind.includes("tool")) {
    // tool_call_start/end carry the name in tool_name, not target
    const name = target || event.tool_name;
    return name ? `tool:${name}` : "tool:unknown";
  }
  if (kind.includes("memory")) {
    return target ? `memory:${target}` : "memory:unknown";
  }
  if (kind.includes("llm")) {
    // llm_call_start/end carry the model in the model field
    const model = event.model;
    if (model) {
      // Use the last segment as a compact label (e.g. "gemini-3-pro-preview")
      const shortModel = model.split("/").pop();
      return `llm:${shortModel}`;
    }
    return "llm:default";
  }
  if (kind.includes("agent")) {
    return target || null;
  }
  if (kind.includes("user") || kind.includes("human") || kind.includes("interaction")) {
    return "user";
  }

  return target || null;
}

function resolveTrafficEdge(event, agentId, resolvedTarget) {
  if (!event) return null;
  
  const pattern = classifyEventPattern(event.kind);
  
  // User input
  if (pattern === "user_input") {
    return agentId ? { from: "user", to: agentId } : null;
  }
  
  // User output
  if (pattern === "user_output") {
    return agentId ? { from: agentId, to: "user" } : null;
  }
  
  // Outbound network
  if (pattern === "outbound") {
    return agentId && resolvedTarget ? { from: agentId, to: resolvedTarget } : null;
  }
  
  // Inbound network
  if (pattern === "inbound") {
    return agentId && resolvedTarget ? { from: resolvedTarget, to: agentId } : null;
  }

  if (event.kind === "tool_call" || event.kind === "memory_read" || event.kind === "memory_write") {
    return agentId && resolvedTarget ? { from: agentId, to: resolvedTarget } : null;
  }
  if (event.kind === "tool_result" || event.kind === "memory_result") {
    return agentId && resolvedTarget ? { from: resolvedTarget, to: agentId } : null;
  }
  if (event.kind === "tool_unavailable" || event.kind === "memory_unavailable") {
    return agentId && resolvedTarget ? { from: agentId, to: resolvedTarget } : null;
  }

  return null;
}

function formatNodeLabel(id) {
  if (!id) return "";
  return id.replace(/^(tool|memory|llm):/i, "");
}

function ensureNode(topology, id, type) {
  if (!id) return false;
  if (topology.nodes.find((node) => node.id === id)) return false;
  topology.nodes.push({ id, type });
  return true;
}

function ensureEdge(topology, from, to, type) {
  if (!from || !to) return false;
  if (topology.edges.find((edge) => edge.from === from && edge.to === to)) return false;
  topology.edges.push(type ? { from, to, type } : { from, to });
  return true;
}

function updateNode(topology, object) {
  if (!object?.id) return false;
  const existing = topology.nodes.find((node) => node.id === object.id);
  if (existing) {
    if (object.type) {
      existing.type = object.type;
    }
    if (object.label) {
      existing.label = object.label;
    }
    return true;
  }
  topology.nodes.push({ id: object.id, type: object.type || "tool", label: object.label });
  return true;
}

function removeNode(topology, id) {
  const nodeIndex = topology.nodes.findIndex((node) => node.id === id);
  if (nodeIndex < 0) return false;
  topology.nodes.splice(nodeIndex, 1);
  topology.edges = topology.edges.filter((edge) => edge.from !== id && edge.to !== id);
  return true;
}

function discoverFromEvents(topology, events) {
  let changed = false;
  events.forEach((event) => {
    // Agent-to-agent communication (pattern-based)
    if (event.kind?.includes("agent") && event.target && event.agent_id) {
      changed = ensureNode(topology, event.target, "agent") || changed;
      const pattern = classifyEventPattern(event.kind);
      if (pattern === "inbound") {
        changed = ensureEdge(topology, event.target, event.agent_id) || changed;
      } else if (pattern === "outbound") {
        changed = ensureEdge(topology, event.agent_id, event.target) || changed;
      }
      return;
    }
    // When an llm_call_start arrives with a model name, label the LLM node
    if ((event.kind === 'llm_call_start' || event.kind === 'llm_call_end') && event.agent_id && event.model) {
      const llmId = `${event.agent_id}__llm`;
      const shortModel = event.model.split('/').pop();
      const existing = topology.nodes.find(n => n.id === llmId);
      if (existing) {
        if (existing.label !== shortModel) { existing.label = shortModel; changed = true; }
      } else {
        topology.nodes.push({ id: llmId, type: 'llm', label: shortModel, _for_agent: event.agent_id });
        changed = true;
      }
      return;
    }
    if (event.kind === "object_upsert" || event.kind === "object_update") {
      const object = event.payload?.object || event.payload;
      changed = updateNode(topology, object) || changed;
      return;
    }
    if (event.kind === "object_delete") {
      const object = event.payload?.object || event.payload;
      changed = removeNode(topology, object?.id) || changed;
      return;
    }
    if (event.kind === "object_link") {
      const link = event.payload?.link || event.payload;
      changed = ensureEdge(topology, link?.source, link?.target, link?.type) || changed;
      return;
    }
    if (event.kind === "object_unlink") {
      const link = event.payload?.link || event.payload;
      if (!link?.source || !link?.target) {
        return;
      }
      const before = topology.edges.length;
      topology.edges = topology.edges.filter(
        (edge) => !(edge.from === link.source && edge.to === link.target)
      );
      changed = topology.edges.length !== before || changed;
      return;
    }
    if (event.agent_id) {
      changed = ensureNode(topology, event.agent_id, "agent") || changed;
    }
    const target = resolveTarget(event);
    if (target?.startsWith("tool:")) {
      changed = ensureNode(topology, target, "tool") || changed;
    } else if (target?.startsWith("memory:")) {
      changed = ensureNode(topology, target, "memory") || changed;
    } else if (target?.startsWith("llm:") && target !== "llm:default") {
      // Only add named LLM nodes; "llm:default" is a fallback label, not a real node
      changed = ensureNode(topology, target, "llm") || changed;
    } else if (target && !target.startsWith("llm:")) {
      changed = ensureNode(topology, target, "tool") || changed;
    }
    if (event.agent_id && target) {
      const recvKinds = new Set(["llm_response", "tool_result", "memory_result"]);
      if (recvKinds.has(event.kind)) {
        changed = ensureEdge(topology, target, event.agent_id) || changed;
      } else {
        changed = ensureEdge(topology, event.agent_id, target) || changed;
      }
    }
  });
  return changed;
}

function formatMetricLabel(key) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function buildTimelineTree(events) {
  // Build a hierarchical structure from events
  const tree = { agent_calls: [], calls: new Map() };
  const agentStates = new Map(); // track last state per agent
  
  events.forEach((event, index) => {
    const agentId = resolveAgentId(event);
    if (!agentId) return;
    
    // Track LLM/tool/memory calls per agent
    if (event.kind === "llm_call" || event.kind === "llm_response") {
      if (!tree.calls.has(agentId)) {
        tree.calls.set(agentId, []);
      }
      tree.calls.get(agentId).push({
        type: "llm",
        kind: event.kind,
        index,
        timestamp: event.timestamp,
        event,
      });
    } else if (event.kind === "tool_call" || event.kind === "tool_result") {
      if (!tree.calls.has(agentId)) {
        tree.calls.set(agentId, []);
      }
      tree.calls.get(agentId).push({
        type: "tool",
        kind: event.kind,
        target: event.target,
        index,
        timestamp: event.timestamp,
        event,
      });
    } else if (event.kind === "memory_read" || event.kind === "memory_write" || event.kind === "memory_result") {
      if (!tree.calls.has(agentId)) {
        tree.calls.set(agentId, []);
      }
      tree.calls.get(agentId).push({
        type: "memory",
        kind: event.kind,
        target: event.target,
        index,
        timestamp: event.timestamp,
        event,
      });
    }
    
    // Track agent state changes (simplified)
    if (event.kind === "audit" || event.kind === "llm_response") {
      const state = event.kind === "audit" ? "input" : "processing";
      agentStates.set(agentId, { state, index, event });
    }
  });
  
  return tree;
}

function buildTimelineGraphTurns(events) {
  // Graph view: each "turn" is a compact row per agent showing audit/tool/memory/llm nodes.
  const turns = [];
  const activeTurnByAgent = new Map();

  const sorted = [...events].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  sorted.forEach((event) => {
    const agentId = resolveAgentId(event);
    if (!agentId) return;

    const kind = event.kind;
    const isTurnStart = kind === "audit";
    if (isTurnStart || !activeTurnByAgent.has(agentId)) {
      const turn = { agentId, nodes: [], start: event.timestamp || 0, end: event.timestamp || 0 };
      turns.push(turn);
      activeTurnByAgent.set(agentId, turn);
    }

    const turn = activeTurnByAgent.get(agentId);
    if (!turn) return;
    turn.end = Math.max(turn.end, event.timestamp || 0);

    if (kind === "audit") {
      turn.nodes.push({ type: "audit", label: "audit" });
      return;
    }
    if (kind === "tool_call") {
      const name = event.target || event.payload?.tool_name || "tool";
      turn.nodes.push({ type: "tool", label: name });
      return;
    }
    if (kind === "tool_result") {
      const name = event.target || event.payload?.tool_name || "tool";
      turn.nodes.push({ type: "tool", label: `${name}:result` });
      return;
    }
    if (kind === "memory_read" || kind === "memory_write") {
      const name = event.target || event.payload?.memory_type || "memory";
      turn.nodes.push({ type: "memory", label: name });
      return;
    }
    if (kind === "memory_result") {
      const name = event.target || event.payload?.memory_type || "memory";
      turn.nodes.push({ type: "memory", label: `${name}:result` });
      return;
    }
    if (kind === "llm_call") {
      turn.nodes.push({ type: "llm", label: "llm_call" });
      return;
    }
    if (kind === "llm_response") {
      turn.nodes.push({ type: "llm", label: "llm_response" });
      activeTurnByAgent.delete(agentId);
      return;
    }
  });

  turns.sort((a, b) => a.end - b.end);
  return turns;
}

function renderTimelineGraph(shouldScroll = false) {
  if (!timelineGraph) return;
  timelineGraph.innerHTML = "";

  const turns = buildTimelineGraphTurns(timelineEvents);
  if (!turns.length) {
    const empty = document.createElement("div");
    empty.className = "timeline-graph-empty";
    empty.textContent = "No events yet";
    timelineGraph.appendChild(empty);
    return;
  }

  // Visual constants
  const rowHeight = 60; // Increased height to allow wrapping
  const dotStart = 100;
  const dotSpacing = 14;
  const maxDotsPerRow = 15;
  const nodeRadius = 14; // Bigger nodes as requested

  // Calculate dynamic height based on turns and wrapped lines
  let currentY = 30;
  const turnMetas = turns.map(turn => {
      const dotCount = turn.nodes.length;
      const lines = Math.ceil(dotCount / maxDotsPerRow) || 1;
      const height = Math.max(rowHeight, lines * 20 + 20);
      const y = currentY;
      currentY += height;
      return { ...turn, y, height };
  });

  // Create SVG container
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.style.width = "100%";
  svg.setAttribute("height", currentY + 20);
  timelineGraph.appendChild(svg);

  // Draw vertical flow line connecting all agent nodes
  if (turnMetas.length > 1) {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", "30");
      line.setAttribute("y1", turnMetas[0].y);
      line.setAttribute("x2", "30");
      line.setAttribute("y2", turnMetas[turnMetas.length - 1].y);
      line.setAttribute("stroke", "#475569"); // Slate-600
      line.setAttribute("stroke-width", "2");
      svg.appendChild(line);
  }

  turnMetas.forEach((turn) => {
    // Turn Node (Agent Start)
    const group = document.createElementNS(svgNS, "g");
    group.setAttribute("transform", `translate(30, ${turn.y})`); // x=30 to center on vertical line
    
    // Circle main node
    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("r", nodeRadius);
    circle.setAttribute("fill", palette.agent || "#38bdf8");
    circle.setAttribute("stroke", "#0f172a");
    circle.setAttribute("stroke-width", "2");
    
    // Label inside or below? Inside if short.
    // User requested "nodes", let's put refined initials or icon.
    // Putting full ID next to it.
    
    const textGroup = document.createElementNS(svgNS, "text");
    textGroup.setAttribute("x", "24");
    textGroup.setAttribute("y", "5");
    textGroup.setAttribute("fill", "#e2e8f0");
    textGroup.setAttribute("font-family", "IBM Plex Mono, monospace");
    textGroup.setAttribute("font-size", "13");
    textGroup.setAttribute("font-weight", "600");
    textGroup.textContent = turn.agentId;

    group.appendChild(circle);
    group.appendChild(textGroup);
    svg.appendChild(group);

    // Render nodes with wrapping (matching topology design)
    turn.nodes.forEach((node, nodeIndex) => {
        const row = Math.floor(nodeIndex / maxDotsPerRow);
        const col = nodeIndex % maxDotsPerRow;
        
        const dotX = dotStart + col * dotSpacing;
        const dotY = turn.y + row * 20; // 20px line height for nodes
        
        const nodeRadius = 8; // Smaller than topology (20), but larger than old dots (4)
        
        // Create node group
        const nodeGroup = document.createElementNS(svgNS, "g");
        nodeGroup.setAttribute("transform", `translate(${dotX} ${dotY})`);
        nodeGroup.style.cursor = "help";
        
        // Create circle
        const circle = document.createElementNS(svgNS, "circle");
        circle.setAttribute("cx", "0");
        circle.setAttribute("cy", "0");
        circle.setAttribute("r", nodeRadius);
        
        let color = palette[node.type] || palette.unknown;
        if (node.type === "tool" && node.label.includes(":result")) color = "#fb923c"; // Lighter orange
        
        circle.setAttribute("fill", color);
        circle.setAttribute("fill-opacity", "0.8");
        circle.setAttribute("stroke", "rgba(255,255,255,0.35)");
        circle.setAttribute("stroke-width", "1.5");
        
        // Create icon (scaled down from topology)
        const icon = document.createElementNS(svgNS, "g");
        icon.setAttribute("fill", "none");
        icon.setAttribute("stroke", "rgba(15,23,42,0.85)");
        icon.setAttribute("stroke-width", "1.2");
        
        const iconScale = 0.5; // 50% of topology size
        if (node.type === "agent") {
          const hex = document.createElementNS(svgNS, "polygon");
          hex.setAttribute("points", "0,-5 4.5,-2.5 4.5,2.5 0,5 -4.5,2.5 -4.5,-2.5");
          icon.appendChild(hex);
        } else if (node.type === "tool") {
          const square = document.createElementNS(svgNS, "rect");
          square.setAttribute("x", "-4");
          square.setAttribute("y", "-4");
          square.setAttribute("width", "8");
          square.setAttribute("height", "8");
          square.setAttribute("rx", "1.5");
          icon.appendChild(square);
        } else if (node.type === "memory") {
          const top = document.createElementNS(svgNS, "ellipse");
          top.setAttribute("cx", "0");
          top.setAttribute("cy", "-3");
          top.setAttribute("rx", "5");
          top.setAttribute("ry", "2");
          const body = document.createElementNS(svgNS, "rect");
          body.setAttribute("x", "-5");
          body.setAttribute("y", "-3");
          body.setAttribute("width", "10");
          body.setAttribute("height", "6");
          body.setAttribute("rx", "2");
          icon.appendChild(body);
          icon.appendChild(top);
        } else if (node.type === "llm") {
          const wave = document.createElementNS(svgNS, "path");
          wave.setAttribute("d", "M -5 1 Q -2.5 -3 0 1 T 5 1");
          icon.appendChild(wave);
        } else if (node.type === "user") {
          const head = document.createElementNS(svgNS, "circle");
          head.setAttribute("cx", "0");
          head.setAttribute("cy", "-3");
          head.setAttribute("r", "2.5");
          const shoulders = document.createElementNS(svgNS, "path");
          shoulders.setAttribute("d", "M -5 4 Q 0 -1 5 4");
          icon.appendChild(head);
          icon.appendChild(shoulders);
        }
        
        // Add tooltip
        const title = document.createElementNS(svgNS, "title");
        title.textContent = `${node.type}: ${node.label}`;
        nodeGroup.appendChild(title);
        
        nodeGroup.appendChild(circle);
        nodeGroup.appendChild(icon);
        svg.appendChild(nodeGroup);
    });
  });

  if (shouldScroll && timelineAutoScroll && timelineGraph.parentElement) {
    timelineGraph.parentElement.scrollTop = timelineGraph.parentElement.scrollHeight;
  }
}

function renderTimeline(shouldScroll = false) {
  if (timelineViewMode === "graph") {
    renderTimelineGraph(shouldScroll);
    return;
  }

  if (!timelineTree) return;

  timelineTree.innerHTML = "";

  if (!timelineEvents.length) {
    const emptyText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    emptyText.setAttribute("x", "140");
    emptyText.setAttribute("y", "350");
    emptyText.setAttribute("text-anchor", "middle");
    emptyText.setAttribute("fill", "#94a3b8");
    emptyText.setAttribute("font-family", "IBM Plex Mono, monospace");
    emptyText.setAttribute("font-size", "11");
    emptyText.textContent = "No events yet";
    timelineTree.appendChild(emptyText);
    return;
  }

  const tree = buildTimelineTree(timelineEvents);
  const agentIds = Array.from(tree.calls.keys());
  const gridSize = 60;
  const startY = 40;
  const startX = 20;

  // Render agent rows (vertical axis)
  agentIds.forEach((agentId, rowIndex) => {
    const y = startY + rowIndex * gridSize;
    const calls = tree.calls.get(agentId) || [];
    const isExpanded = expandedGroups.has(agentId);


    // Agent Icon Group
    const iconGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    iconGroup.setAttribute("transform", `translate(${startX}, ${y - 4}) scale(0.8)`); // Scale down icon a bit
    
    // Hexagon background (Agent style)
    const hex = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
    hex.setAttribute("points", "0,-10 9,-5 9,5 0,10 -9,5 -9,-5");
    hex.setAttribute("fill", palette.agent);
    hex.setAttribute("stroke", "#0f172a");
    hex.setAttribute("stroke-width", "2");
    iconGroup.appendChild(hex);

    // Agent label
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", startX + 16); // Shift text right
    label.setAttribute("y", y + 4);
    label.setAttribute("fill", palette.agent);
    label.setAttribute("font-family", "IBM Plex Mono, monospace");
    label.setAttribute("font-size", "10");
    label.setAttribute("cursor", "pointer");
    label.textContent = `${agentId}${isExpanded ? " ▾" : " ▸"}`;
    label.addEventListener("click", () => toggleTimelineGroup(agentId));
    
    timelineTree.appendChild(iconGroup);
    timelineTree.appendChild(label);

    if (!isExpanded) {
      // Collapsed: show summary count
      const summary = document.createElementNS("http://www.w3.org/2000/svg", "text");
      summary.setAttribute("x", startX + 120);
      summary.setAttribute("y", y + 4);
      summary.setAttribute("fill", "#94a3b8");
      summary.setAttribute("font-family", "IBM Plex Mono, monospace");
      summary.setAttribute("font-size", "9");
      summary.textContent = `${calls.length} calls`;
      timelineTree.appendChild(summary);
    } else {
      // Expanded: show calls horizontally
      calls.slice(0, 12).forEach((call, colIndex) => {
        const x = startX + 120 + colIndex * 12;
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", x);
        circle.setAttribute("cy", y);
        circle.setAttribute("r", "4");
        const color = call.type === "llm" ? palette.llm : call.type === "tool" ? palette.tool : palette.memory;
        circle.setAttribute("fill", color);
        circle.setAttribute("opacity", "0.8");
        circle.setAttribute("cursor", "pointer");

        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `${call.type}: ${call.kind}`;
        circle.appendChild(title);

        timelineTree.appendChild(circle);
      });

      if (calls.length > 12) {
        const more = document.createElementNS("http://www.w3.org/2000/svg", "text");
        more.setAttribute("x", startX + 120 + 12 * 12);
        more.setAttribute("y", y + 4);
        more.setAttribute("fill", "#94a3b8");
        more.setAttribute("font-family", "IBM Plex Mono, monospace");
        more.setAttribute("font-size", "8");
        more.textContent = `+${calls.length - 12}`;
        timelineTree.appendChild(more);
      }
    }
  });

  // Adjust viewBox if needed
  const height = Math.max(700, startY + agentIds.length * gridSize + 40);
  timelineTree.setAttribute("viewBox", `0 0 280 ${height}`);

  // Auto-scroll to bottom if enabled
  if (shouldScroll && timelineAutoScroll && timelineTree.parentElement) {
    timelineTree.parentElement.scrollTop = timelineTree.parentElement.scrollHeight;
  }
}

function toggleTimelineGroup(agentId) {
  if (expandedGroups.has(agentId)) {
    expandedGroups.delete(agentId);
  } else {
    expandedGroups.add(agentId);
  }
  renderTimeline();
}

function setTimelineViewMode(mode) {
  timelineViewMode = mode;

  const tabTree = document.getElementById("timeline-tab-tree");
  const tabGraph = document.getElementById("timeline-tab-graph");
  if (tabTree && tabGraph) {
    tabTree.classList.toggle("is-active", mode === "tree");
    tabGraph.classList.toggle("is-active", mode === "graph");
    tabTree.setAttribute("aria-selected", mode === "tree" ? "true" : "false");
    tabGraph.setAttribute("aria-selected", mode === "graph" ? "true" : "false");
  }

  if (timelineTree) {
    timelineTree.classList.toggle("is-hidden", mode !== "tree");
  }
  if (timelineGraph) {
    timelineGraph.classList.toggle("is-hidden", mode !== "graph");
  }
  renderTimeline();
}

function formatLogLine(event) {
  const agentId = resolveAgentId(event);
  const agent = agentId ? `agent(${agentId})` : "system";
  const prompt = resolvePrompt(event);
  const preview = formatPayloadPreview(event);
  if (event.kind === "audit") {
    if (prompt) {
      return `${agent} audit: ${prompt}`;
    }
    return `${agent} audit${preview}`;
  }
  if (event.kind === "user_input") {
    const text = event.content || prompt || "";
    return text ? `user -> ${agent}: ${text}` : `user -> ${agent} input${preview}`;
  }
  if (event.kind === "llm_call") {
    return `--openai--> llm:default input${preview}`;
  }
  if (event.kind === "llm_response") {
    return `<--openai-- llm:default reply${preview}`;
  }
  if (event.kind === "tool_call") {
    return `-> tool:${event.target || "unknown"} from ${agent}`;
  }
  if (event.kind === "memory_read") {
    return `-> memory:${event.target || "unknown"} from ${agent}`;
  }
  if (event.kind === "tool_server_proxy") {
    const toolName = event.payload?.request?.tool_name;
    if (toolName) {
      return `-> tool:${toolName} from ${agent}`;
    }
    return `-> tool-server ${event.payload?.path || "request"}`;
  }
  return `-> ${agent} ${event.kind}`;
}

function getEntityType(text) {
  if (!text) return "unknown";
  const lower = text.toLowerCase();
  if (lower === "user") return "user";
  if (lower === "llm" || lower.includes("llm:")) return "llm";
  if (lower === "remote_tool" || lower === "system" || lower === "observe" || lower === "eval" || lower === "explain") return "infra";
  if (lower.startsWith("agent")) return "agent";
  if (lower.startsWith("tool:")) return "tool";
  if (lower.startsWith("memory:")) return "memory";
  return "unknown";
}

// Generic event classification by pattern
function classifyEventPattern(kind) {
  const k = (kind || "").toLowerCase();

  // *_end events are inbound responses — must check BEFORE "call" since *_call_end contains "call"
  if (k.endsWith("_end")) return "inbound";

  // routing = agent-to-agent delegation (outbound)
  if (k === "routing") return "outbound";

  // Outbound network patterns
  if (k.includes("call") || k.includes("request") || k.includes("_sent") || k.includes("write") || (k.includes("agent_to_agent") && !k.includes("result"))) {
    return "outbound";
  }

  // Inbound network patterns
  if (k.includes("response") || k.includes("result") || k.includes("_received") || k.includes("reply")) {
    return "inbound";
  }
  
  // User input
  if (k.includes("user_input") || k === "input") {
    return "user_input";
  }
  
  // User output
  if (k.includes("to_user") || k.includes("interaction") || k.includes("user_response")) {
    return "user_output";
  }
  
  return "system";
}

// Extract protocol label from event
function extractProtocol(event) {
  // Check explicit protocol field
  if (event.protocol) return event.protocol;
  if (event.attributes?.protocol) return event.attributes.protocol;
  
  // Infer from kind — use neutral labels; no hardcoded transport protocol names
  const kind = event.kind || "";
  if (kind.includes("llm")) return "";  // use plain → arrow; model shown in target label
  if (kind.includes("agent-remote") || kind.includes("agent_to_agent")) return "agent";
  if (kind.includes("tool") || kind.includes("remote_tool")) return "tool";
  if (kind.includes("memory")) return "memory";
  
  return "";
}

// Resolve target entity from event
function resolveTargetEntity(event) {
  const kind = event.kind || "";
  const target = event.target || event.to || event.tool_name;
  
  // Explicit target with typing
  if (target?.includes(":")) return target;
  
  // Infer target type from context
  if (kind.includes("llm")) {
    const model = event.model;
    return model ? `llm:${model.split("/").pop()}` : "llm:unknown";
  }
  if (kind.includes("user") || kind.includes("human")) return "user";
  if (kind.includes("agent") && target) return `agent(${target})`;
  if (kind.includes("tool") && target) return `tool:${target}`;
  if (kind.includes("memory") && target) return `memory:${target}`;
  if (target) return target;
  
  return "unknown";
}

function buildLogParts(event) {
  const agentId = resolveAgentId(event);
  const agent = agentId ? `agent(${agentId})` : "system";
  const kind = event.kind || "";
  const pattern = classifyEventPattern(kind);
  const protocol = extractProtocol(event);
  const text = extractTextContent(event);
  const preview = formatPayloadPreview(event);
  const prompt = resolvePrompt(event);
  const target = resolveTargetEntity(event);
  const source = event.source || event.from;

  if (kind === "routing") {
    const fromAgent = event.source_agent_id ? `agent(${event.source_agent_id})` : agent;
    const toAgent = event.target_agent_id ? `agent(${event.target_agent_id})` : (target || "unknown");
    return {
      direction: "sent",
      from: fromAgent,
      to: toAgent,
      protocol: "agent",
      detail: event.reason || event.task || "routing",
    };
  }

  if (kind === "audit") {
    return {
      direction: "system",
      from: agent,
      to: "system",
      protocol: "audit",
      detail: prompt || text || `audit${preview}`,
    };
  }
  
  // User input
  if (pattern === "user_input") {
    return {
      direction: "sent",
      from: "user",
      to: agent,
      protocol: "input",
      detail: prompt || text || `input${preview}`,
    };
  }
  
  // User output
  if (pattern === "user_output") {
    const content = event.payload?.content || event.content || text;
    return {
      direction: "received",
      from: agent,
      to: "user",
      protocol: "output",
      detail: content || "message",
    };
  }
  
  // Outbound network (calls, requests, writes)
  if (pattern === "outbound") {
    const operation = event.operation || event.action || event.kind || "send";
    return {
      direction: "sent",
      from: agent,
      to: target,
      protocol: protocol,
      detail: text ? text.trim() : operation.trim(),
    };
  }
  
  // Inbound network (responses, results)
  if (pattern === "inbound") {
    const fromEntity = source ? (source.includes(":") || source.includes("(") ? source : target) : target;
    let detail;
    if (kind.includes("llm")) {
      // Show LLM response content (llm_call_end carries it in response field)
      const content = event.response?.content || event.output?.content || text;
      detail = content ? content.slice(0, 200) : (event.kind || "llm_response");
    } else {
      const status = event.payload?.result?.status || event.result?.status || event.status;
      detail = status ? `result: ${status}` : (text || event.kind || "result");
    }
    return {
      direction: "received",
      from: fromEntity,
      to: agent,
      protocol: protocol,
      detail: detail.trim(),
    };
  }
  
  // System/unknown
  return {
    direction: "system",
    from: agent,
    to: "system",
    protocol: "system",
    detail: event.kind,
  };
}

function formatPayloadPreview(event) {
  // Include a compact snapshot so the log proves input/output is flowing.
  const payload = event.input ?? event.output ?? event.payload;
  if (!payload) {
    return "";
  }
  let text = "";
  if (typeof payload === "string") {
    text = payload;
  } else {
    try {
      text = JSON.stringify(payload);
    } catch (error) {
      return "";
    }
  }
  const trimmed = text.length > 160 ? `${text.slice(0, 160)}...` : text;
  return `: ${trimmed}`;
}

function triggerFlowPulse(from, to, durationMs) {
    if (isInitialLoad) return Promise.resolve();

    // Try the directly drawn edge first
    const directKey  = `${from}->${to}`;
    const reverseKey = `${to}->${from}`;
    let line = edgeState.get(directKey);
    let reversed = false;
    if (!line) {
      line = edgeState.get(reverseKey);
      reversed = true;   // packet must travel AGAINST the drawn direction
    }

    if (line) {
      let x1 = Number.parseFloat(line.getAttribute("x1"));
      let y1 = Number.parseFloat(line.getAttribute("y1"));
      let x2 = Number.parseFloat(line.getAttribute("x2"));
      let y2 = Number.parseFloat(line.getAttribute("y2"));
      if (Number.isFinite(x1) && Number.isFinite(y1) && Number.isFinite(x2) && Number.isFinite(y2)) {
        // Swap coordinates when using the reverse edge so the packet travels
        // in the correct semantic direction (response flows go tool→agent etc.)
        if (reversed) { [x1, x2] = [x2, x1]; [y1, y2] = [y2, y1]; }
        return triggerPulseAnimation(x1, y1, x2, y2, durationMs, "pulse-packet");
      }
    }
    // Fallback: use computed node positions (always correct direction)
    return triggerFreePulse(from, to, durationMs, "pulse-packet");
}

/** Read a node's drawn position from SVG transform when currentPositions is stale */
function getDrawnNodePosition(nodeId) {
    const svg = document.getElementById("topology");
    if (!svg) return null;
    const g = svg.querySelector(`g[data-node-id="${nodeId}"]`);
    if (!g) return null;
    const m = g.getAttribute("transform")?.match(/translate\(([0-9.-]+)[,\s]+([0-9.-]+)\)/);
    return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]) } : null;
}

function triggerFreePulse(fromId, toId, durationMs, className = "pulse-packet") {
    const fromPos = (currentPositions?.get(fromId)) || getDrawnNodePosition(fromId);
    const toPos   = (currentPositions?.get(toId))   || getDrawnNodePosition(toId);
    if (!fromPos || !toPos) return Promise.resolve();
    return triggerPulseAnimation(fromPos.x, fromPos.y, toPos.x, toPos.y, durationMs, className);
}

function triggerPulseAnimation(x1, y1, x2, y2, durationMs, className) {
    return new Promise((resolve) => {
        const svg = document.getElementById("topology");
        if (!svg) {
            console.warn("triggerPulseAnimation: SVG not found");
            resolve();
            return;
        }
        
        // Debug: Log coordinates to see if they make sense
        if (x1 === undefined || y1 === undefined || x2 === undefined || y2 === undefined) {
            console.warn("triggerPulseAnimation: Invalid coordinates", { x1, y1, x2, y2 });
            resolve();
            return;
        }
        
        // Organic streak: ellipse oriented along travel direction via rotate="auto"
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
        circle.setAttribute("rx", "16");
        circle.setAttribute("ry", "4");
        circle.setAttribute("class", className);
        circle.setAttribute("cx", "0");
        circle.setAttribute("cy", "0");
        
        // animateMotion with rotate="auto" aligns the ellipse long-axis with trajectory
        const animate = document.createElementNS("http://www.w3.org/2000/svg", "animateMotion");
        animate.setAttribute("dur", `${durationMs}ms`);
        animate.setAttribute("repeatCount", "1");
        animate.setAttribute("rotate", "auto");
        animate.setAttribute("calcMode", "spline");
        animate.setAttribute("keyTimes", "0;1");
        animate.setAttribute("keySplines", "0.4 0 0.2 1");
        animate.setAttribute("path", `M ${x1} ${y1} L ${x2} ${y2}`);
        animate.setAttribute("fill", "freeze");
        
        // Event handlers
        let resolved = false;
        const cleanup = () => {
            if (!resolved) {
                resolved = true;
                resolve();
            }
            // Remove immediately after animation
            if (circle.parentNode) {
                circle.parentNode.removeChild(circle);
            }
        };
        
        animate.addEventListener("endEvent", cleanup);
        animate.addEventListener("end", cleanup);
        setTimeout(cleanup, durationMs + 100);
        
        circle.appendChild(animate);
        svg.appendChild(circle);
        
        // Start animation explicitly
        requestAnimationFrame(() => {
            try {
                animate.beginElement();
            } catch (e) {
                // Fallback if beginElement fails
                console.warn("Animation start failed", e);
            }
        });
    });
}


const metricsState = {
  lastValues: new Map(),
};

function animateMetric(element, type = "faint") {
  const cls = type === "bright" ? "highlight-bright" : "highlight-faint";
  element.classList.remove("highlight-faint", "highlight-bright");
  void element.offsetWidth; // Trigger reflow
  element.classList.add(cls);
}

function updateMetricUI(id, value, formatter, highlightType = "faint") {
  const element = document.getElementById(id);
  if (!element) return;
  
  const lastVal = metricsState.lastValues.get(id);
  const distinct = lastVal !== value;
  
  if (distinct || lastVal === undefined) {
    if (formatter) {
      element.textContent = formatter(value);
    } else {
      element.textContent = value;
    }
    metricsState.lastValues.set(id, value);
    
    // Only animate if it's not the very first load (optional, but looks better)
    if (lastVal !== undefined) {
      animateMetric(element.closest('.metric-card'), highlightType);
    }
  }
}

function updateStatusClass(element, value, thresholds) {
  element.classList.remove("status-ok", "status-warn", "status-error");
  if (value >= thresholds.ok) {
    element.classList.add("status-ok");
  } else if (value >= thresholds.warn) {
    element.classList.add("status-warn");
  } else {
    element.classList.add("status-error");
  }
}

function updateMetrics(events) {
  // Use timelineEvents for cumulative stats if available and larger than the window
  const sourceEvents = (timelineEvents.length > (events || []).length) ? timelineEvents : (events || []);
  
  if (!sourceEvents || sourceEvents.length === 0) {
    if (!promptExecutionActive || sessionStartTime === 0) {
      return;
    }
    const nowSec = Date.now() / 1000;
    const durationSec = Math.max(0, nowSec - sessionStartTime);
    const durationFormatted = durationSec < 60
      ? `${durationSec.toFixed(1)}s`
      : `${Math.floor(durationSec / 60)}m ${(durationSec % 60).toFixed(0)}s`;
    updateMetricUI("metric-duration", durationSec, () => durationFormatted, "faint");
    return;
  }

  // 1. Calculate Durations & Timestamps
  const sorted = [...sourceEvents].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  
  // Set session start time once
  if (sessionStartTime === 0 && sorted.length > 0) {
      sessionStartTime = sorted[0].timestamp || 0;
  }
  
  // Use global session start if available, else fallback to window start
  const start = sessionStartTime || sorted[0]?.timestamp || 0;
  
  // Calculate duration ONLY if prompt is actively executing
  // Otherwise keep last known value displayed
  let durationSec;
  if (promptExecutionActive) {
      // Use current time as end boundary for duration while executing
      const nowSec = Date.now() / 1000;
      const lastEventTime = sorted[sorted.length - 1]?.timestamp || 0;
      const end = Math.max(lastEventTime, nowSec);
      durationSec = Math.max(0, end - start);
  } else if (lastMetricsSnapshot && lastMetricsSnapshot.durationSec !== undefined) {
      // Keep last calculated duration
      durationSec = lastMetricsSnapshot.durationSec;
  } else {
      // Fallback: calculate from events only (no live clock)
      const lastEventTime = sorted[sorted.length - 1]?.timestamp || 0;
      durationSec = Math.max(0, lastEventTime - start);
  }
  
  const durationFormatted = (() => {
    if (durationSec < 60) return `${durationSec.toFixed(1)}s`;
    const m = Math.floor(durationSec / 60);
    const s = (durationSec % 60).toFixed(0);
    return `${m}m ${s}s`;
  })();

  updateMetricUI("metric-duration", durationSec, () => durationFormatted, "faint");

  // 2. Cost & Tokens
  let totalTokens = 0;
  const toolsUsed = new Set();
  let errorCount = 0;

  sourceEvents.forEach(e => {
    // Tokens
    const t = e.token_count || e.volume || 0;
    totalTokens += typeof t === 'number' ? t : 0;
    
    // Tools
    if (e.kind === "tool_call" || e.kind === "tool_result") {
      const tool = e.target || e.payload?.tool_name;
      if (tool) toolsUsed.add(tool);
    }
    
    // Errors
    if (e.kind === "error" || (e.payload && e.payload.status === "error")) {
      errorCount++;
    }
  });

  const cost = (totalTokens / 1000) * 0.002;
  
  updateMetricUI("metric-cost", cost, (v) => `$${v.toFixed(4)}`, "faint");
  updateMetricUI("metric-tokens", totalTokens, (v) => v.toLocaleString(), "faint");
  
  // 3. Activity & tool-server
  updateMetricUI("metric-tools", toolsUsed.size, null, "bright"); 
  
  // 4. Quality & Reliability
  const count = sourceEvents.length;

  // Event Rate
  const rate = durationSec > 0 ? (count / (durationSec / 60)) : 0;
  updateMetricUI("metric-events", rate, (v) => v.toFixed(0), "faint"); 

  // preventing division by zero
  const reliability = count > 0 ? ((count - errorCount) / count) * 100 : 100;
  
  updateMetricUI("metric-reliability", reliability, (v) => `${v.toFixed(1)}%`, "bright");
  const elRel = document.getElementById("metric-reliability");
  if (elRel) updateStatusClass(elRel, reliability, { ok: 98, warn: 90 });
  
  // Quality Score: Skip until we have real evaluation data
  // Leave the metric-quality element at "--" by default

  // Performance (Market Perf): Mock oscillating value
  // We can make it slightly deterministic based on event density (events per second approx)
  // Just to make it move
  const timeWindow = 5; // look at last 5 seconds?
  // Simple mock with some consistency
  const perf = 9400 + Math.floor((durationSec % 10) * 15) + (events.length % 5) * 10;
  updateMetricUI("metric-perf", perf, (v) => `${v} ms`, "faint");
}

function appendLog(event, isNew = true) {
  if (event.kind && event.kind.startsWith("object_")) {
    return;
  }
  if (event.kind === "ontology_alignment_skipped" || event.kind === "ontology_alignment") {
    return;
  }
  if (event.kind === "human_interaction" || event.kind === "agent_to_user") {
    return;
  }
  if (event.kind && (event.kind.startsWith("otlp_") || event.kind.startsWith("otel_"))) {
    return;
  }
  // *_start events are opening brackets only — no content yet; suppress entirely.
  // The matching *_end or *_response carries the actual result and belongs in the log.
  if (event.kind && event.kind.endsWith("_start")) {
    return;
  }
  if ((event.kind === "llm_call" || event.kind === "llm_response" || event.kind === "llm_call_end") && (!event.agent_id || event.agent_id === "system")) {
    return;
  }
  // Filter internal plumbing events that have no meaningful exchange target
  const _INTERNAL_KINDS = new Set(["processing_call_start", "processing_call_end", "routing_result", "execution_start", "execution_end"]);
  if (_INTERNAL_KINDS.has(event.kind)) {
    return;
  }
  const parts = buildLogParts(event);

  // Filter out system and audit messages completely from exchange log
  if (event.kind === "audit" || event.kind === "system" || parts.from === "system" || parts.to === "system") {
      return;
  }

  // Deduplication check
  
  // Check last 5 log items for duplicates (checking data attributes)
  const recentItems = Array.from(activityLog.children).slice(-5);
  for (const item of recentItems) {
      const from = item.getAttribute("data-from");
      const to = item.getAttribute("data-to");
      const detail = item.getAttribute("data-detail");
      
      // Check for exact duplicates in recent history
      if (detail === parts.detail && from === parts.from && to === parts.to) {
          return;
      }
  }

  // Remove pending indicator if it exists
  const pendingIndicator = activityLog.querySelector('.log-item--pending');
  if (pendingIndicator) {
    pendingIndicator.remove();
  }
  
  const item = document.createElement("div");
  // Store metadata for dedup
  item.setAttribute("data-from", parts.from);
  item.setAttribute("data-to", parts.to);
  item.setAttribute("data-detail", parts.detail);
  
  const itemClass = isNew ? `log-item log-item--new log-item--${parts.direction}` : `log-item log-item--${parts.direction}`;
  item.className = itemClass;

  const timestamp = document.createElement("span");
  timestamp.className = "log-timestamp";
  const eventTime = event.timestamp ? new Date(event.timestamp * 1000) : new Date();
  timestamp.setAttribute('data-timestamp', eventTime.getTime().toString());
  timestamp.textContent = formatTimestamp(eventTime);
  timestamp.title = eventTime.toLocaleString();
  
  // Debug: log llm_response events
  if (event.kind === 'llm_response') {
    console.log('[DEBUG] llm_response event logged:', event.agent_id, eventTime.toLocaleTimeString());
  }
  
  const line = document.createElement("div");
  line.className = "log-line log-line--collapsed";
  line.dataset.collapsed = "true";
  const from = document.createElement("span");
  const fromType = getEntityType(parts.from);
  from.className = `log-badge log-badge--${fromType}`;
  from.textContent = parts.from;
  const arrow = document.createElement("span");
  arrow.className = "log-arrow";
  if (parts.protocol && !["system", "input", "output"].includes(parts.protocol)) {
     // Always display as src --(proto)--> dest
     arrow.textContent = ` --(${parts.protocol})--> `;
     arrow.classList.add("log-arrow--protocol");
  } else {
     arrow.textContent = " → ";
  }
  const to = document.createElement("span");
  const toType = getEntityType(parts.to);
  to.className = `log-badge log-badge--${toType}`;
  to.textContent = parts.to;
  const detail = document.createElement("span");
  detail.className = "log-detail";
  detail.textContent = parts.detail || formatLogLine(event);
  
  const modalBtn = document.createElement("button");
  modalBtn.className = "log-modal-btn";
  modalBtn.innerHTML = "⋯";
  modalBtn.title = "View details";
  modalBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    openLogModal(event, parts);
  });
  
  line.appendChild(timestamp);
  line.appendChild(from);
  line.appendChild(arrow);
  line.appendChild(to);
  line.appendChild(detail);
  line.appendChild(modalBtn);
  
  line.addEventListener("click", () => {
    const collapsed = line.dataset.collapsed === "true";
    line.dataset.collapsed = collapsed ? "false" : "true";
    line.classList.toggle("log-line--collapsed", !collapsed);
  });
  
  item.appendChild(line);
  activityLog.appendChild(item);
  
  // Add pending indicator at the end
  addPendingIndicator();
  
  while (activityLog.children.length > 201) { // +1 for pending indicator
    if (activityLog.firstChild.className !== 'log-item--pending') {
      activityLog.removeChild(activityLog.firstChild);
    } else {
      activityLog.removeChild(activityLog.children[1]);
    }
  }
  if (autoScrollEnabled) {
    activityLog.scrollTop = activityLog.scrollHeight;
  }
  if (isNew) {
    setTimeout(() => item.classList.remove("log-item--new"), 1200);
  }
}

let useAbsoluteTimestamps = false;

function formatTimestamp(date) {
  if (useAbsoluteTimestamps) {
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  
  const now = new Date();
  const diff = now - date;
  
  if (diff < 10000) { // Less than 10 seconds
    return "just now";
  } else if (diff < 60000) { // Less than 1 minute
    const secs = Math.floor(diff / 1000);
    return `${secs}s ago`;
  } else if (diff < 3600000) { // Less than 1 hour
    const mins = Math.floor(diff / 60000);
    return `${mins}m ago`;
  } else if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
  } else {
    return date.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
}

function updateRelativeTimestamps() {
  if (useAbsoluteTimestamps) return;
  
  const timestamps = activityLog.querySelectorAll('.log-timestamp');
  timestamps.forEach(el => {
    const absTime = el.getAttribute('data-timestamp');
    if (absTime) {
      const date = new Date(parseInt(absTime));
      el.textContent = formatTimestamp(date);
    }
  });
}

// Update relative timestamps every 10 seconds
setInterval(updateRelativeTimestamps, 1000);

function addPendingIndicator() {
  const existing = activityLog.querySelector('.log-item--pending');
  if (existing) {
    return;
  }
  
  const pending = document.createElement("div");
  pending.className = "log-item log-item--pending";
  pending.innerHTML = `
    <div class="log-pending-dots">
      <span class="dot">•</span>
      <span class="dot">•</span>
      <span class="dot">•</span>
    </div>
  `;
  activityLog.appendChild(pending);
  
  if (autoScrollEnabled) {
    activityLog.scrollTop = activityLog.scrollHeight;
  }
}

function openLogModal(event, parts) {
  const modal = document.getElementById('logModal');
  if (!modal) {
    createLogModal();
    return openLogModal(event, parts);
  }
  
  const title = document.getElementById('logModalTitle');
  const content = document.getElementById('logModalContent');
  
  title.textContent = `${parts.from} → ${parts.to}`;
  
  const sources = Array.isArray(event.sources)
    ? event.sources
    : event.source
      ? [event.source]
      : [];

  const formatted = JSON.stringify(event, null, 2);
  content.innerHTML = `
    <div class="modal-section">
      <div class="modal-label">Event Type:</div>
      <div class="modal-value">${event.kind || 'unknown'}</div>
    </div>
    <div class="modal-section">
      <div class="modal-label">Timestamp:</div>
      <div class="modal-value">${event.timestamp ? new Date(event.timestamp * 1000).toLocaleString() : 'N/A'}</div>
    </div>
    ${sources.length ? `
    <div class="modal-section">
      <div class="modal-label">Sources:</div>
      <div class="modal-value">${sources.map(String).join(', ')}</div>
    </div>
    ` : ''}
    <div class="modal-section">
      <div class="modal-label">Detail:</div>
      <div class="modal-value">${parts.detail || 'N/A'}</div>
    </div>
    <div class="modal-section">
      <div class="modal-label">Raw Event:</div>
      <pre class="modal-json">${formatted}</pre>
    </div>
  `;
  
  modal.classList.add('is-visible');
}

function createLogModal() {
  const modal = document.createElement('div');
  modal.id = 'logModal';
  modal.className = 'modal';
  modal.innerHTML = `
    <div class="modal-backdrop"></div>
    <div class="modal-dialog">
      <div class="modal-header">
        <h3 id="logModalTitle" class="modal-title">Event Details</h3>
        <button class="modal-close" onclick="document.getElementById('logModal').classList.remove('is-visible')">×</button>
      </div>
      <div id="logModalContent" class="modal-body"></div>
    </div>
  `;
  document.body.appendChild(modal);
  
  modal.querySelector('.modal-backdrop').addEventListener('click', () => {
    modal.classList.remove('is-visible');
  });
}

function getCurrentAppId() {
  if (appSelect && appSelect.value) {
    return appSelect.value;
  }
  return currentDataset.dataset || "default";
}

function loadNodeLayout(appId) {
  const raw = localStorage.getItem(`mas.nodeLayout.${appId}`);
  if (!raw) {
    return {};
  }
  try {
    return JSON.parse(raw);
  } catch (error) {
    return {};
  }
}

function saveNodeLayout(appId, layout) {
  localStorage.setItem(`mas.nodeLayout.${appId}`, JSON.stringify(layout));
}

function getLayoutMode(appId) {
  if (!appId) {
    return "tiered";
  }
  return localStorage.getItem(`mas.layoutMode.${appId}`) || "tiered";
}

function setLayoutMode(appId, mode) {
  if (!appId) {
    return;
  }
  localStorage.setItem(`mas.layoutMode.${appId}`, mode);
}

function resetLayout(appId) {
  if (!appId) return;
  localStorage.removeItem(`mas.layoutMode.${appId}`);
  localStorage.removeItem(`mas.nodeLayout.${appId}`);
  if (layoutSelect) {
    layoutSelect.value = "tiered";
  }
  drawTopology(cachedTopology);
}

function hydrateLayoutSelect(appId) {
  if (!layoutSelect) return;
  layoutSelect.innerHTML = "";
  LAYOUT_OPTIONS.forEach((optionDef) => {
    const option = document.createElement("option");
    option.value = optionDef.id;
    option.textContent = optionDef.label;
    layoutSelect.appendChild(option);
  });
  layoutSelect.value = getLayoutMode(appId);
}

function clientToSvg(clientX, clientY) {
  const point = svg.createSVGPoint();
  point.x = clientX;
  point.y = clientY;
  const matrix = svg.getScreenCTM();
  if (!matrix) {
    return { x: clientX, y: clientY };
  }
  const transformed = point.matrixTransform(matrix.inverse());
  return { x: transformed.x, y: transformed.y };
}

function initViewBox() {
  if (!svg) return;
  const vb = svg.viewBox.baseVal;
  if (!vb || !vb.width || !vb.height) {
    svg.setAttribute("viewBox", "0 0 1600 900");
  }
  const resolved = svg.viewBox.baseVal;
  viewBoxState = {
    x: resolved.x,
    y: resolved.y,
    width: resolved.width,
    height: resolved.height,
  };
  defaultViewBox = { ...viewBoxState };
}

function setViewBox(next) {
  viewBoxState = next;
  svg.setAttribute("viewBox", `${next.x} ${next.y} ${next.width} ${next.height}`);
}

function resetViewBox() {
  if (!defaultViewBox) {
    initViewBox();
  }
  if (defaultViewBox) {
    setViewBox({ ...defaultViewBox });
  }
}

function isNodeTarget(target) {
  let el = target;
  while (el && el !== svg) {
    if (el.dataset && el.dataset.nodeId) {
      return true;
    }
    el = el.parentNode;
  }
  return false;
}

function startPan(event) {
  if (event.button !== 0) return;
  if (!viewBoxState || isNodeTarget(event.target)) return;
  panState = {
    startX: event.clientX,
    startY: event.clientY,
    viewBox: { ...viewBoxState },
  };
  svg.classList.add("is-panning");
  svg.setPointerCapture(event.pointerId);
}

function handlePanMove(event) {
  if (!panState || !viewBoxState) return;
  const scaleX = viewBoxState.width / Math.max(svg.clientWidth, 1);
  const scaleY = viewBoxState.height / Math.max(svg.clientHeight, 1);
  const dx = (event.clientX - panState.startX) * scaleX;
  const dy = (event.clientY - panState.startY) * scaleY;
  setViewBox({
    x: panState.viewBox.x - dx,
    y: panState.viewBox.y - dy,
    width: panState.viewBox.width,
    height: panState.viewBox.height,
  });
}

function endPan(event) {
  if (!panState) return;
  svg.classList.remove("is-panning");
  svg.releasePointerCapture(event.pointerId);
  panState = null;
}

function handleZoom(event) {
  if (!viewBoxState) return;
  event.preventDefault();
  const zoomFactor = Math.exp(-event.deltaY * 0.0015);
  const pointer = clientToSvg(event.clientX, event.clientY);
  const minWidth = (defaultViewBox?.width || viewBoxState.width) * 0.35;
  const maxWidth = (defaultViewBox?.width || viewBoxState.width) * 4;
  const nextWidth = Math.min(maxWidth, Math.max(minWidth, viewBoxState.width / zoomFactor));
  const nextHeight = (viewBoxState.height / viewBoxState.width) * nextWidth;
  const mx = (pointer.x - viewBoxState.x) / viewBoxState.width;
  const my = (pointer.y - viewBoxState.y) / viewBoxState.height;
  setViewBox({
    x: pointer.x - mx * nextWidth,
    y: pointer.y - my * nextHeight,
    width: nextWidth,
    height: nextHeight,
  });
}

function startDrag(event, nodeId) {
  if (event.button !== 0) {
    return;
  }
  event.stopPropagation();
  event.preventDefault();
  const appId = getCurrentAppId();
  if (getLayoutMode(appId) !== "custom") {
    // Snapshot current valid positions before switching to custom mode
    // This prevents the layout from resetting to a random state (fallback force layout)
    // because "custom" mode initially has no saved positions in localStorage.
    if (cachedTopology && cachedTopology.nodes) {
       const snapshot = {};
       cachedTopology.nodes.forEach(n => {
           const pos = currentPositions.get(n.id);
           if (pos) {
               snapshot[n.id] = { x: pos.x, y: pos.y };
           }
       });
       // Only save if we have positions to save
       if (Object.keys(snapshot).length > 0) {
           saveNodeLayout(appId, snapshot);
       }
    }

    setLayoutMode(appId, "custom");
    if (layoutSelect) {
      layoutSelect.value = "custom";
    }
  }
  const pos = currentPositions.get(nodeId);
  if (!pos) {
    return;
  }
  const pointer = clientToSvg(event.clientX, event.clientY);
  dragState = {
    nodeId,
    offsetX: pointer.x - pos.x,
    offsetY: pointer.y - pos.y,
  };
  svg.setPointerCapture(event.pointerId);
  svg.addEventListener("pointermove", handleDragMove);
  svg.addEventListener("pointerup", handleDragEnd);
  svg.addEventListener("pointercancel", handleDragEnd);
}

function handleDragMove(event) {
  if (!dragState) return;
  const pointer = clientToSvg(event.clientX, event.clientY);
  const nextX = pointer.x - dragState.offsetX;
  const nextY = pointer.y - dragState.offsetY;
  currentPositions.set(dragState.nodeId, { x: nextX, y: nextY });
  if (!dragFrame) {
    dragFrame = requestAnimationFrame(() => {
      dragFrame = null;
      applyNodePosition(dragState.nodeId);
      updateConnectedEdges(dragState.nodeId);
    });
  }
}

function handleDragEnd(event) {
  if (!dragState) return;
  const appId = getCurrentAppId();
  const layout = loadNodeLayout(appId);
  const pos = currentPositions.get(dragState.nodeId);
  if (pos) {
    layout[dragState.nodeId] = { x: pos.x, y: pos.y };
    saveNodeLayout(appId, layout);
  }
  svg.releasePointerCapture(event.pointerId);
  svg.removeEventListener("pointermove", handleDragMove);
  svg.removeEventListener("pointerup", handleDragEnd);
  svg.removeEventListener("pointercancel", handleDragEnd);
  dragState = null;
}

function applyNodePosition(nodeId) {
  const group = nodeGroups.get(nodeId);
  const pos = currentPositions.get(nodeId);
  if (!group || !pos) return;
  group.setAttribute("transform", `translate(${pos.x} ${pos.y})`);
}

function updateConnectedEdges(nodeId) {
  if (!cachedTopology) return;
  cachedTopology.edges.forEach((edge) => {
    if (edge.from !== nodeId && edge.to !== nodeId) return;
    const line = edgeState.get(`${edge.from}->${edge.to}`);
    if (!line) return;
    const from = currentPositions.get(edge.from);
    const to = currentPositions.get(edge.to);
    if (!from || !to) return;
    line.setAttribute("x1", from.x);
    line.setAttribute("y1", from.y);
    line.setAttribute("x2", to.x);
    line.setAttribute("y2", to.y);
  });
}

function stripVolatile(value) {
  if (Array.isArray(value)) {
    return value.map(stripVolatile);
  }
  if (value && typeof value === "object") {
    const cleaned = {};
    Object.entries(value).forEach(([key, entry]) => {
      if (["timestamp", "run_id", "trace_id", "span_id"].includes(key)) {
        return;
      }
      cleaned[key] = stripVolatile(entry);
    });
    return cleaned;
  }
  return value;
}

function eventSignature(event) {
  return JSON.stringify({
    kind: event.kind,
    agent_id: event.agent_id,
    target: event.target,
    payload: stripVolatile(event.payload),
  });
}

function shouldProcessEvent(event, now) {
  const key = eventSignature(event);
  const lastSeen = recentEventKeys.get(key);
  if (lastSeen && now - lastSeen < DEDUPE_WINDOW_MS) {
    return false;
  }
  recentEventKeys.set(key, now);
  return true;
}

function pruneEventKeys(now) {
  for (const [key, lastSeen] of recentEventKeys.entries()) {
    if (now - lastSeen > DEDUPE_WINDOW_MS * 2) {
      recentEventKeys.delete(key);
    }
  }
}

function resolveEntryAgentId() {
  if (!cachedTopology) return null;
  const entryEdge = cachedTopology.edges.find((edge) => edge.from === "user");
  if (entryEdge?.to) return entryEdge.to;
  if (sessionEntryAgent) return sessionEntryAgent;
  const fallbackAgent = cachedTopology.nodes.find((node) => node.type === "agent");
  return fallbackAgent?.id || null;
}

function seedUserInput(promptText) {
  const agentId = resolveEntryAgentId();
  const event = {
    kind: "user_input",
    agent_id: agentId,
    content: promptText,
    timestamp: Date.now() / 1000,
  };
  timelineEvents.push(event);
  appendLog(event, true);
}

let controllerOk = true;
let serviceOverall = "ok";

function setStatusDot(status) {
  if (!controllerStatus) return;
  controllerStatus.classList.remove("status-dot--ok", "status-dot--warn", "status-dot--error");
  if (status === "warn") {
    controllerStatus.classList.add("status-dot--warn");
  } else if (status === "error") {
    controllerStatus.classList.add("status-dot--error");
  } else {
    controllerStatus.classList.add("status-dot--ok");
  }
}

function setStatusWorking(isWorking) {
  if (!controllerStatus) return;
  controllerStatus.classList.toggle("status-dot--working", !!isWorking);
}

function applyOverallStatus() {
  if (!controllerOk) {
    setStatusDot("error");
    return;
  }
  setStatusDot(serviceOverall === "warn" ? "warn" : "ok");
}

function setControllerStatus(ok, message) {
  if (!controllerStatus || !controllerWarn) return;
  controllerOk = ok;
  controllerWarn.classList.toggle("is-visible", !ok);
  controllerWarn.title = message;
  applyOverallStatus();
}

async function fetchJson(path) {
  try {
    const response = await fetch(path);
    if (!response.ok) {
      // Don't mark controller offline for 404s - endpoints may not exist yet
      // Let fetchHealth() handle controller status
      return null;
    }
    // Restore controller status if it was offline (self-healing)
    if (!controllerOk) {
      setControllerStatus(true, "Controller online");
    }
    return await response.json();
  } catch (error) {
    // Network errors should still mark controller offline
    setControllerStatus(false, "Controller offline");
    return null;
  }
}

async function fetchEvents() {
  const url = currentRunId ? `/api/events?run_id=${encodeURIComponent(currentRunId)}` : "/api/events";
  const events = (await fetchJson(url)) || [];
  return events.map((event) => {
    if (!event.kind && event.type) {
      return { ...event, kind: event.type };
    }
    return event;
  });
}

async function fetchMetrics() {
  return (await fetchJson("/api/metrics")) || {};
}

async function fetchHealth() {
  const response = await fetch("/api/health");
  return response.ok;
}

async function fetchInfrastructureServices() {
  const payload = await fetchJson("/api/infrastructure");
  return payload?.services || [];
}

async function fetchServiceStatus() {
  return (await fetchJson("/api/service-status")) || null;
}

async function fetchStartupStatus() {
  const payload = await fetchJson("/api/startup-status");
  return payload || { phase: "unknown", progress: 0, message: "Checking..." };
}

async function recoverState() {
  setResyncing(true);
  try {
    const status = await fetchJson("/api/status");
    console.log("[State Recovery] Controller status:", status);
    if (status?.run_id) {
      currentRunId = status.run_id;
    }
    if (status?.scenario && scenarioSelect) {
      scenarioSelect.value = status.scenario;
    }

    // Keep scenario ready after refresh if it was initialized, even if not actively running.
    isScenarioRunning = Boolean(status?.running || status?.scenario);
    // isWaitingForResponse tracks an in-flight sendPrompt HTTP call — never
    // true on page load (there is no request in flight after a refresh).
    isWaitingForResponse = false;
    // Restore execution state from server: if a run is still in progress on
    // the server side (e.g. after a page refresh), keep Interrupt visible.
    isExecutionActive = Boolean(status?.running);

    if (isScenarioRunning) {
      console.log("[State Recovery] Scenario ready");
    } else {
      console.log("[State Recovery] No active scenario - showing STOPPED");
    }

    // Update UI to reflect recovered state
    updateControlsState();
  } catch (e) {
    console.error("[State Recovery] Error:", e);
    isScenarioRunning = false;
    isWaitingForResponse = false;
    updateControlsState();
  } finally {
    setResyncing(false);
  }
}

async function sendPrompt(prompt, scenario, signal) {
  try {
    // Check if we are already running in autonomous mode (persistent session)
    if (currentRunId && isScenarioRunning) {
        // We might be just queueing a message
    }
    
    const userModel = userModelSelect ? userModelSelect.value : "manual";
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, scenario, user_model: userModel }),
      signal
    });
    
    if (!response.ok) {
      if (response.status === 409) {
          // If busy, we should not have been able to click send.
          // But if we did, maybe we want to alert user.
          throw new Error("System is busy processing another prompt.");
      }
      let detail = response.statusText;
      try {
          const err = await response.json();
          if (err.detail) detail = err.detail;
      } catch (e) {}
      throw new Error(`Request failed (${response.status}): ${detail}`);
    }
    return await response.json();
  } catch (err) {
    if (err.name !== 'AbortError') {
        const msg = err.message || "Controller offline";
        setControllerStatus(false, msg);
    }
    throw err;
  }
}

/**
 * Wipe all animation state from nodes and edges without touching the SVG layout.
 * Call this instead of drawTopology() when the topology structure hasn't changed.
 */
function clearAnimationState() {
  // Clear all time-bounded tracking
  nodeActiveUntil.clear();
  edgeActiveUntil.clear();
  nodeWorkingUntil.clear();
  nodeWaitingUntil.clear();
  nodeUnavailable.clear();
  persistentNodeClasses.clear();
  // Strip animation classes from every drawn node circle
  for (const circle of nodeState.values()) {
    circle.classList.remove('node-active', 'node-waiting', 'node-working', 'node-unavailable');
  }
  // Strip highlight classes from every drawn edge
  for (const line of edgeState.values()) {
    line.classList.remove('edge-active', 'edge-flow--request', 'edge-flow--response', 'edge-volume-pulse');
    clearEdgePulse(line);
  }
}

function resetUiState({ clearTopology }) {
  // Reset cursors unconditionally: the new run writes a fresh feed file that
  // starts at _index=0.  Keeping the old run's lastSeenIndex (e.g. 42) would
  // cause the filter `event._index > lastSeenIndex` to reject all new events.
  lastSeenIndex = -1;
  lastSeenTimestamp = 0;
  
  sessionStartTime = 0; // Reset session timer
  activityLog.innerHTML = "";
  timelineEvents = [];
  expandedGroups.clear();
  isInitialLoad = false; // From this point, all events are new
  renderTimeline();
  updateMetrics([]);
  
  if (clearTopology) {
    // Preserve the current layout: only reset visual animation state.
    // Nodes discovered in the previous run will remain visible and correctly
    // positioned — only their active/waiting highlights are cleared.
    clearAnimationState();

    // Re-fetch the static topology silently. If the node set is different
    // (e.g. user switched to a different scenario type) we redraw; otherwise
    // the existing SVG — including custom positions and lanes — is preserved.
    fetchJson("/api/topology").then((staticTopology) => {
      if (!staticTopology || !staticTopology.nodes?.length) return;
      const freshTopo = augmentTopologyWithSyntheticNodes({ nodes: staticTopology.nodes, edges: staticTopology.edges || [] });
      // Compare node sets — only redraw if structure differs
      const currentIds = new Set((cachedTopology?.nodes || []).map(n => n.id));
      const freshIds   = new Set(freshTopo.nodes.map(n => n.id));
      const same = freshIds.size === currentIds.size && [...freshIds].every(id => currentIds.has(id));
      if (!same || !cachedTopology) {
        cachedTopology = freshTopo;
        nodeState.clear();
        edgeState.clear();
        drawTopology(cachedTopology);
      } else {
        // Structure unchanged — just update the cached reference with fresh metadata
        // (e.g. updated labels/patterns) without touching the SVG.
        cachedTopology = freshTopo;
      }
    }).catch(() => {});
  }
}

async function refresh(topology) {
  if (isRefreshing) return;
  isRefreshing = true;
  try {
    const ok = await fetchHealth().catch(() => false);
    document.documentElement.classList.toggle("is-offline", !ok);
    // Always set controller status based on health check
    if (ok) {
      setControllerStatus(true, "Controller online");
    } else {
      setControllerStatus(false, "Controller offline");
    }

    // Freeze updates while waiting for run initialization
    if (isWaitingForResponse && !currentRunId) {
      return;
    }

    const now = Date.now();
    let events = await fetchEvents();
    if (currentRunId) {
      events = events.filter((event) => !event.run_id || event.run_id === currentRunId);
    }
    // Render counters from the most recent metrics snapshot.
    if (eventCount) {
      eventCount.textContent = events.length;
    }

    // Notify scrubber controller about the latest event list (updates timeline range).
    if (window.scrubberController) scrubberController.onEventsUpdated(events);

    // Update metrics panel with current data (or cumulative timeline)
    updateMetrics(events);

    if (cachedTopology && discoverFromEvents(cachedTopology, events)) {
      drawTopology(cachedTopology);
    }
    const active = new Set();

        const freshEvents = events
        .filter((event) => {
            if (event._index !== undefined) {
            return event._index > lastSeenIndex;
            }
            return (event.timestamp || 0) > lastSeenTimestamp;
        })
        .sort((a, b) => {
            if (a._index !== undefined && b._index !== undefined) {
            return a._index - b._index;
            }
            return (a.timestamp || 0) - (b.timestamp || 0);
        });


        // On initial load: advance the cursor to the current tail so that the
        // NEXT refresh cycle only delivers truly new events (no double-animation).
        // We still let freshEvents flow through the plugin loop below — with
        // isInitialLoad=true the loop skips all travel animations and gaps, but
        // it does populate the activity log and rebuild node waiting/active states.
        if (isInitialLoad) {
            if (events.length > 0) {
                const maxTs = Math.max(...events.map(e => e.timestamp || 0));
                const maxIdx = Math.max(...events.map(e => e._index || -1));
                lastSeenTimestamp = Math.max(lastSeenTimestamp, maxTs);
                lastSeenIndex = Math.max(lastSeenIndex, maxIdx);
            }
            // isInitialLoad stays true until the end of this function so the loop
            // runs without animation gaps and triggerFlowPulse is a no-op.
        }

    // Handle Loading State Transition
    if (isWaitingForResponse && freshEvents.length > 0) {
      setLoadingState(false);
      updateControlsState();
      
      // Clear user waiting state immediately when response starts arriving
      if (nodeWaitingUntil.has("user")) {
        nodeWaitingUntil.set("user", 0);
      }
    }

    // Sync isExecutionActive with the server every refresh cycle when a run
    // is believed to be active but no HTTP call is in flight.  This catches
    // cases where we missed the terminal event (e.g. network hiccup) and
    // ensures the Interrupt button clears in at most ~2 s after completion.
    if (isExecutionActive && !isWaitingForResponse) {
      const serverRunStatus = await fetchJson("/api/status");
      if (serverRunStatus !== null && !serverRunStatus.running) {
        isExecutionActive = false;
        updateControlsState();
      }
    }

    const hasUserResponse = freshEvents.some((event) => (
      event.kind === "user_response" || event.kind === "human_interaction" || event.kind === "agent_to_user"
    ));
    // Terminal event received: execution is complete on the server side.
    if (hasUserResponse && isExecutionActive) {
      isExecutionActive = false;
      updateControlsState();
    }
    if (promptExecutionActive && hasUserResponse) {
      const lastEventTime = events[events.length - 1]?.timestamp || (Date.now() / 1000);
      const durationSec = Math.max(0, lastEventTime - sessionStartTime);
      lastMetricsSnapshot = { durationSec };
      promptExecutionActive = false;
    }
    
    // Stop metrics tracking when execution completes
    if (promptExecutionActive && !isWaitingForResponse && events.length > 0) {
      // Snapshot final metrics for display
      const sorted = [...events].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
      const start = sessionStartTime || sorted[0]?.timestamp || 0;
      const lastEventTime = sorted[sorted.length - 1]?.timestamp || 0;
      const durationSec = Math.max(0, lastEventTime - start);
      lastMetricsSnapshot = { durationSec };
      promptExecutionActive = false;
      console.log("[Metrics] Execution completed, final duration:", durationSec, "s");
    }


    for (let i = 0; i < freshEvents.length; i++) {
        const event = freshEvents[i];
        
        // Didactic Flow: Visible pulse animation
        // 650ms lets the streak visually traverse long edges clearly
        const pulseDuration = 650;
        if (!isInitialLoad) {
            // Gap between events for sequential visualization - increased gap
               await new Promise((r) => setTimeout(r, i === 0 ? 0 : 50));
        }
        const visualNow = Date.now();

        if (!shouldProcessEvent(event, visualNow)) {
          continue;
        }
        
        const agentId = resolveAgentId(event);
        if (agentId) active.add(agentId);
        const resolvedTarget = resolveTarget(event);
        
        if (resolvedTarget && !isInitialLoad) {
            // Bump propagation only for live events
            // triggerFlowPulse logic is usually inside pluginRegistry.processEvent
            // but we can ensure the pluginContext has isInitialLoad flag
        }

        // ============================================================
        // Process event through plugin system
        // ============================================================
        const pluginContext = {
          ...sharedContext,
          visualNow,
          agentId,
          target: resolvedTarget,
          isInitialLoad,
          pulseDuration,
        };
        
        // Process event through plugin system (handles all state, flow, log, timeline)
        await pluginRegistry.processEvent(event, pluginContext);
    }

    if (freshEvents.length) {
      lastSeenTimestamp = Math.max(...freshEvents.map((event) => event.timestamp || lastSeenTimestamp));
      const indexValues = freshEvents
        .map((event) => event._index)
        .filter((value) => value !== undefined);
      if (indexValues.length) {
        lastSeenIndex = Math.max(...indexValues, lastSeenIndex);
      }
    }
    pruneActive(now);
    pruneEventKeys(now);
    if (activeAgents) {
      activeAgents.textContent = active.size;
    }
    if (lastEvent && events.length > 0) {
      // Find meaningful content to display in info window
      const meaningful = events.slice().reverse().find(e => 
           e.kind === "agent_to_user" || e.kind === "human_interaction" || (e.kind === "llm_response" && e.payload?.content)
      );
      if (meaningful) {
          let text = meaningful.kind;
          if (meaningful.kind === "agent_to_user" || meaningful.kind === "human_interaction") {
               text = meaningful.payload?.content || meaningful.content || text;
          } else if (meaningful.kind === "llm_response") {
               text = meaningful.payload?.content || "Thinking...";
          }
          if (typeof text === 'string') {
              lastEvent.textContent = text.length > 80 ? text.substring(0, 80) + "..." : text;
              lastEvent.title = text;
          }
      }
    }
    const hasNewEvents = freshEvents.length > 0;
    renderTimeline(hasNewEvents && !isInitialLoad);
    
    // After first refresh, mark as non-initial regardless of content
    if (isInitialLoad) {
      isInitialLoad = false;
    }
  } finally {
    isRefreshing = false;
  }
}

async function init() {
  // Disable controls during startup
  if (btnInitScenario) btnInitScenario.disabled = true;
  if (btnSendPrompt) btnSendPrompt.disabled = true;

  // Show startup progress
  const statusDiv = document.createElement("div");
  statusDiv.id = "startup-overlay";
  statusDiv.style.cssText = "position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(0,0,0,0.9); padding: 2rem; border-radius: 8px; z-index: 10000; color: white; text-align: center;";
  statusDiv.innerHTML = '<div id="startup-message">Initializing...</div><div style="margin-top: 1rem; width: 200px; height: 4px; background: rgba(255,255,255,0.2); border-radius: 2px; overflow: hidden;"><div id="startup-progress" style="width: 0%; height: 100%; background: #38bdf8; transition: width 0.3s;"></div></div>';
  document.body.appendChild(statusDiv);
  
  // Poll startup status
  let attempts = 0;
  while (attempts < 20) {
    try {
        const status = await fetchStartupStatus();
        const messageEl = document.getElementById("startup-message");
        const progressEl = document.getElementById("startup-progress");
        if (messageEl) messageEl.textContent = status.message || "Starting...";
        if (progressEl) progressEl.style.width = `${status.progress}%`;
        
        if (status.phase === "complete" || status.phase === "ready") break;
    } catch (e) {
        console.warn("Startup polling failed", e);
    }
    await new Promise(resolve => setTimeout(resolve, 500));
    attempts++;
  }
  
  // Remove startup overlay
  statusDiv.remove();
  
  // Recover state from controller (if reloading page)
  await recoverState();

  // Load infrastructure services dynamically
  const infraServices = await fetchInfrastructureServices();
  // Store for status dropdown
  window.availableInfraServices = infraServices;

  // Fast restore from localStorage: render topology before network fetch for instant display
  const _lsTopoKey = `mas.topology.${getCurrentAppId() || 'default'}`;
  try {
    const _lsStored = localStorage.getItem(_lsTopoKey);
    if (_lsStored) {
      const _lsTopo = JSON.parse(_lsStored);
      if (_lsTopo?.nodes?.length) {
        cachedTopology = _lsTopo;
        drawTopology(cachedTopology);
      }
    }
  } catch(e) {}

  // Pre-populate topology from config so agents and connections are visible
  // immediately on page load — no run required.
  const staticTopology = await fetchJson("/api/topology").catch(() => null);
  if (staticTopology && staticTopology.nodes && staticTopology.nodes.length > 0) {
    cachedTopology = augmentTopologyWithSyntheticNodes({
      nodes: staticTopology.nodes,
      edges: staticTopology.edges || [],
    });
  } else {
    // Fallback: start with infra nodes only (will be enriched by events)
    const infraNodes = infraServices.map((s) => ({
      id: s.id,
      type: s.type || "infra",
      label: s.label || s.id,
    }));
    cachedTopology = { nodes: infraNodes, edges: [] };
  }
  drawTopology(cachedTopology);

  // Enable initial controls after status checks and topology init
  updateControlsState();

  // Setup status menu toggle on LED
  if (controllerStatus && serviceStatusMenu) {
    controllerStatus.style.cursor = "pointer";
    controllerStatus.addEventListener("click", (e) => {
      e.stopPropagation();
      serviceStatusMenu.classList.toggle("is-open");
      serviceStatusMenu.setAttribute("aria-hidden", serviceStatusMenu.classList.contains("is-open") ? "false" : "true");
    });
    window.addEventListener("click", (event) => {
      if (!serviceStatusMenu.contains(event.target) && event.target !== controllerStatus) {
        serviceStatusMenu.classList.remove("is-open");
        serviceStatusMenu.setAttribute("aria-hidden", "true");
      }
    });
  }

  const tabTree = document.getElementById("timeline-tab-tree");
  const tabGraph = document.getElementById("timeline-tab-graph");
  if (tabTree) {
    tabTree.addEventListener("click", () => setTimelineViewMode("tree"));
  }
  if (tabGraph) {
    tabGraph.addEventListener("click", () => setTimelineViewMode("graph"));
  }
  setTimelineViewMode(timelineViewMode);

  renderTimeline();
  await refresh(cachedTopology);
  await refreshServiceStatus(true);
  setInterval(() => refresh(cachedTopology), 2000);
  // Keep the LED dropdown status fresh without spamming the controller.
  setInterval(() => refreshServiceStatus(false), 4000);
}

if (btnInitScenario) {
  btnInitScenario.addEventListener("click", async () => {
    // RUNAPP Logic
    const scenario = scenarioSelect.value;
    if (!scenario) return;

    resetUiState({ clearTopology: true });

    setLoadingState(true, "Initializing Scenario...");
    updateControlsState(); // Trigger disabled state while loading
    
    // Call /api/scenario to initialize and emit topology
    try {
        const response = await fetch("/api/scenario", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scenario, user_model: userModelSelect ? userModelSelect.value : "manual" }) // Ensure user model is passed
        });
        
        if (!response.ok) {
            throw new Error(`Failed to start scenario: ${response.status}`);
        }
        
        const result = await response.json();
        if (result?.run_id) {
          currentRunId = result.run_id;
          await refresh(cachedTopology);
        }
        console.log("Scenario initialized:", result);
    } catch (error) {
        console.error("Error starting scenario:", error);
        setLoadingState(false);
        alert(`Failed to start scenario: ${error.message}`);
        return;
    }
    
    isScenarioRunning = true;
    setLoadingState(false);
    updateControlsState();
    
    // Auto-select valid prompt if available
    const promptData = promptSelect.value ? JSON.parse(promptSelect.value) : null;
    if (!promptData && promptSelect.options.length > 0 && promptSelect.options[0].value) {
       promptSelect.selectedIndex = 0;
    }
  });
}

if (btnStopScenario) {
  btnStopScenario.addEventListener("click", async () => {
        // Hard stop logic
        await fetch("/api/stop", { method: "POST" }).catch(console.error);
        
        isScenarioRunning = false;
        sessionEntryAgent = "";
        sessionStartTime = 0;
        // Optionally abort any pending prompt
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        isWaitingForResponse = false; // Force stop waiting
        isExecutionActive = false;    // Server run has been stopped
        setLoadingState(false);
        updateControlsState();
  });
}

if (btnStopUser) {
  btnStopUser.addEventListener("click", async () => {
      // Stop Autonomous User (Interrupt)
      if (abortController) {
          abortController.abort();
          abortController = null;
      }
      isExecutionActive = false;
      console.log("Interrupting user...");
      await fetch("/api/interrupt", { method: "POST" }).catch(console.error);

      // We do not stop the scenario, just the user action
      setLoadingState(false, "User Interrupted");
      // updateControlsState will keep stop user button enabled if we consider it "resumable" or disable it?
      // Since "Autonomous User" implies a loop, we might need to handle state to disable this button.
      // But for now, just sending interrupt is what's requested.
  });
}

if (promptInput && promptClear) {
    const handleInput = () => {
        const hasText = promptInput.value.length > 0;
        if (hasText) {
            promptClear.classList.remove("is-hidden");
        } else {
            promptClear.classList.add("is-hidden");
        }
        updateControlsState(); // Re-validate Send button
    };
    promptInput.addEventListener("input", handleInput);
    promptClear.addEventListener("click", () => {
        promptInput.value = "";
        promptInput.focus();
        handleInput();
    });
}

if (btnSendPrompt) {
  btnSendPrompt.addEventListener("click", () => {
     // Trigger form submit logic
     promptForm.dispatchEvent(new Event('submit', { cancelable: true }));
  });
}

promptForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  
  // Handle Interrupt Logic
  // Any click on the button (which is "Interrupt" when waiting) calls /api/interrupt (Soft Stop)
  // This does NOT kill the process, just clears queues and signals abort.
  if (isWaitingForResponse || isExecutionActive) {
      if (abortController) {
          abortController.abort();
          abortController = null;
      }
      isExecutionActive = false;
      
      // Send signal to backend to interrupt current task
      console.log("Interrupting task...");
      await fetch("/api/interrupt", { method: "POST" }).catch(console.error);

      setLoadingState(false, "Interrupted");
      updateControlsState();
      // Important: Do not set isScenarioRunning = false, as we keep the session alive!
      return;
  }
  
  const promptText = promptInput ? promptInput.value.trim() : "";
  if (!promptText) {
    alert("Please enter a prompt");
    return;
  }
  
  // Try to find scenario from datalist match if possible, else default
  let scenario = scenarioSelect.value;
  // If no scenario selected, check if prompt matches a known one
  if (!scenario) {
      const dataList = document.getElementById("prompt-options");
      if (dataList) {
          const match = Array.from(dataList.options).find(opt => opt.value === promptText);
          if (match && match.dataset.scenario) {
              scenario = match.dataset.scenario;
          }
      }
      if (!scenario) scenario = "baseline"; // Fallback
  }

  const payload = { prompt: promptText, scenario };

  seedUserInput(payload.prompt);
  
  // Visual feedback: ensure user node exists and animate it
  if (cachedTopology) {
    const userNodeAdded = ensureNode(cachedTopology, "user", "user");
    // If we added a node, we MUST redraw to create the SVG elements
    if (userNodeAdded) {
      drawTopology(cachedTopology);
    }
    const now = Date.now();
    setNodeActive("user", now);
    // Animate user→agent packet so the bump is visibly seen leaving the user node
    const entryAgentId = resolveEntryAgentId();
    if (entryAgentId) {
      enqueueFlowPulse(async () => {
        const t = Date.now();
        // User node: brief timer-based flash (user doesn't "hold" the token)
        setNodeActive("user", t);
        setEdgeActive("user", entryAgentId, t, "request", 1, 750);
        await triggerFlowPulse("user", entryAgentId, 650);
        // Entry agent RECEIVES — goes persistently red (stays until it dispatches)
        setNodePersistentClass(entryAgentId, 'node-active');
        clearNodePersistentClass(entryAgentId, 'node-waiting');
        clearEdgeActive("user", entryAgentId);
        await new Promise(r => setTimeout(r, 380));
      });
    }
    setNodeStatus("user", "waiting", now, 60000);
  }
  
  // Reset metrics for new prompt execution
  metricsState.lastValues.clear();
  lastMetricsSnapshot = null;
  promptExecutionActive = true; // Start tracking execution time
  sessionStartTime = Date.now() / 1000;
  sessionEntryAgent = "";
  ["metric-cost", "metric-tokens", "metric-events", "metric-duration", 
   "metric-tool-server", "metric-reliability", "metric-quality", "metric-perf"].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        if (id === "metric-cost") el.textContent = "$0.0000";
        else if (id === "metric-duration" || id === "metric-perf") el.textContent = "0s";
        else if (id === "metric-reliability") el.textContent = "100%";
        else if (id === "metric-quality") el.textContent = "--";
        else el.textContent = "0";
        el.classList.remove("status-ok", "status-warn", "status-error");
      }
  });
  
  // Reset event cursors so new run's events (_index starting at 0) pass
  // the freshness filter.  isInitialLoad must stay FALSE so events are
  // animated (setting it true silently consumes the first batch).
  lastSeenIndex = -1;
  lastSeenTimestamp = 0;
  isInitialLoad = false;
  activityLog.innerHTML = "";
  timelineEvents = [];
  expandedGroups.clear();
  renderTimeline();
  // Clear stale topology highlights from the previous run.
  nodeState.clear();
  edgeState.clear();
  if (cachedTopology) drawTopology(cachedTopology);
  // Mark scenario as running immediately so Run/Stop button and
  // status pill are correct before the first event arrives.
  isScenarioRunning = true;
  updateControlsState();
  setLoadingState(true, "Processing request...");
  currentRunId = null;
  
  abortController = new AbortController();

  const token = ++runToken;
  try {
      const result = await sendPrompt(payload.prompt, scenario, abortController.signal);
      if (token === runToken && result?.run_id) {
        currentRunId = result.run_id;
        // Mark execution as active on the server: the run is now in progress.
        // This keeps the Interrupt button visible until the final response
        // arrives (or until status.running becomes false on the next poll).
        isExecutionActive = true;
        await refresh(cachedTopology);
      }
      // Note: we don't automatically set loading=false here because we want to wait for events,
      // handled by refresh loop. But if we want "Stop" to work, we need to know we are done sending.
      // Usually sendPrompt just initiates. The events come later.
      // The refresh loop handles the transition to "not waiting" when events arrive.
  } catch (err) {
      if (err.name === 'AbortError') {
          console.log('Prompt execution aborted');
      } else {
          console.error(err);
          // Real error: roll back running state so UI is not stuck
          isScenarioRunning = false;
      }
      setLoadingState(false);
      updateControlsState();
  } finally {
      abortController = null;
  }
});


toggleOtel.addEventListener("change", () => {
  showOtel = toggleOtel.checked;
  if (cachedTopology) {
    drawTopology(cachedTopology);
  }
});

const toggleAbsoluteTime = document.getElementById("toggle-absolute-time");
if (toggleAbsoluteTime) {
  toggleAbsoluteTime.addEventListener("change", () => {
    useAbsoluteTimestamps = toggleAbsoluteTime.checked;
    updateRelativeTimestamps(); // Immediately update all timestamps
  });
}

if (autoScrollToggle) {
  autoScrollEnabled = autoScrollToggle.checked;
  autoScrollToggle.addEventListener("change", () => {
    autoScrollEnabled = autoScrollToggle.checked;
    if (autoScrollEnabled) {
      activityLog.scrollTop = activityLog.scrollHeight;
    }
  });
}

if (demoModeButton) {
  demoModeButton.addEventListener("click", () => {
    applyDemoMode().catch((error) => {
      console.error("[Demo Mode] Failed to apply demo mode:", error);
    });
  });
}

if (timelineAutoScrollToggle) {
  timelineAutoScroll = timelineAutoScrollToggle.checked;
  timelineAutoScrollToggle.addEventListener("change", () => {
    timelineAutoScroll = timelineAutoScrollToggle.checked;
  });
}

const btnResetLayout = document.getElementById("reset-layout");
if (btnResetLayout) {
  btnResetLayout.addEventListener("click", () => {
    const appId = getCurrentAppId();
    setLayoutMode(appId, "hierarchical-lr");
    layoutSelect.value = "hierarchical-lr";
    if (cachedTopology) drawTopology(cachedTopology);
    resetViewBox();
  });
}

const btnResetView = document.getElementById("reset-view");
if (btnResetView) {
  btnResetView.addEventListener("click", () => {
      // Clear logs and timeline
      resetUiState({ clearTopology: false });
    resetViewBox();
      // Keep topology but gray it out?
      // Reset topology colors
      if (cachedTopology) {
          // discoverFromEvents sets classes based on activity. 
          // If we clear nodeState/edgeState (which resetUiState does), and redraw:
          drawTopology(cachedTopology);
      }
      // Clear ALL metrics
      ["metric-cost", "metric-tokens", "metric-events", "metric-duration", 
       "metric-tools", "metric-reliability", "metric-quality", "metric-perf"].forEach(id => {
          const el = document.getElementById(id);
          if (el) {
            if (id === "metric-cost") el.textContent = "$0.0000";
            else if (id === "metric-duration" || id === "metric-perf") el.textContent = "0s";
            else if (id === "metric-reliability") el.textContent = "100%";
            else if (id === "metric-quality") el.textContent = "--";
            else el.textContent = "0";
            el.classList.remove("status-ok", "status-warn", "status-error");
          }
      });
      // Clear metrics state
      metricsState.lastValues.clear();
  });
}

async function loadDataset() {
  const payload = await fetchJson("/api/dataset");
  return payload || { items: [] };
}

async function loadDatasets() {
  const payload = await fetchJson("/api/datasets").catch(() => null);
  return payload?.datasets || [];
}

async function loadScenarios() {
  const payload = await fetchJson("/api/scenarios");
  return payload?.scenarios || [];
}

async function loadCapabilities() {
  const payload = await fetchJson("/api/capabilities");
  backendName = payload?.backend || "unknown";
  return payload || { control: false, backend: "unknown" };
}

function pickApplications(payload, project) {
  // Prefer explicit dataset name from the benchmark JSON, then the project
  // name derived from the config directory, then a neutral fallback.
  if (payload?.dataset) return [payload.dataset];
  if (project) return [project];
  return ["default"];
}

function pickScenarios(items) {
  const set = new Set();
  items.forEach((item) => set.add(item.scenario || "baseline"));
  return Array.from(set).sort();
}

function pickPrompts(items, scenario) {
  const seen = new Set();
  const prompts = [];
  items.forEach((item) => {
    if (scenario && item.scenario && item.scenario !== scenario) {
      return;
    }
    if (seen.has(item.prompt)) return;
    seen.add(item.prompt);
    prompts.push(item);
  });
  return prompts.slice(0, 10);
}

function hydrateApplicationSelect(applications) {
  if (!appSelect) return;
  appSelect.innerHTML = "";
  applications.forEach((app, index) => {
    const option = document.createElement("option");
    option.value = app;
    option.textContent = app;
    if (index === 0) option.selected = true;
    appSelect.appendChild(option);
  });
  // Always keep the trigger interactive so users can see the active app name.
  if (navAppTrigger) {
    navAppTrigger.style.pointerEvents = "";
    navAppTrigger.removeAttribute("aria-disabled");
  }
}

async function hydratePromptSelect(items, scenario) {
  // Use datalist instead of select
  const dataList = document.getElementById("prompt-options");
  if (!dataList) return;
  
  dataList.innerHTML = "";

  const prompts = pickPrompts(items, scenario);
  
  if (prompts.length === 0) {
      // Do nothing or add generic hint
  } else {
    prompts.forEach((item) => {
      const option = document.createElement("option");
      // Datalist options use 'value' as the text shown/inserted
      option.value = item.prompt; 
      // Store metadata in dataset if needed, but input just reads value
      option.dataset.scenario = item.scenario || "baseline"; 
      dataList.appendChild(option);
    });
  }
}

function hydrateDatasetFilter(datasets) {
  const dropdown = document.getElementById("nav-dataset-dropdown");
  if (!dropdown) return;
  dropdown.innerHTML = "";
  if (!datasets || datasets.length === 0) return;
  const currentName = currentDataset?.dataset || "";
  datasets.forEach((ds) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "nav-dataset-item";
    btn.dataset.file = ds.file;
    btn.textContent = ds.name;
    if (ds.name === currentName || (datasets.length === 1 && !currentName)) btn.classList.add("is-active");
    dropdown.appendChild(btn);
  });
  // Sync label text with loaded dataset
  const label = document.getElementById("nav-dataset-label");
  if (label) label.textContent = currentName || (datasets[0]?.name ?? "Dataset");
}

async function hydrateScenarioSelect(scenarios) {
  // scenarios: string[] from /api/scenarios (configs/*.json stems)
  scenarioSelect.innerHTML = "";
  
  if (scenarios.length === 0) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "No scenarios";
    scenarioSelect.appendChild(placeholder);
  } else {
    scenarios.forEach((scenario, index) => {
      const option = document.createElement("option");
      option.value = scenario;
      option.textContent = scenario;
      if (index === 0) option.selected = true;
      scenarioSelect.appendChild(option);
    });
  }
}

async function setScenario(scenario) {
  const token = selectionToken;
  const userModel = userModelSelect ? userModelSelect.value : "manual";
  const response = await fetch("/api/scenario", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario, user_model: userModel }),
  }).catch(() => {
    setControllerStatus(false, "Controller offline");
  });
  if (!response || !response.ok) {
    return;
  }
  const result = await response.json();
  if (token === selectionToken && result?.run_id) {
    currentRunId = result.run_id;
  }
}

async function syncSelections() {
  setResyncing(true);
  const token = ++selectionToken;
  try {
    // Fetch status, apps, dataset and scenarios in parallel.
    const [statusPayload, appsPayload, datasetPayload, apiScenarios, availableDatasets] = await Promise.all([
      fetchJson("/api/status").catch(() => ({})),
      fetchJson("/api/apps").catch(() => ({ apps: [] })),
      loadDataset(),
      loadScenarios(),
      loadDatasets(),
    ]);
    if (token !== selectionToken) return;

    currentDataset = datasetPayload;
    const items = currentDataset.items || [];

    // Prefer /api/scenarios (config-driven); fall back to extracting from dataset.
    const scenarios = apiScenarios.length > 0 ? apiScenarios : pickScenarios(items);

    // Populate app selector from /api/apps; fall back to dataset/project name.
    const discoveredApps = appsPayload?.apps || [];
    if (discoveredApps.length > 0 && appSelect) {
      appSelect.innerHTML = "";
      const activeId = statusPayload?.project || "";
      discoveredApps.forEach((app, index) => {
        const option = document.createElement("option");
        option.value = app.id;
        option.textContent = app.name;
        option.dataset.labConfig = app.lab_config || "";
        if (app.id === activeId || index === 0) option.selected = true;
        appSelect.appendChild(option);
      });
      // Always keep the trigger interactive.
      if (navAppTrigger) {
        navAppTrigger.style.pointerEvents = "";
        navAppTrigger.removeAttribute("aria-disabled");
      }
    } else {
      // Fall back to name derived from status/dataset.
      const project = statusPayload?.project || "";
      const applications = pickApplications(currentDataset, project);
      hydrateApplicationSelect(applications);
    }

    hydrateLayoutSelect(getCurrentAppId());

    // Update UI immediately (Application is selected)
    updateControlsState();

    await hydrateScenarioSelect(scenarios);
    await hydratePromptSelect(items, scenarioSelect.value || null);
    hydrateDatasetFilter(availableDatasets);
    if (scenarioSelect.value) {
      await setScenario(scenarioSelect.value);
    }
    updateControlsState();
  } finally {
    if (token === selectionToken) {
      setResyncing(false);
    }
  }
}

async function bootstrap() {
  // Wait for backend to be ready first
  await init();

  const capabilities = await loadCapabilities();
  if (!capabilities.control && promptForm) {
    promptForm.style.display = "none";
  }
  
  // Always attempt to sync selections (populates dropdowns)
  await syncSelections();
}

bootstrap();

if (dockHandle) {
  const savedHeight = Number(localStorage.getItem("mas.dockHeight"));
  if (!Number.isNaN(savedHeight) && savedHeight > 0) {
    dockHeight = savedHeight;
    document.documentElement.style.setProperty("--dock-height", `${dockHeight}px`);
  }
  dockHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    dockHandle.setPointerCapture(event.pointerId);
    const startY = event.clientY;
    const startHeight = dockHeight;
    const onMove = (moveEvent) => {
      const next = Math.min(320, Math.max(90, startHeight - (moveEvent.clientY - startY)));
      dockHeight = next;
      document.documentElement.style.setProperty("--dock-height", `${dockHeight}px`);
    };
    const onEnd = () => {
      localStorage.setItem("mas.dockHeight", String(dockHeight));
      dockHandle.releasePointerCapture(event.pointerId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
  });
}

if (appSelect) {
  appSelect.addEventListener("change", async () => {
    isScenarioRunning = false;
    updateControlsState();
    resetUiState({ clearTopology: true });
    await syncSelections();
    drawTopology(cachedTopology);
  });
}

if (layoutSelect) {
  layoutSelect.addEventListener("change", () => {
    const appId = getCurrentAppId();
    setLayoutMode(appId, layoutSelect.value);
    drawTopology(cachedTopology);
  });
}

async function stopCurrentSession() {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
  
  if (isScenarioRunning && currentRunId) {
      try {
        await fetch(`/api/run/${currentRunId}/stop`, { method: "POST" });
      } catch (e) {
        console.error("Failed to stop run remotely:", e);
      }
  }
  
  setScenarioStatus("STOPPED");
  isScenarioRunning = false;
  isWaitingForResponse = false;
  promptExecutionActive = false;
  updateControlsState();
}

if (resetLayoutButton) {
  // Rename button to "Reset & Clean" logic if we want, or add new button logic
  // For now, let's keep it as layout reset only, and implement a dedicated clear button action
}

const btnResetAll = document.getElementById("reset-view");
if (btnResetAll) {
    btnResetAll.addEventListener("click", async () => {
        await stopCurrentSession();
        resetUiState({ clearTopology: true });
    });
}

function setPanelVisibility(panel, isVisible) {
  if (!panel) return;
  panel.classList.toggle("is-hidden", !isVisible);
}

function applyPanelState() {
  const showSignals = localStorage.getItem("mas.showSignals") !== "false";
  const showLog = localStorage.getItem("mas.showLog") !== "false";
  const showMetrics = localStorage.getItem("mas.showMetrics") !== "false";
  
  setPanelVisibility(sidePanel, showSignals);
  setPanelVisibility(logDock, showLog);
  setPanelVisibility(metricsPanel, showMetrics);
  if (metricsHandle) setPanelVisibility(metricsHandle, showMetrics);
  
  board?.classList.toggle("has-no-panel", !showSignals);
  
  // Update CSS variable based on visibility and drag state
  if (!showMetrics) {
    document.documentElement.style.setProperty("--metrics-panel-width", "0px");
  } else {
    // Restore saved width or default
    // Check if inline style is set? No, we use CSS var.
    // We should rely on localStorage for width if dragging implemented
    // But for now, just let the CSS var defined in root or style take effect.
    // If dragging sets it, it persists in style attribute of html.
    // If we just toggle it, we need to ensure it's not 0.
    const current = getComputedStyle(document.documentElement).getPropertyValue("--metrics-panel-width").trim();
    if (current === "0px") {
       document.documentElement.style.setProperty("--metrics-panel-width", "280px");
    }
  }

  if (!showLog) {
    document.documentElement.style.setProperty("--dock-height", "0px");
  } else {
    document.documentElement.style.setProperty("--dock-height", `${dockHeight}px`);
  }
  if (toggleSignals) toggleSignals.checked = showSignals;
  if (toggleLog) toggleLog.checked = showLog;
  if (toggleMetrics) toggleMetrics.checked = showMetrics;
  
  if (!showLog && panelMenu) {
    panelMenu.classList.remove("is-open");
  }
}

if (panelMenuButton && panelMenu) {
  panelMenuButton.addEventListener("click", () => {
    panelMenu.classList.toggle("is-open");
  });
  window.addEventListener("click", (event) => {
    if (!panelMenu.contains(event.target) && event.target !== panelMenuButton) {
      panelMenu.classList.remove("is-open");
    }
  });
}

if (toggleSignals) {
  toggleSignals.addEventListener("change", () => {
    localStorage.setItem("mas.showSignals", toggleSignals.checked ? "true" : "false");
    applyPanelState();
  });
}

if (toggleLog) {
  toggleLog.addEventListener("change", () => {
    localStorage.setItem("mas.showLog", toggleLog.checked ? "true" : "false");
    applyPanelState();
  });
}

if (toggleMetrics) {
  toggleMetrics.addEventListener("change", () => {
    localStorage.setItem("mas.showMetrics", toggleMetrics.checked ? "true" : "false");
    applyPanelState();
  });
}

if (hideSignals) {
  hideSignals.addEventListener("click", () => {
    localStorage.setItem("mas.showSignals", "false");
    applyPanelState();
  });
}

if (hideLog) {
  hideLog.addEventListener("click", () => {
    localStorage.setItem("mas.showLog", "false");
    applyPanelState();
  });
}

if (hideMetrics) {
  hideMetrics.addEventListener("click", () => {
    localStorage.setItem("mas.showMetrics", "false");
    applyPanelState();
  });
}

if (metricsHandle) {
  let startX = 0;
  let startWidth = 0;
  metricsHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    metricsHandle.setPointerCapture(event.pointerId);
    startX = event.clientX;
    const styles = getComputedStyle(document.documentElement);
    startWidth = parseInt(styles.getPropertyValue("--metrics-panel-width") || "280", 10);
    const onMove = (moveEvent) => {
      // Dragging left increases width
      const diff = startX - moveEvent.clientX; // Left = + width
      const next = Math.min(600, Math.max(200, startWidth + diff));
      document.documentElement.style.setProperty("--metrics-panel-width", `${next}px`);
    };
    const onEnd = () => {
      metricsHandle.releasePointerCapture(event.pointerId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
  });
}

if (sideHandle) {
  let startX = 0;
  let startWidth = 0;
  sideHandle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    sideHandle.setPointerCapture(event.pointerId);
    startX = event.clientX;
    startWidth = parseInt(getComputedStyle(document.documentElement).getPropertyValue("--side-panel-width"), 10);
    const onMove = (moveEvent) => {
      const next = Math.min(460, Math.max(200, startWidth + (startX - moveEvent.clientX)));
      document.documentElement.style.setProperty("--side-panel-width", `${next}px`);
    };
    const onEnd = () => {
      sideHandle.releasePointerCapture(event.pointerId);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
  });
}

applyPanelState();

if (scenarioSelect) {
  scenarioSelect.addEventListener("change", async () => {
    if (!scenarioSelect.value) return;
    
    // Reset running state on scenario change
    isScenarioRunning = false;
    updateControlsState();
    
    selectionToken += 1;
    resetUiState({ clearTopology: true });
    await hydratePromptSelect(currentDataset.items || [], scenarioSelect.value);
    await setScenario(scenarioSelect.value);
  });
}

function renderServiceStatusList(services) {
  if (!serviceStatusList) return;
  serviceStatusList.innerHTML = "";
  if (!services || !services.length) {
    const empty = document.createElement("div");
    empty.className = "service-status-empty";
    empty.textContent = "No services discovered";
    serviceStatusList.appendChild(empty);
    return;
  }
  services.forEach((service) => {
    const item = document.createElement("div");
    item.className = "service-status-item";
    if (service.detail) {
      item.title = service.detail;
    }

    const label = document.createElement("span");
    label.className = "service-status-label";
    label.textContent = service.label || service.id || "unknown";

    const pill = document.createElement("span");
    const status = service.status || "unknown";
    pill.className = `service-status-pill service-status-pill--${status}`;
    pill.textContent = status.toUpperCase();
    if (service.detail) {
      pill.title = service.detail;
    }

    item.appendChild(label);
    item.appendChild(pill);
    serviceStatusList.appendChild(item);
  });
}

async function refreshServiceStatus(force) {
  const payload = await fetchServiceStatus();
  if (!payload) {
    serviceOverall = "error";
    servicesOk = false;
    if (serviceStatusSummary) {
      serviceStatusSummary.textContent = "Controller offline";
    }
    applyOverallStatus();
    setStatusWorking(false);
    updateControlsState();
    return;
  }
  serviceOverall = payload.overall || "warn";
  // Only block UI if critical error, not for warnings
  servicesOk = (serviceOverall === "ok" || serviceOverall === "warn");
  const recovery = payload.recovery || { attempted: false, restarted: [], failed: [] };
  const attempted = !!recovery.attempted;
  const restarted = Array.isArray(recovery.restarted) ? recovery.restarted : [];
  const failed = Array.isArray(recovery.failed) ? recovery.failed : [];
  setStatusWorking(attempted && (restarted.length > 0 || failed.length > 0));
  if (serviceStatusSummary) {
    if (failed.length > 0) {
      serviceStatusSummary.textContent = "Recovery failed - reset";
    } else if (restarted.length > 0) {
      serviceStatusSummary.textContent = `Recovered ${restarted.length}`;
    } else if (attempted) {
      serviceStatusSummary.textContent = "Recovering...";
    } else {
      serviceStatusSummary.textContent = serviceOverall === "ok" ? "All ok" : "Service issues";
    }
  }
  renderServiceStatusList(payload.services || []);
  applyOverallStatus();
  updateControlsState();
}

// =============================================================================
// ScrubberController — timeline scrubber at the bottom of the UI
//
// Responsibilities
// ----------------
//  • Track tMin / tMax from incoming events (live mode auto-advances).
//  • Let the user drag the range to any point in the run (scrub mode).
//  • Play / pause: animates cursorTs forward via requestAnimationFrame.
//  • Live button: jumps back to the end and resumes live tracking.
//  • KG stats badge: fetches /api/kg/snapshot (live) or /api/kg/at?t=<ts>
//    (scrub) and displays "KG: Nn / Me" in the corner.
//
// Design: pure JS class, zero external dependencies beyond the existing DOM.
// =============================================================================
class ScrubberController {
  constructor() {
    // Playback state
    this.isLive = true;      // true = follow live events; false = scrub mode
    this.isPlaying = false;  // true = RAF-driven forward playback
    this.speed = 1;          // playback speed multiplier (0.5 / 1 / 2 / 4 / 8)
    this.cursorTs = 0;       // current position (Unix seconds)
    this.tMin = 0;           // earliest known event timestamp
    this.tMax = 0;           // latest known event timestamp

    // Internal
    this._rafId = null;
    this._lastRafTime = null;
    this._kg = { node_count: 0, edge_count: 0 };
    this._kgPollTimer = null;

    // DOM refs (elements added by index.html)
    this._range = document.getElementById('scrubber-range');
    this._playPause = document.getElementById('scrubber-play-pause');
    this._liveBtn = document.getElementById('scrubber-live');
    this._timeCurrent = document.getElementById('scrubber-time-current');
    this._timeTotal = document.getElementById('scrubber-time-total');
    this._speedSelect = document.getElementById('scrubber-speed');
    this._kgStats = document.getElementById('scrubber-kg-stats');
    this._iconPlay = document.getElementById('scrubber-icon-play');
    this._iconPause = document.getElementById('scrubber-icon-pause');
    this._fill = document.getElementById('scrubber-progress-fill');

    this._bindEvents();
    this._startKgPolling();
    this._updateLiveBtn(); // reflect initial live state in DOM
  }

  // ------------------------------------------------------------------
  // Public API — called by the main refresh() loop
  // ------------------------------------------------------------------

  /** Update time range with the latest full event list. */
  onEventsUpdated(events) {
    const timestamps = events.map(e => e.timestamp || 0).filter(Boolean);
    if (!timestamps.length) return;
    const newMin = Math.min(...timestamps);
    const newMax = Math.max(...timestamps);
    if (!this.tMin || newMin < this.tMin) this.tMin = newMin;
    if (newMax > this.tMax) this.tMax = newMax;
    if (this.isLive) {
      this.cursorTs = newMax;
      this._render();
    } else {
      // Only update total duration display, leave cursor where the user put it
      if (this._timeTotal) {
        this._timeTotal.textContent = this._formatDuration(this.tMax - this.tMin);
      }
    }
  }

  // ------------------------------------------------------------------
  // Event handlers
  // ------------------------------------------------------------------

  /** User drags the range input. */
  _onRangeInput(e) {
    const frac = parseInt(e.target.value, 10) / 1000;
    const range = this.tMax - this.tMin;
    this.cursorTs = range > 0 ? this.tMin + frac * range : this.tMin;
    this._exitLive();
    this._stopRaf();
    this.isPlaying = false;
    this._updatePlayPauseBtn();
    this._render();
  }

  /** User releases the range — fetch KG state at the scrubbed time. */
  _onRangeChange() {
    if (!this.isLive) this._fetchKgAtCursor();
  }

  /** Toggle play / pause. */
  _onPlayPause() {
    if (this.isLive) {
      // Enter scrub mode at the current (live) position
      this._exitLive();
    }
    this.isPlaying = !this.isPlaying;
    this._updatePlayPauseBtn();
    if (this.isPlaying) {
      // If at the end, restart from the beginning
      if (this.cursorTs >= this.tMax) this.cursorTs = this.tMin;
      this._lastRafTime = null;
      this._startRaf();
    } else {
      this._stopRaf();
      this._fetchKgAtCursor();
    }
  }

  /** Jump to live. */
  _onLive() {
    this._stopRaf();
    this.isLive = true;
    this.isPlaying = false;
    this.cursorTs = this.tMax;
    this._updateLiveBtn();
    this._updatePlayPauseBtn();
    this._render();
    this._updateKgStats();  // immediate refresh
  }

  // ------------------------------------------------------------------
  // RAF-based playback
  // ------------------------------------------------------------------

  _startRaf() {
    const tick = (now) => {
      if (!this.isPlaying) return;
      if (this._lastRafTime !== null) {
        const dt = (now - this._lastRafTime) / 1000; // real seconds elapsed
        this.cursorTs += dt * this.speed;
        if (this.cursorTs >= this.tMax) {
          this.cursorTs = this.tMax;
          this.isPlaying = false;
          this._updatePlayPauseBtn();
          this._render();
          this._fetchKgAtCursor();
          return;
        }
      }
      this._lastRafTime = now;
      this._render();
      this._rafId = requestAnimationFrame(tick);
    };
    this._rafId = requestAnimationFrame(tick);
  }

  _stopRaf() {
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._lastRafTime = null;
  }

  // ------------------------------------------------------------------
  // Rendering helpers
  // ------------------------------------------------------------------

  /** Sync all scrubber DOM to current state. */
  _render() {
    const range = this.tMax - this.tMin;
    const frac = range > 0 ? Math.max(0, Math.min(1, (this.cursorTs - this.tMin) / range)) : 1;
    const val = Math.round(frac * 1000);
    if (this._range) this._range.value = val;
    if (this._fill) this._fill.style.width = `${frac * 100}%`;
    if (this._timeCurrent) {
      this._timeCurrent.textContent = this._formatDuration(this.cursorTs - this.tMin);
    }
    if (this._timeTotal) {
      this._timeTotal.textContent = this._formatDuration(this.tMax - this.tMin);
    }
  }

  _updateLiveBtn() {
    if (this._liveBtn) this._liveBtn.classList.toggle('is-live', this.isLive);
    // Sync the scrubber bar itself so CSS can hide the play button in live mode
    const bar = document.getElementById('scrubber-bar');
    if (bar) bar.classList.toggle('is-live', this.isLive);
  }

  _updatePlayPauseBtn() {
    if (this._iconPlay) this._iconPlay.style.display = this.isPlaying ? 'none' : '';
    if (this._iconPause) this._iconPause.style.display = this.isPlaying ? '' : 'none';
  }

  _exitLive() {
    this.isLive = false;
    this._updateLiveBtn();
  }

  /** Format a duration in seconds as "m:ss". */
  _formatDuration(secs) {
    if (!secs || secs < 0) return '0:00';
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  // ------------------------------------------------------------------
  // KG stats (fetched from server)
  // ------------------------------------------------------------------

  async _fetchKgAtCursor() {
    if (!this.cursorTs) return;
    try {
      const res = await fetch(`/api/kg/at?t=${this.cursorTs.toFixed(3)}`);
      if (!res.ok) return;
      this._kg = await res.json();
      this._renderKgStats();
    } catch (_) { /* network errors are non-fatal */ }
  }

  async _updateKgStats() {
    try {
      const res = await fetch('/api/kg/snapshot');
      if (!res.ok) return;
      this._kg = await res.json();
      this._renderKgStats();
    } catch (_) { /* non-fatal */ }
  }

  _renderKgStats() {
    if (!this._kgStats) return;
    const n = this._kg.node_count || 0;
    const e = this._kg.edge_count || 0;
    this._kgStats.textContent = `KG: ${n}n / ${e}e`;
  }

  _startKgPolling() {
    // In live mode: refresh KG stats every 3 s
    this._kgPollTimer = setInterval(() => {
      if (this.isLive) this._updateKgStats();
    }, 3000);
    this._updateKgStats();
  }

  // ------------------------------------------------------------------
  // DOM bindings
  // ------------------------------------------------------------------

  _bindEvents() {
    if (this._range) {
      this._range.addEventListener('input', this._onRangeInput.bind(this));
      this._range.addEventListener('change', this._onRangeChange.bind(this));
    }
    if (this._playPause) {
      this._playPause.addEventListener('click', this._onPlayPause.bind(this));
    }
    if (this._liveBtn) {
      this._liveBtn.addEventListener('click', this._onLive.bind(this));
    }
    if (this._speedSelect) {
      this._speedSelect.addEventListener('change', (e) => {
        this.speed = parseFloat(e.target.value) || 1;
      });
    }
  }
}

// Instantiate after DOM is available (script runs after body).
const scrubberController = new ScrubberController();
