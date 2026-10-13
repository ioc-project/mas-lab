//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle, CodeBlock, TabPanel } from "@/components";
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  FormControl,
  FormControlLabel,
  IconButton,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import {
  Add as AddIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandMoreIcon,
} from "@mui/icons-material";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { parse, stringify } from "yaml";
import type {
  Experiment,
  InfraLlmMode,
  InfraToolsMode,
  LevelSpec,
  OverlayRef,
  PipelineHookEntry,
  RuntimeCacheMode,
} from "@/types/experiment-types";
import {
  useScenarios,
  useDatasets,
  useOverlays,
  usePipelines,
  useExperimentContent,
  createExperiment,
  updateExperimentApi,
  type OverlayEntry,
} from "@/api/apiCalls";

const TAB_KEYS = ["setup", "yaml"] as const;
type ExperimentTab = (typeof TAB_KEYS)[number];

interface ScenarioFormData {
  id: string;
  description: string;
  tags: string;
  overlays: string[];
}

/**
 * Convert an overlay display name (used in the UI multi-select) back to a
 * ``{ref: path}`` object for the experiment YAML.
 *
 * Display names use the ``global/`` prefix for library-root overlays;
 * app-scoped overlays use their bare name.  The ``path`` field from the
 * OverlayEntry carries the filesystem-relative path.
 */
function overlayDisplayNameToRef(
  displayName: string,
  overlayOptions: OverlayEntry[],
): { ref: string } | string {
  const match = overlayOptions.find(
    (o) => overlayDisplayName(o) === displayName,
  );
  if (match?.path) return { ref: match.path };
  return displayName;
}

/** Build a UI display name for an overlay entry: ``global/name`` or bare ``name``. */
function overlayDisplayName(entry: OverlayEntry): string {
  if (!entry.namespace || entry.namespace === "global") {
    return `global/${entry.name}`;
  }
  return entry.name;
}

/**
 * Convert an ``OverlayRef`` from an experiment YAML (either a bare string or
 * ``{ref: path}``) back to a UI display name for the multi-select.
 */
function overlayRefToDisplayName(
  ref: OverlayRef,
  overlayOptions: OverlayEntry[],
): string {
  if (typeof ref === "string") return ref;
  const path = ref.ref;
  const match = overlayOptions.find((o) => o.path === path);
  if (match) return overlayDisplayName(match);
  const stem =
    path
      .replace(/\.yaml$/, "")
      .split("/")
      .pop() ?? path;
  return stem;
}

function OverlaySelect({
  value,
  onChange,
  overlays,
}: {
  value: string[];
  onChange: (val: string[]) => void;
  overlays: OverlayEntry[];
}) {
  const sorted = useMemo(
    () => [
      ...overlays.filter((o) => !o.namespace || o.namespace === "global"),
      ...overlays.filter((o) => o.namespace && o.namespace !== "global"),
    ],
    [overlays],
  );

  const selected = value[0] ?? "";

  return (
    <FormControl variant="standard" sx={{ minWidth: 350, flex: 1 }}>
      <InputLabel>Overlay</InputLabel>
      <Select
        value={selected}
        label="Overlay"
        onChange={(e) => {
          const v = e.target.value as string;
          onChange(v ? [v] : []);
        }}
      >
        <MenuItem value="">
          <em>None</em>
        </MenuItem>
        {sorted.map((o) => {
          const dn = overlayDisplayName(o);
          return (
            <MenuItem key={dn} value={dn}>
              {dn}
            </MenuItem>
          );
        })}
      </Select>
    </FormControl>
  );
}

// ── Pipeline hooks ──────────────────────────────────────────────────────────
// Experiment manifests carry per-level lifecycle hooks (application / scenario /
// test / run), each with `pre:` and `post:` arrays of pipelines.  We only author
// the reference form: { ref: "pipelines/<name>.yaml" } (library-root-relative,
// matching how datasets/apps are referenced).  Inline steps / {id} are never
// emitted; any such hand-written entries are preserved untouched.

const HOOK_LEVELS = ["application", "scenario", "test", "run"] as const;
type HookLevel = (typeof HOOK_LEVELS)[number];
type HookPhase = "pre" | "post";

/** Per-level pre/post picker selections (pipeline stems or raw preserved refs). */
type HookSelections = Record<HookLevel, Record<HookPhase, string[]>>;
/** Per-level pre/post entries we don't author (inline steps/id) — kept verbatim. */
type HookOpaque = Record<HookLevel, Record<HookPhase, PipelineHookEntry[]>>;

interface PipelineOption {
  /** File stem — the value written into `pipelines/<stem>.yaml`. */
  stem: string;
  /** Human label — pipeline metadata name, falling back to the stem. */
  label: string;
}

const HOOK_LEVEL_META: Record<HookLevel, { title: string; hint: string }> = {
  application: {
    title: "Application",
    hint: "Runs once — pre before the whole experiment, post after all scenarios.",
  },
  scenario: { title: "Scenario", hint: "Runs once per scenario." },
  test: { title: "Test", hint: "Runs once per test (scenario × item)." },
  run: {
    title: "Run",
    hint: "Runs before/after EVERY run — expensive.",
  },
};

/** Empty per-level pre/post map (serves both HookSelections and HookOpaque). */
function emptyHookMap<T>(): Record<HookLevel, Record<HookPhase, T[]>> {
  return {
    application: { pre: [], post: [] },
    scenario: { pre: [], post: [] },
    test: { pre: [], post: [] },
    run: { pre: [], post: [] },
  };
}

/** Strip a `.yaml`/`.yml` extension and any directory, leaving the file stem. */
function refToStem(ref: string): string {
  return (
    ref
      .replace(/\.ya?ml$/i, "")
      .split("/")
      .pop() ?? ref
  );
}

/** The ref path of a hook entry (bare string or `{ref}`), or undefined if inline. */
function hookEntryRef(entry: PipelineHookEntry): string | undefined {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object" && "ref" in entry) {
    const r = (entry as { ref?: unknown }).ref;
    if (typeof r === "string") return r;
  }
  return undefined;
}

/**
 * Split a raw pre/post array into picker values (stem when known, else the raw
 * ref preserved so it stays visible) and opaque entries we don't author.
 */
function splitHookPhase(
  entries: PipelineHookEntry[] | undefined,
  stems: Set<string>,
): { values: string[]; opaque: PipelineHookEntry[] } {
  const values: string[] = [];
  const opaque: PipelineHookEntry[] = [];
  for (const entry of entries ?? []) {
    const ref = hookEntryRef(entry);
    if (ref === undefined) {
      opaque.push(entry);
      continue;
    }
    const stem = refToStem(ref);
    values.push(stems.has(stem) ? stem : ref);
  }
  return { values, opaque };
}

/**
 * Build a serialized pre/post array from picker values + preserved opaque
 * entries.  Known stems become `{ ref: "pipelines/<stem>.yaml" }`; unmatched raw
 * values are re-emitted as `{ ref: <raw> }`.  Returns undefined when empty.
 */
function buildHookPhase(
  values: string[],
  opaque: PipelineHookEntry[],
  stems: Set<string>,
): PipelineHookEntry[] | undefined {
  const out: PipelineHookEntry[] = values.map((v) =>
    stems.has(v) ? { ref: `pipelines/${v}.yaml` } : { ref: v },
  );
  for (const entry of opaque) out.push(entry);
  return out.length ? out : undefined;
}

/** Multi-select chip picker over the library's pipelines (order preserved). */
function PipelineHookSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string[];
  options: PipelineOption[];
  onChange: (val: string[]) => void;
}) {
  const optionStems = useMemo(
    () => new Set(options.map((o) => o.stem)),
    [options],
  );
  // Raw refs that don't match a known pipeline still need a MenuItem so MUI can
  // render them as selected chips (and keep them in the value).
  const extras = value.filter((v) => !optionStems.has(v));

  return (
    <FormControl variant="standard" sx={{ flex: 1, minWidth: 0 }}>
      <InputLabel>{label}</InputLabel>
      <Select
        multiple
        value={value}
        label={label}
        onChange={(e) => {
          const v = e.target.value;
          onChange(typeof v === "string" ? v.split(",") : v);
        }}
        renderValue={(selected) => (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
            {(selected as string[]).map((s) => {
              const opt = options.find((o) => o.stem === s);
              return <Chip key={s} size="small" label={opt ? opt.label : s} />;
            })}
          </Box>
        )}
      >
        {options.map((o) => (
          <MenuItem key={o.stem} value={o.stem}>
            {o.label}
          </MenuItem>
        ))}
        {extras.map((v) => (
          <MenuItem key={v} value={v}>
            {v}
          </MenuItem>
        ))}
      </Select>
    </FormControl>
  );
}

/** One level's hooks: a scope hint plus Pre and Post pickers side by side. */
function HookSection({
  level,
  selections,
  options,
  onChange,
}: {
  level: HookLevel;
  selections: Record<HookPhase, string[]>;
  options: PipelineOption[];
  onChange: (phase: HookPhase, val: string[]) => void;
}) {
  const meta = HOOK_LEVEL_META[level];
  return (
    <Stack sx={{ gap: "8px" }}>
      <Stack direction="row" alignItems="baseline" sx={{ gap: "8px" }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          {meta.title}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {meta.hint}
        </Typography>
      </Stack>
      <Stack direction="row" sx={{ gap: "16px" }}>
        <PipelineHookSelect
          label="Pre"
          value={selections.pre}
          options={options}
          onChange={(v) => onChange("pre", v)}
        />
        <PipelineHookSelect
          label="Post"
          value={selections.post}
          options={options}
          onChange={(v) => onChange("post", v)}
        />
      </Stack>
    </Stack>
  );
}

const CreateExperiment = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const {
    library = "",
    id: routeName,
    experimentTab,
  } = useParams<{
    library: string;
    id: string;
    experimentTab: string;
  }>();

  const isEditing = Boolean(routeName);
  const editingName = routeName ? decodeURIComponent(routeName) : "";

  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const [configsDir, setConfigsDir] = useState("apps");
  const [masManifest, setMasManifest] = useState("");
  const [usePatchOverlays, setUsePatchOverlays] = useState(false);

  const [scenarios, setScenarios] = useState<ScenarioFormData[]>([
    { id: "", description: "", tags: "", overlays: [] },
  ]);

  const [datasetPath, setDatasetPath] = useState("");

  const [nRuns, setNRuns] = useState("1");
  const [parallelScenarios, setParallelScenarios] = useState("3");
  const [timeout, setTimeout] = useState("300");
  const [pauseBetweenRuns, setPauseBetweenRuns] = useState("1.0");
  const [strategy, setStrategy] = useState("coverage");

  const [infraLlm, setInfraLlm] = useState<InfraLlmMode>("live");
  const [infraTools, setInfraTools] = useState<InfraToolsMode>("live");
  const [runtimeCache, setRuntimeCache] =
    useState<RuntimeCacheMode>("content-addressed");

  const [hooks, setHooks] = useState<HookSelections>(() => emptyHookMap());
  const [hookOpaque, setHookOpaque] = useState<HookOpaque>(() =>
    emptyHookMap(),
  );
  const [advancedHooksOpen, setAdvancedHooksOpen] = useState(false);

  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");

  const { data: scenarioOptions = [], isLoading: scenariosLoading } =
    useScenarios(library);
  const { data: datasetOptions = [], isLoading: datasetsLoading } =
    useDatasets(library);
  const { data: overlayOptions = [], isLoading: overlaysLoading } =
    useOverlays(library);
  const { data: pipelineOptions = [], isLoading: pipelinesLoading } =
    usePipelines(library);
  const { data: editContent, isLoading: editContentLoading } =
    useExperimentContent(library, editingName);

  // Available pipelines as picker options: value = file stem, label = name.
  const pipelinePickerOptions = useMemo<PipelineOption[]>(
    () =>
      pipelineOptions.map((p) => {
        const stem = refToStem(p.filename || p.name);
        return { stem, label: p.name || stem };
      }),
    [pipelineOptions],
  );
  const pipelineStems = useMemo(
    () => new Set(pipelinePickerOptions.map((o) => o.stem)),
    [pipelinePickerOptions],
  );

  const selectedTab = Math.max(
    0,
    TAB_KEYS.indexOf(experimentTab as ExperimentTab),
  );

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newValue: number) => {
      const newTab = TAB_KEYS[newValue];
      const basePath = experimentTab
        ? location.pathname.replace(/\/[^/]+$/, `/${newTab}`)
        : `${location.pathname}/${newTab}`;
      navigate(basePath, { replace: true });
    },
    [navigate, location.pathname, experimentTab],
  );

  const filteredOverlays = useMemo(() => {
    if (!usePatchOverlays) return overlayOptions;
    const globalOnly = overlayOptions.filter(
      (o) => !o.namespace || o.namespace === "global",
    );
    if (!masManifest.trim()) return globalOnly;
    return overlayOptions.filter(
      (o) =>
        !o.namespace ||
        o.namespace === "global" ||
        o.namespace === masManifest.trim(),
    );
  }, [overlayOptions, usePatchOverlays, masManifest]);

  const populateFromExperiment = useCallback(
    (exp: Experiment) => {
      setName(exp.name);
      setDescription(exp.description ?? "");
      const app0 = exp.applications?.[0];
      setConfigsDir(app0?.configs_dir ?? "apps");
      const rawManifest = app0?.manifest ?? "";
      setMasManifest(
        rawManifest
          .replace(/^apps\//, "")
          .replace(/\/mas\.yaml$/, "")
          .replace(/\.yaml$/, ""),
      );

      const hasOverlays =
        exp.scenarios?.some((s) => {
          const ov = s.overlays;
          if (!ov) return false;
          return (
            (ov.logic?.length ?? 0) +
              (ov.control?.length ?? 0) +
              (ov.infra?.length ?? 0) >
            0
          );
        }) ?? false;
      setUsePatchOverlays(hasOverlays);

      setScenarios(
        exp.scenarios?.length
          ? exp.scenarios.map((s) => {
              const allOverlays = [
                ...(s.overlays?.logic ?? []),
                ...(s.overlays?.control ?? []),
                ...(s.overlays?.infra ?? []),
              ];
              return {
                id: s.id,
                description: s.description ?? "",
                tags: s.tags?.join(", ") ?? "",
                overlays: allOverlays.map((ov) =>
                  overlayRefToDisplayName(ov, overlayOptions),
                ),
              };
            })
          : [{ id: "", description: "", tags: "", overlays: [] }],
      );
      setDatasetPath(exp.dataset?.path ?? "");
      setNRuns(exp.run?.n_runs?.toString() ?? "1");
      setParallelScenarios(
        exp.execution?.parallel_scenarios?.toString() ?? "3",
      );
      setTimeout(exp.execution?.timeout?.toString() ?? "300");
      setPauseBetweenRuns(
        exp.execution?.pause_between_runs?.toString() ?? "1.0",
      );
      setStrategy(exp.execution?.strategy ?? "coverage");
      setInfraLlm(exp.execution?.emulation?.infra?.llm ?? "live");
      setInfraTools(exp.execution?.emulation?.infra?.tools ?? "live");
      setRuntimeCache(
        exp.execution?.emulation?.runtime?.cache ?? "content-addressed",
      );

      // Level hooks: map each `{ref: pipelines/<name>.yaml}` back to its stem
      // (matched against known pipelines); unmatched refs and inline entries are
      // preserved so nothing hand-authored is lost.
      const nextHooks: HookSelections = emptyHookMap();
      const nextOpaque: HookOpaque = emptyHookMap();
      for (const level of HOOK_LEVELS) {
        const section = exp[level] as LevelSpec | undefined;
        if (!section) continue;
        const pre = splitHookPhase(section.pre, pipelineStems);
        const post = splitHookPhase(section.post, pipelineStems);
        nextHooks[level] = { pre: pre.values, post: post.values };
        nextOpaque[level] = { pre: pre.opaque, post: post.opaque };
      }
      setHooks(nextHooks);
      setHookOpaque(nextOpaque);
      // Reveal the advanced levels if any of them carry hooks.
      const hasAdvancedHooks = (["scenario", "test", "run"] as const).some(
        (l) =>
          nextHooks[l].pre.length ||
          nextHooks[l].post.length ||
          nextOpaque[l].pre.length ||
          nextOpaque[l].post.length,
      );
      if (hasAdvancedHooks) setAdvancedHooksOpen(true);
    },
    [overlayOptions, pipelineStems],
  );

  // Map the fetched experiment onto the form once the content and the overlay
  // options (needed to map overlay refs back to display names) are available.
  // Guarded so a later overlay-options refresh can't clobber in-progress edits.
  const populatedRef = useRef(false);
  useEffect(() => {
    if (
      !isEditing ||
      populatedRef.current ||
      overlaysLoading ||
      pipelinesLoading ||
      !editContent
    )
      return;
    populatedRef.current = true;
    try {
      const raw = parse(editContent.content) as Record<string, unknown>;
      const parsed = (raw?.experiment ?? raw) as Experiment;
      if (parsed) {
        if (!parsed.name) parsed.name = editingName;
        populateFromExperiment(parsed);
      }
    } catch (err) {
      setLoadError(
        err instanceof Error
          ? err.message
          : "Failed to load experiment for editing.",
      );
    }
  }, [
    isEditing,
    overlaysLoading,
    pipelinesLoading,
    editContent,
    editingName,
    populateFromExperiment,
  ]);

  const buildExperimentObject = useCallback((): Record<string, unknown> => {
    const builtScenarios = scenarios
      .filter((s) => s.id.trim())
      .map((s) => {
        const entry: Record<string, unknown> = { id: s.id.trim() };
        if (s.description.trim()) entry.description = s.description.trim();
        const tags = s.tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean);
        if (tags.length) entry.tags = tags;
        if (usePatchOverlays && s.overlays.length > 0) {
          entry.overlays = {
            logic: s.overlays.map((displayName) =>
              overlayDisplayNameToRef(displayName, overlayOptions),
            ),
            control: [],
            infra: [],
          };
        }
        return entry;
      });

    const experiment: Record<string, unknown> = {};
    if (name.trim()) experiment.name = name.trim();
    if (description.trim()) experiment.description = description.trim();

    const appEntry: Record<string, unknown> = {};
    if (usePatchOverlays && masManifest.trim())
      appEntry.manifest = `apps/${masManifest.trim()}/mas.yaml`;
    if (!usePatchOverlays && configsDir.trim())
      appEntry.configs_dir = configsDir.trim();
    if (Object.keys(appEntry).length) experiment.applications = [appEntry];

    if (builtScenarios.length) experiment.scenarios = builtScenarios;

    if (datasetPath.trim()) experiment.dataset = { path: datasetPath.trim() };

    experiment.run = { n_runs: parseInt(nRuns, 10) || 1 };

    experiment.execution = {
      parallel_scenarios: parseInt(parallelScenarios, 10) || undefined,
      timeout: parseInt(timeout, 10) || undefined,
      pause_between_runs: parseFloat(pauseBetweenRuns) || undefined,
      strategy: strategy.trim() || undefined,
      emulation: {
        infra: { llm: infraLlm, tools: infraTools },
        runtime: { cache: runtimeCache },
      },
    };

    // Level hooks: emit `experiment.<level>.{pre,post}` as `{ref: pipelines/…}`
    // lists; skip empty phases so no bare pre:/post: keys appear.  The run level
    // is merged onto the existing run object (which already carries n_runs).
    for (const level of HOOK_LEVELS) {
      const pre = buildHookPhase(
        hooks[level].pre,
        hookOpaque[level].pre,
        pipelineStems,
      );
      const post = buildHookPhase(
        hooks[level].post,
        hookOpaque[level].post,
        pipelineStems,
      );
      if (!pre && !post) continue;
      const section =
        (experiment[level] as Record<string, unknown> | undefined) ?? {};
      if (pre) section.pre = pre;
      if (post) section.post = post;
      experiment[level] = section;
    }

    return { experiment };
  }, [
    name,
    description,
    configsDir,
    masManifest,
    scenarios,
    usePatchOverlays,
    datasetPath,
    nRuns,
    parallelScenarios,
    timeout,
    pauseBetweenRuns,
    strategy,
    infraLlm,
    infraTools,
    runtimeCache,
    overlayOptions,
    hooks,
    hookOpaque,
    pipelineStems,
  ]);

  const yamlPreview = useMemo(
    () => stringify(buildExperimentObject(), { lineWidth: 120 }),
    [buildExperimentObject],
  );

  const addScenario = () => {
    setScenarios([
      ...scenarios,
      { id: "", description: "", tags: "", overlays: [] },
    ]);
  };

  const removeScenario = (index: number) => {
    setScenarios(scenarios.filter((_, i) => i !== index));
  };

  const updateHook = useCallback(
    (level: HookLevel, phase: HookPhase, values: string[]) => {
      setHooks((prev) => ({
        ...prev,
        [level]: { ...prev[level], [phase]: values },
      }));
    },
    [],
  );

  const updateScenario = (
    index: number,
    field: keyof ScenarioFormData,
    value: string | string[],
  ) => {
    const updated = [...scenarios];
    updated[index] = { ...updated[index], [field]: value };
    setScenarios(updated);
  };

  const handlePatchOverlaysToggle = (checked: boolean) => {
    setUsePatchOverlays(checked);
    setConfigsDir(checked ? "overlays" : "apps");
    setScenarios([{ id: "", description: "", tags: "", overlays: [] }]);
  };

  const canSave =
    !saving &&
    Boolean(name.trim()) &&
    scenarios.some((s) => s.id.trim()) &&
    Boolean(datasetPath.trim()) &&
    Boolean(nRuns.trim()) &&
    Boolean(parallelScenarios.trim()) &&
    Boolean(timeout.trim()) &&
    Boolean(pauseBetweenRuns.trim());

  const handleSave = useCallback(async () => {
    setError("");
    const trimmedName = name.trim();
    if (!trimmedName) return;

    const yamlContent = stringify(buildExperimentObject(), { lineWidth: 120 });

    setSaving(true);
    try {
      if (isEditing) {
        await updateExperimentApi(library, editingName, {
          name: trimmedName,
          content: yamlContent,
        });
      } else {
        await createExperiment(library, {
          name: trimmedName,
          content: yamlContent,
        });
      }
      // Reset the list hooks (["experiments", …]) plus the per-experiment
      // detail/content hooks, which live under different key prefixes and would
      // otherwise keep serving stale data after an edit (including renames,
      // since these prefixes match across all names).
      queryClient.resetQueries({ queryKey: ["experiments"] });
      queryClient.resetQueries({ queryKey: ["experiment"] });
      queryClient.resetQueries({ queryKey: ["experiment-content"] });
      navigate(`/${library}/experiments`);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save experiment.",
      );
    } finally {
      setSaving(false);
    }
  }, [
    name,
    buildExperimentObject,
    isEditing,
    library,
    editingName,
    queryClient,
    navigate,
  ]);

  const handleCancel = useCallback(() => {
    navigate(`/${library}/experiments`);
  }, [navigate, library]);

  const pageTitle = isEditing ? `Edit Experiment` : "Add Experiment";

  // While editing, keep the loader up until every dependency the form is built
  // from has resolved: the experiment content plus the option queries used to
  // render/map the selects.
  const isFormLoading =
    isEditing &&
    (editContentLoading ||
      overlaysLoading ||
      pipelinesLoading ||
      scenariosLoading ||
      datasetsLoading);

  return (
    <PageWithTitle
      title={
        <Stack
          direction="row"
          sx={{
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Typography
            variant="h5"
            sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
          >
            {pageTitle}
          </Typography>
          <Stack direction="row" sx={{ gap: "8px" }}>
            <Button onClick={handleCancel} disabled={saving}>
              Cancel
            </Button>
            <Button variant="primary" onClick={handleSave} disabled={!canSave}>
              {saving ? "Saving..." : "Save"}
            </Button>
          </Stack>
        </Stack>
      }
    >
      <Stack direction="column" sx={{ width: "100%", height: "100%" }}>
        {loadError && (
          <Alert
            severity="error"
            onClose={() => setLoadError("")}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {loadError}
          </Alert>
        )}

        {isFormLoading ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "50vh",
            }}
          >
            <CircularProgress />
          </Box>
        ) : (
          <>
            <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
              <Tabs value={selectedTab} onChange={handleTabChange}>
                <Tab label="Setup" id="experiment-tab-0" />
                <Tab label="Yaml" id="experiment-tab-1" />
              </Tabs>
            </Box>

            <TabPanel
              value={selectedTab}
              index={0}
              sx={{ flex: 1, overflow: "auto" }}
            >
              <Stack sx={{ gap: "16px", mt: "8px", padding: "8px 0" }}>
                {error && <Alert severity="error">{error}</Alert>}

                <Typography variant="subtitle2" color="text.secondary">
                  Basic Info
                </Typography>
                <Stack direction="row" sx={{ gap: "16px" }}>
                  <TextField
                    label="Name"
                    placeholder="Enter experiment name"
                    variant="standard"
                    autoComplete="off"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                    fullWidth
                    error={Boolean(error)}
                  />
                </Stack>
                <TextField
                  label="Description"
                  placeholder="Enter a description for this experiment"
                  variant="standard"
                  autoComplete="off"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  multiline
                  rows={2}
                  fullWidth
                />

                <Divider />

                <Stack
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                >
                  <Stack
                    direction="column"
                    alignItems="flex-start"
                    sx={{ gap: "4px" }}
                  >
                    <Typography variant="subtitle2" color="text.secondary">
                      Scenarios
                    </Typography>
                    <FormControlLabel
                      control={
                        <Checkbox
                          checked={usePatchOverlays}
                          onChange={(e) =>
                            handlePatchOverlaysToggle(e.target.checked)
                          }
                          size="small"
                        />
                      }
                      label={
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ marginLeft: "4px" }}
                        >
                          Use Patch Overlays
                        </Typography>
                      }
                      sx={{ marginLeft: 0 }}
                    />
                  </Stack>
                  <IconButton size="small" onClick={addScenario}>
                    <AddIcon fontSize="small" />
                  </IconButton>
                </Stack>
                {scenarios.map((scenario, index) => (
                  <Stack
                    key={index}
                    direction="row"
                    sx={{ gap: "8px" }}
                    alignItems="center"
                    justifyContent="center"
                  >
                    {usePatchOverlays ? (
                      <TextField
                        label="ID"
                        placeholder="scenario-id"
                        variant="standard"
                        autoComplete="off"
                        value={scenario.id}
                        onChange={(e) =>
                          updateScenario(index, "id", e.target.value)
                        }
                        required
                        sx={{ width: 400 }}
                      />
                    ) : (
                      <FormControl
                        variant="standard"
                        required
                        sx={{ width: 350 }}
                      >
                        <InputLabel>ID</InputLabel>
                        <Select
                          value={scenario.id}
                          label="ID"
                          onChange={(e) =>
                            updateScenario(index, "id", e.target.value)
                          }
                        >
                          {scenarioOptions
                            .filter(
                              (s) =>
                                s.name === scenario.id ||
                                !scenarios.some((sc) => sc.id === s.name),
                            )
                            .map((s) => (
                              <MenuItem key={s.name} value={s.name}>
                                {s.name}
                              </MenuItem>
                            ))}
                        </Select>
                      </FormControl>
                    )}

                    {usePatchOverlays && (
                      <OverlaySelect
                        value={scenario.overlays}
                        onChange={(val) =>
                          updateScenario(index, "overlays", val)
                        }
                        overlays={filteredOverlays}
                      />
                    )}

                    <TextField
                      label="Description"
                      placeholder="Scenario description"
                      variant="standard"
                      autoComplete="off"
                      value={scenario.description}
                      onChange={(e) =>
                        updateScenario(index, "description", e.target.value)
                      }
                      fullWidth
                    />
                    <TextField
                      label="Tags"
                      placeholder="tag1, tag2"
                      variant="standard"
                      autoComplete="off"
                      value={scenario.tags}
                      onChange={(e) =>
                        updateScenario(index, "tags", e.target.value)
                      }
                      sx={{ width: 250 }}
                    />
                    {scenarios.length > 1 && (
                      <IconButton
                        size="medium"
                        onClick={() => removeScenario(index)}
                        sx={{ marginTop: "20px !important" }}
                      >
                        <DeleteIcon fontSize="medium" />
                      </IconButton>
                    )}
                  </Stack>
                ))}

                {usePatchOverlays && (
                  <>
                    <Divider />

                    <Typography variant="subtitle2" color="text.secondary">
                      MAS Configuration
                    </Typography>
                    <FormControl variant="standard" required fullWidth>
                      <InputLabel>Base MAS Application</InputLabel>
                      <Select
                        value={masManifest}
                        label="Base MAS Application"
                        onChange={(e) => {
                          setMasManifest(e.target.value);
                          setScenarios((prev) =>
                            prev.map((s) => ({ ...s, overlays: [] })),
                          );
                        }}
                      >
                        {scenarioOptions.map((s) => (
                          <MenuItem key={s.name} value={s.name}>
                            {s.name}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </>
                )}

                <Divider />

                <Typography variant="subtitle2" color="text.secondary">
                  Dataset
                </Typography>
                <FormControl variant="standard" required fullWidth>
                  <InputLabel>Dataset Path</InputLabel>
                  <Select
                    value={datasetPath}
                    label="Dataset Path"
                    onChange={(e) => setDatasetPath(e.target.value)}
                  >
                    {datasetOptions
                      .filter(
                        (d) =>
                          d.name.endsWith(".yaml") || d.name.endsWith(".yml"),
                      )
                      .map((d) => (
                        <MenuItem key={d.path} value={d.path}>
                          {d.path.replace(/^datasets\//, "")}
                        </MenuItem>
                      ))}
                  </Select>
                </FormControl>

                <Divider />

                <Typography variant="subtitle2" color="text.secondary">
                  Execution
                </Typography>
                <Stack direction="row" sx={{ gap: "16px" }}>
                  <TextField
                    label="Number of Runs"
                    placeholder="1"
                    variant="standard"
                    autoComplete="off"
                    value={nRuns}
                    onChange={(e) => setNRuns(e.target.value)}
                    type="number"
                    required
                    slotProps={{ htmlInput: { min: 1 } }}
                    sx={{ width: 140 }}
                  />
                  <TextField
                    label="Parallel Scenarios"
                    placeholder="3"
                    variant="standard"
                    autoComplete="off"
                    value={parallelScenarios}
                    onChange={(e) => setParallelScenarios(e.target.value)}
                    type="number"
                    required
                    slotProps={{ htmlInput: { min: 1 } }}
                    sx={{ width: 160 }}
                  />
                  <TextField
                    label="Timeout (s)"
                    placeholder="300"
                    variant="standard"
                    autoComplete="off"
                    value={timeout}
                    onChange={(e) => setTimeout(e.target.value)}
                    type="number"
                    required
                    slotProps={{ htmlInput: { min: 1 } }}
                    sx={{ width: 130 }}
                  />
                  <TextField
                    label="Pause Between Runs (s)"
                    placeholder="1.0"
                    variant="standard"
                    autoComplete="off"
                    value={pauseBetweenRuns}
                    onChange={(e) => setPauseBetweenRuns(e.target.value)}
                    type="number"
                    required
                    slotProps={{ htmlInput: { step: 0.5, min: 0 } }}
                    sx={{ width: 180 }}
                  />
                  <FormControl variant="standard" required sx={{ width: 160 }}>
                    <InputLabel>Strategy</InputLabel>
                    <Select
                      value={strategy}
                      label="Strategy"
                      onChange={(e) => setStrategy(e.target.value)}
                    >
                      <MenuItem value="coverage">coverage</MenuItem>
                      <MenuItem value="random">random</MenuItem>
                      <MenuItem value="sequential">sequential</MenuItem>
                    </Select>
                  </FormControl>
                </Stack>

                <Divider />

                <Typography variant="subtitle2" color="text.secondary">
                  Emulation
                </Typography>
                <Stack direction="row" sx={{ gap: "16px" }}>
                  <FormControl variant="standard" required sx={{ width: 150 }}>
                    <InputLabel>Infra LLM</InputLabel>
                    <Select
                      value={infraLlm}
                      label="Infra LLM"
                      onChange={(e) =>
                        setInfraLlm(e.target.value as InfraLlmMode)
                      }
                    >
                      <MenuItem value="live">live</MenuItem>
                      <MenuItem value="mock">mock</MenuItem>
                      <MenuItem value="replay">replay</MenuItem>
                    </Select>
                  </FormControl>
                  <FormControl variant="standard" required sx={{ width: 150 }}>
                    <InputLabel>Infra Tools</InputLabel>
                    <Select
                      value={infraTools}
                      label="Infra Tools"
                      onChange={(e) =>
                        setInfraTools(e.target.value as InfraToolsMode)
                      }
                    >
                      <MenuItem value="live">live</MenuItem>
                      <MenuItem value="mock">mock</MenuItem>
                      <MenuItem value="stub">stub</MenuItem>
                    </Select>
                  </FormControl>
                  <FormControl variant="standard" required sx={{ width: 200 }}>
                    <InputLabel>Runtime Cache</InputLabel>
                    <Select
                      value={runtimeCache}
                      label="Runtime Cache"
                      onChange={(e) =>
                        setRuntimeCache(e.target.value as RuntimeCacheMode)
                      }
                    >
                      <MenuItem value="content-addressed">
                        content-addressed
                      </MenuItem>
                      <MenuItem value="disabled">disabled</MenuItem>
                      <MenuItem value="forced">forced</MenuItem>
                    </Select>
                  </FormControl>
                </Stack>

                <Divider />

                <Typography variant="subtitle2" color="text.secondary">
                  Pipeline hooks
                </Typography>

                <HookSection
                  level="application"
                  selections={hooks.application}
                  options={pipelinePickerOptions}
                  onChange={(phase, val) =>
                    updateHook("application", phase, val)
                  }
                />

                <Button
                  onClick={() => setAdvancedHooksOpen((o) => !o)}
                  sx={{ alignSelf: "flex-start" }}
                  endIcon={
                    <ExpandMoreIcon
                      sx={{
                        transition: "transform 0.2s",
                        transform: advancedHooksOpen
                          ? "rotate(180deg)"
                          : "none",
                      }}
                    />
                  }
                >
                  Advanced (scenario, test, run)
                </Button>
                <Collapse in={advancedHooksOpen}>
                  <Stack sx={{ gap: "16px" }}>
                    {(["scenario", "test", "run"] as const).map((level) => (
                      <HookSection
                        key={level}
                        level={level}
                        selections={hooks[level]}
                        options={pipelinePickerOptions}
                        onChange={(phase, val) => updateHook(level, phase, val)}
                      />
                    ))}
                  </Stack>
                </Collapse>
              </Stack>
            </TabPanel>

            <TabPanel
              value={selectedTab}
              index={1}
              sx={{ flex: 1, overflow: "auto" }}
            >
              <Box sx={{ mt: "8px" }}>
                <CodeBlock code={yamlPreview} language="yaml" />
              </Box>
            </TabPanel>
          </>
        )}
      </Stack>
    </PageWithTitle>
  );
};

export default CreateExperiment;
