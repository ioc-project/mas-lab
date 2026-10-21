//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/** Compatibility wrappers over the scope/item/run API. */
import { useMutation, useQuery } from "@tanstack/react-query";

import { API_BASE_URL, ApiError, apiJson } from "@/api/client";
import { fetchChoices } from "@/api/choices";
import {
  createItem,
  deleteItem,
  fetchItem,
  fetchItems,
  updateItem,
  validateInScope,
} from "@/api/items";
import { fetchArtifactBlob, fetchRunArtifacts, fetchArtifactText, type ArtifactNode } from "@/api/artifacts";
import { deleteRun, fetchRun, fetchRuns, startRun } from "@/api/runs";
import { toScopeId } from "@/api/scope";
import { fetchScopeConfig, fetchScopes, pickDefaultScope } from "@/api/scopes";
import { uploadFile } from "@/api/uploads";

export { API_BASE_URL, ApiError };

export interface Library {
  dir: string;
  name: string;
  description: string;
}

export function pickLibrary(libraries: Library[] | undefined): string {
  if (!libraries?.length) return "";
  const scopes = libraries.map((lib) => ({
    id: toScopeId(lib.dir),
    name: lib.dir,
    kind: "library" as const,
    title: lib.name,
    description: lib.description,
    origin: "workspace",
    writable: true,
    kinds: [],
  }));
  return pickDefaultScope(scopes);
}

export function useLibraries() {
  return useQuery({
    queryKey: ["libraries"],
    queryFn: async (): Promise<Library[]> => {
      const scopes = await fetchScopes();
      return scopes.map((s) => ({
        dir: s.id,
        name: s.title || s.name,
        description: s.description,
      }));
    },
  });
}

export interface ValidateRequest {
  library: string;
  manifest_yaml?: string;
  content?: string;
}

async function validateManifest(request: ValidateRequest) {
  const manifest = request.manifest_yaml ?? request.content ?? "";
  const result = await validateInScope(toScopeId(request.library), { manifest });
  return {
    ...result,
    stdout: result.valid ? "ok" : "",
    message: result.valid ? "valid" : result.stderr,
    status: result.valid ? "ok" : "error",
  };
}

export function useValidateAgent() {
  return useMutation({ mutationFn: validateManifest });
}

export function useValidateMas() {
  return useMutation({ mutationFn: validateManifest });
}

export interface ToolOption {
  name: string;
  description: string;
}

export function useTools(library: string, _namespaces: string[] = ["global"]) {
  return useQuery({
    queryKey: ["tools", library],
    queryFn: async (): Promise<ToolOption[]> => {
      const data = await fetchChoices("tools", toScopeId(library));
      const tools = (data.tools as Array<{ ref?: string; id: string; title?: string; summary?: string }>) ?? [];
      return tools.map((t) => ({
        name: t.ref || t.id,
        description: t.summary || t.title || t.id,
      }));
    },
    enabled: !!library,
  });
}

export interface SkillOption {
  name: string;
  description: string;
}

export function useSkills(library: string, _namespaces: string[] = ["global"]) {
  return useQuery({
    queryKey: ["skills", library],
    queryFn: async (): Promise<SkillOption[]> => {
      const data = await fetchChoices("skills", toScopeId(library));
      const skills = (data.skills as Array<{ ref?: string; id: string; title?: string; summary?: string }>) ?? [];
      return skills.map((t) => ({
        name: t.ref || t.id,
        description: t.summary || t.title || t.id,
      }));
    },
    enabled: !!library,
  });
}

export interface RunAgentRequest {
  library: string;
  manifest_yaml: string;
  query: string;
  flavour?: string;
  session_id?: string;
  verbose?: boolean;
  timeout?: number;
}

export interface JobSubmitResponse {
  job_id: string;
  status: string;
  command: string;
}

export interface JobResponse {
  id: string;
  endpoint: string;
  command: string;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "cancelled"
    | "timeout"
    | "interrupted";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pid: number | null;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  error: string | null;
  response?: string;
  error_message?: string;
  error_detail?: string;
  session_id?: string;
}

function runToJob(run: {
  id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  exit_code?: number | null;
  job_id?: string | null;
  log?: string | null;
}): JobResponse {
  return {
    id: run.id,
    endpoint: "/api/runs",
    command: "run",
    status: (run.status === "interrupted" ? "failed" : run.status) as JobResponse["status"] || "pending",
    created_at: run.started_at || "",
    started_at: run.started_at ?? null,
    finished_at: run.finished_at ?? null,
    pid: null,
    exit_code: run.exit_code ?? null,
    stdout: run.log ?? "",
    stderr: "",
    error: null,
  };
}

export async function runAgent(request: RunAgentRequest): Promise<{ job_id: string; status: string; command: string; session_id?: string }> {
  const started = await startRun({
    scope: toScopeId(request.library),
    kind: "application",
    item: "inline",
    query: request.query,
    manifest: request.manifest_yaml,
  });
  return { job_id: started.id, status: started.status, command: "run", session_id: request.session_id };
}

export interface JobSummary {
  id: string;
  endpoint: string;
  command: string;
  status: JobResponse["status"];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pid: number | null;
  exit_code: number | null;
}

export interface JobDetail extends JobSummary {
  stdout: string;
  stderr: string;
  error: string | null;
  request_body?: Record<string, unknown>;
}

export async function fetchJobs(_status?: string): Promise<JobSummary[]> {
  const runs = await fetchRuns();
  return runs.map((r) => runToJob(r));
}

export async function fetchJobDetail(jobId: string): Promise<JobDetail> {
  return runToJob(await fetchRun(jobId));
}

export async function pollJob(jobId: string): Promise<JobResponse> {
  return runToJob(await fetchRun(jobId));
}

export interface RunMasRequest {
  library: string;
  manifest_yaml?: string;
  item?: string;
  query: string;
  overlays?: string[];
  flavour?: string;
  verbose?: boolean;
  timeout?: number;
}

export async function runMas(
  request: RunMasRequest,
): Promise<{ job_id: string; status: string; command: string }> {
  const started = await startRun({
    scope: toScopeId(request.library),
    kind: "application",
    item: request.item ?? "inline",
    query: request.query,
    overlays: request.overlays,
    manifest: request.item ? undefined : request.manifest_yaml,
  });
  return { job_id: started.id, status: started.status, command: "run" };
}

export interface BenchmarkRunRequest {
  library: string;
  experiment_yaml: string;
  progress?: boolean;
  n_runs?: number;
  timeout?: number;
}

export async function runBenchmark(
  request: BenchmarkRunRequest,
): Promise<{ job_id: string; status: string; command: string }> {
  const started = await startRun({
    scope: toScopeId(request.library),
    kind: "experiment",
    item: "inline",
    manifest: request.experiment_yaml,
  });
  return { job_id: started.id, status: started.status, command: "run" };
}

export interface MasResourceEntry {
  mas_name: string;
  mas_yaml: string;
  agents?: Record<string, string>;
}

export interface MasResourceCreateRequest {
  mas_name: string;
  mas_yaml: string;
  agents: Record<string, string>;
}

export interface MasResourceUpdateRequest {
  mas_yaml: string;
  agents: Record<string, string>;
}

export interface MasResourceCreateResponse {
  mas_name: string;
}

export interface MasResourceDetail {
  mas_name: string;
  mas_yaml: string;
  agents: Record<string, string>;
}

export async function fetchMasResources(
  library: string,
): Promise<Record<string, MasResourceEntry>> {
  const groups = await fetchItems(toScopeId(library), "application");
  const result: Record<string, MasResourceEntry> = {};
  for (const group of groups) {
    const itemId = group.latest ? `${group.name}@${group.latest}` : group.items[0]?.id || group.name;
    const detail = await fetchItem(toScopeId(library), "application", itemId);
    result[detail.id] = {
      mas_name: detail.id,
      mas_yaml: detail.manifest,
      agents: detail.children,
    };
  }
  return result;
}

export function useMasResources(library: string) {
  return useQuery({
    queryKey: ["apps", library],
    queryFn: () => fetchMasResources(library),
    enabled: !!library,
  });
}

export async function fetchMasResourceDetail(
  library: string,
  masName: string,
): Promise<MasResourceDetail> {
  const detail = await fetchItem(toScopeId(library), "application", masName);
  return { mas_name: detail.id, mas_yaml: detail.manifest, agents: detail.children };
}

export function useMasResourceDetail(library: string, masName: string) {
  return useQuery({
    queryKey: ["apps", library, masName],
    queryFn: () => fetchMasResourceDetail(library, masName),
    enabled: !!library && !!masName,
  });
}

export async function createMasResource(
  request: MasResourceCreateRequest & { library: string },
): Promise<MasResourceCreateResponse> {
  const created = await createItem(toScopeId(request.library), {
    kind: "application",
    name: request.mas_name,
  });
  await updateItem(toScopeId(request.library), "application", created.id, {
    manifest: request.mas_yaml,
    children: request.agents,
    revision: created.revision,
  });
  return { mas_name: created.id };
}

export async function updateMasResource(
  request: MasResourceUpdateRequest & {
    library: string;
    mas_name: string;
    old_mas_name?: string;
  },
): Promise<void> {
  const itemId = request.old_mas_name || request.mas_name;
  const current = await fetchItem(toScopeId(request.library), "application", itemId);
  await updateItem(toScopeId(request.library), "application", current.id, {
    manifest: request.mas_yaml,
    children: request.agents,
    revision: current.revision,
  });
}

export async function deleteMasResource(library: string, masName: string): Promise<void> {
  await deleteItem(toScopeId(library), "application", masName);
}

export interface ScenarioEntry {
  name: string;
  description?: string;
}

export function useScenarios(library: string) {
  return useQuery({
    queryKey: ["scenarios", library],
    queryFn: async (): Promise<ScenarioEntry[]> => {
      const groups = await fetchItems(toScopeId(library), "overlay");
      return groups.map((g) => ({ name: g.name, description: g.summary }));
    },
    enabled: !!library,
  });
}

export interface DatasetEntry {
  name: string;
  path: string;
  description?: string;
}

export function useDatasets(library: string) {
  return useQuery({
    queryKey: ["datasets", library],
    queryFn: async (): Promise<DatasetEntry[]> => {
      const groups = await fetchItems(toScopeId(library), "dataset");
      return groups.map((g) => ({
        name: g.name,
        path: g.items[0]?.ref || g.name,
        description: g.summary,
      }));
    },
    enabled: !!library,
  });
}

export interface ExperimentSummary {
  name: string;
  description?: string;
  library?: string;
  n_runs?: number;
  version?: string;
  scenarios?: string[];
  dataset?: string;
}

export function useAllExperiments() {
  return useQuery({
    queryKey: ["experiments", "all"],
    queryFn: async (): Promise<ExperimentSummary[]> => {
      const scopes = await fetchScopes();
      const out: ExperimentSummary[] = [];
      for (const scope of scopes) {
        if (!scope.kinds.includes("experiment")) continue;
        const groups = await fetchItems(scope.id, "experiment");
        for (const g of groups) {
          out.push({ name: g.name, description: g.summary, library: scope.id });
        }
      }
      return out;
    },
  });
}

export function useExperiments(library: string) {
  return useQuery({
    queryKey: ["experiments", library],
    queryFn: async (): Promise<ExperimentSummary[]> => {
      const groups = await fetchItems(toScopeId(library), "experiment");
      return groups.map((g) => ({ name: g.name, description: g.summary, library }));
    },
    enabled: !!library,
  });
}

export interface ExperimentContentResponse {
  name: string;
  content: string;
}

export async function fetchExperimentContent(
  library: string,
  name: string,
): Promise<ExperimentContentResponse> {
  const detail = await fetchItem(toScopeId(library), "experiment", name);
  return { name: detail.id, content: detail.manifest };
}

export function useExperimentContent(library: string, name: string) {
  return useQuery({
    queryKey: ["experiments", library, name],
    queryFn: () => fetchExperimentContent(library, name),
    enabled: !!library && !!name,
  });
}

export async function createExperiment(
  library: string,
  nameOrPayload: string | { name: string; content: string },
  content?: string,
): Promise<void> {
  const name = typeof nameOrPayload === "string" ? nameOrPayload : nameOrPayload.name;
  const yaml = typeof nameOrPayload === "string" ? content ?? "" : nameOrPayload.content;
  const created = await createItem(toScopeId(library), { kind: "experiment", name });
  await updateItem(toScopeId(library), "experiment", created.id, {
    manifest: yaml,
    revision: created.revision,
  });
}

export async function updateExperimentApi(
  library: string,
  name: string,
  contentOrPayload: string | { name?: string; content: string },
): Promise<void> {
  const yaml = typeof contentOrPayload === "string" ? contentOrPayload : contentOrPayload.content;
  const current = await fetchItem(toScopeId(library), "experiment", name);
  await updateItem(toScopeId(library), "experiment", current.id, {
    manifest: yaml,
    revision: current.revision,
  });
}

export async function deleteExperimentApi(library: string, name: string): Promise<void> {
  await deleteItem(toScopeId(library), "experiment", name);
}

export async function deleteExperimentCache(_library: string, name: string): Promise<void> {
  const runs = await fetchRuns();
  await Promise.all(runs.filter((r) => r.item === name).map((r) => deleteRun(r.id)));
}

export interface FileTreeEntry {
  name: string;
  type: "file" | "dir" | "directory";
  children?: FileTreeEntry[];
  size?: number;
  artifact_id?: string;
}

export interface ExperimentDetail {
  name: string;
  tree?: FileTreeEntry[];
  metadata?: Record<string, unknown>;
}

function artifactToEntry(node: ArtifactNode): FileTreeEntry {
  const isDir =
    node.kind === "dir" ||
    node.kind === "directory" ||
    Boolean(node.children?.length);
  return {
    name: node.name,
    type: isDir ? "directory" : "file",
    size: node.size,
    artifact_id: node.artifact_id,
    children: node.children?.map(artifactToEntry),
  };
}

export function findArtifactId(tree: FileTreeEntry[] | undefined, path: string): string | undefined {
  if (!tree || !path) return undefined;
  const parts = path.split("/").filter(Boolean);
  let nodes = tree;
  let found: FileTreeEntry | undefined;
  for (const part of parts) {
    found = nodes.find((n) => n.name === part);
    if (!found) return undefined;
    nodes = found.children ?? [];
  }
  return found?.artifact_id;
}

export function useExperimentDetail(experimentName: string) {
  const query = useQuery({
    queryKey: ["experiment", experimentName],
    queryFn: async (): Promise<ExperimentDetail> => {
      const runs = (await fetchRuns()).filter((r) => r.item === experimentName);
      const children = await Promise.all(
        runs.map(async (run) => {
          const artifacts = await fetchRunArtifacts(run.id);
          return {
            name: run.id,
            type: "directory" as const,
            children: artifacts.map(artifactToEntry),
          };
        }),
      );
      const latest = runs[0];
      return {
        name: experimentName,
        tree: children,
        metadata: latest
          ? {
              status: latest.status,
              run_id: latest.id,
              started_at: latest.started_at,
              finished_at: latest.finished_at,
            }
          : {},
      };
    },
    enabled: !!experimentName,
    refetchInterval: (q) => {
      const status = q.state.data?.metadata?.status;
      const s = typeof status === "string" ? status.toLowerCase() : "";
      return s === "running" || s === "pending" || s === "in_progress" ? 1500 : 3000;
    },
  });

  const isRunning =
    typeof query.data?.metadata?.status === "string" &&
    ["running", "pending", "in_progress"].includes(
      String(query.data.metadata.status).toLowerCase(),
    );

  return { ...query, isRunning };
}

export interface ExperimentFileResponse {
  content: string;
  media_type?: string;
}

export async function fetchExperimentFile(
  _experimentName: string,
  artifactId: string,
): Promise<ExperimentFileResponse> {
  return { content: await fetchArtifactText(artifactId) };
}

export interface PipelineStepTypeConfigField {
  type?: string;
  default?: unknown;
  description?: string;
  required?: boolean;
  enum?: string[];
  [key: string]: unknown;
}

export interface PipelineStepTypeEntry {
  type: string;
  phase?: string;
  description?: string;
  category?: string;
  label?: string;
  config?: Record<string, PipelineStepTypeConfigField>;
}

export interface PipelineStepTypesResponse {
  step_types?: PipelineStepTypeEntry[];
  categories?: Array<{ id: string; label?: string }>;
}

export function usePipelineStepTypes() {
  return useQuery({
    queryKey: ["choices", "step-types"],
    queryFn: async (): Promise<PipelineStepTypesResponse> => {
      const data = await fetchChoices("step-types");
      return {
        step_types: (data.step_types as PipelineStepTypeEntry[]) ?? [],
        categories: (data.categories as Array<{ id: string; label?: string }>) ?? [],
      };
    },
  });
}

export interface PipelineStepSummary {
  name: string;
  type: string;
}

export interface PipelineSummary {
  name: string;
  description?: string;
  filename?: string;
  ref?: string;
  experiment?: string;
  steps?: PipelineStepSummary[];
}

export function usePipelines(library: string) {
  return useQuery({
    queryKey: ["pipelines", library],
    queryFn: async (): Promise<PipelineSummary[]> => {
      const groups = await fetchItems(toScopeId(library), "pipeline");
      return groups.map((g) => ({
        name: g.name,
        description: g.summary,
        filename: g.name,
        ref: g.items[0]?.ref || g.name,
      }));
    },
    enabled: !!library,
  });
}

export interface PipelineDetail {
  name: string;
  content: string;
}

export async function fetchPipelineDetail(library: string, name: string): Promise<PipelineDetail> {
  const detail = await fetchItem(toScopeId(library), "pipeline", name);
  return { name: detail.id, content: detail.manifest };
}

export function usePipelineDetail(library: string, name: string) {
  return useQuery({
    queryKey: ["pipelines", library, name],
    queryFn: () => fetchPipelineDetail(library, name),
    enabled: !!library && !!name,
  });
}

export async function createPipeline(
  library: string,
  nameOrSpec: string | { name: string; content: string },
  content?: string,
): Promise<void> {
  const name = typeof nameOrSpec === "string" ? nameOrSpec : nameOrSpec.name;
  const yaml = typeof nameOrSpec === "string" ? content ?? "" : nameOrSpec.content;
  const created = await createItem(toScopeId(library), { kind: "pipeline", name });
  await updateItem(toScopeId(library), "pipeline", created.id, {
    manifest: yaml,
    revision: created.revision,
  });
}

export async function updatePipeline(
  library: string,
  name: string,
  contentOrSpec: string | { name?: string; content: string },
): Promise<void> {
  const yaml = typeof contentOrSpec === "string" ? contentOrSpec : contentOrSpec.content;
  const current = await fetchItem(toScopeId(library), "pipeline", name);
  await updateItem(toScopeId(library), "pipeline", current.id, {
    manifest: yaml,
    revision: current.revision,
  });
}

export async function deletePipeline(library: string, name: string): Promise<void> {
  await deleteItem(toScopeId(library), "pipeline", name);
}

export function useValidateOverlay() {
  return useMutation({ mutationFn: validateManifest });
}

export interface OverlayEntry {
  name: string;
  description?: string;
  namespace?: string;
  path?: string;
  ref?: string;
  ui_canvas?: boolean | string;
}

export async function fetchOverlays(library: string): Promise<OverlayEntry[]> {
  const groups = await fetchItems(toScopeId(library), "overlay");
  return groups.map((g) => {
    const ref = g.items[0]?.ref || g.name;
    return {
      name: g.name,
      description: g.summary,
      path: ref,
      ref,
      ui_canvas: g.ui_canvas,
    };
  });
}

export function useOverlays(library: string) {
  return useQuery({
    queryKey: ["overlays", library],
    queryFn: () => fetchOverlays(library),
    enabled: !!library,
  });
}

export interface OverlayDetail {
  name: string;
  content: string;
}

export async function fetchOverlay(library: string, name: string): Promise<OverlayDetail> {
  const detail = await fetchItem(toScopeId(library), "overlay", name);
  return { name: detail.id, content: detail.manifest };
}

export function useOverlay(library: string, name: string) {
  return useQuery({
    queryKey: ["overlays", library, name],
    queryFn: () => fetchOverlay(library, name),
    enabled: !!library && !!name,
  });
}

export async function createOverlay(
  library: string,
  nameOrSpec: string | { name: string; content: string; run_validation?: boolean },
  content?: string,
): Promise<void> {
  const name = typeof nameOrSpec === "string" ? nameOrSpec : nameOrSpec.name;
  const yaml = typeof nameOrSpec === "string" ? content ?? "" : nameOrSpec.content;
  const created = await createItem(toScopeId(library), { kind: "overlay", name });
  await updateItem(toScopeId(library), "overlay", created.id, {
    manifest: yaml,
    revision: created.revision,
  });
}

export async function updateOverlay(
  library: string,
  name: string,
  contentOrSpec: string | { name?: string; content: string; run_validation?: boolean },
): Promise<void> {
  const yaml = typeof contentOrSpec === "string" ? contentOrSpec : contentOrSpec.content;
  const current = await fetchItem(toScopeId(library), "overlay", name);
  await updateItem(toScopeId(library), "overlay", current.id, {
    manifest: yaml,
    revision: current.revision,
  });
}

export async function deleteOverlayApi(library: string, name: string): Promise<void> {
  await deleteItem(toScopeId(library), "overlay", name);
}

export function useValidatePipeline() {
  return useMutation({ mutationFn: validateManifest });
}

export async function runPipeline(
  library: string,
  name: string,
): Promise<{ job_id: string; status: string; command: string }> {
  const started = await startRun({
    scope: toScopeId(library),
    kind: "pipeline",
    item: name,
  });
  return { job_id: started.id, status: started.status, command: "run" };
}

export function useEvalMetrics() {
  return useQuery({
    queryKey: ["metrics", "eval"],
    queryFn: () => apiJson<Record<string, string>>("/api/metrics/eval"),
  });
}

export function useMceMetrics() {
  return useQuery({
    queryKey: ["metrics", "mce"],
    queryFn: () => apiJson<Record<string, string>>("/api/metrics/mce"),
  });
}

export interface DatasetSummary {
  name: string;
  description?: string;
  path?: string;
}

export interface DatasetDetail {
  name: string;
  content: string;
}

export async function fetchDatasetsList(library: string): Promise<DatasetSummary[]> {
  const groups = await fetchItems(toScopeId(library), "dataset");
  return groups.map((g) => ({ name: g.name, description: g.summary }));
}

export function useDatasetsList(library: string) {
  return useQuery({
    queryKey: ["datasets-list", library],
    queryFn: () => fetchDatasetsList(library),
    enabled: !!library,
  });
}

export async function fetchDatasetDetail(library: string, name: string): Promise<DatasetDetail> {
  const detail = await fetchItem(toScopeId(library), "dataset", name);
  return { name: detail.id, content: detail.manifest };
}

export function useDatasetDetail(library: string, name: string) {
  return useQuery({
    queryKey: ["datasets", library, name],
    queryFn: () => fetchDatasetDetail(library, name),
    enabled: !!library && !!name,
  });
}

export async function deleteDataset(library: string, name: string): Promise<void> {
  await deleteItem(toScopeId(library), "dataset", name);
}

export interface UpdateDatasetPayload {
  content: string;
  name?: string;
}

export async function updateDataset(
  library: string,
  currentName: string,
  payload: UpdateDatasetPayload,
): Promise<void> {
  const current = await fetchItem(toScopeId(library), "dataset", currentName);
  await updateItem(toScopeId(library), "dataset", current.id, {
    manifest: payload.content,
    revision: current.revision,
  });
}

export function useUpdateDataset(library: string, currentName: string) {
  return useMutation({
    mutationFn: (payload: UpdateDatasetPayload) => updateDataset(library, currentName, payload),
  });
}

export async function createDatasetApi(
  library: string,
  name: string,
  content: string,
): Promise<void> {
  const created = await createItem(toScopeId(library), { kind: "dataset", name });
  await updateItem(toScopeId(library), "dataset", created.id, {
    manifest: content,
    revision: created.revision,
  });
}

export function useCreateDataset(library: string) {
  return useMutation({
    mutationFn: ({ name, content }: { name: string; content: string }) =>
      createDatasetApi(library, name, content),
  });
}

export function useDeleteDataset(library: string, name: string) {
  return useMutation({
    mutationFn: () => deleteDataset(library, name),
  });
}

export type ConfigFiles = Record<string, Record<string, string>>;

export function useConfigFiles(library: string) {
  return useQuery({
    queryKey: ["config-files", library],
    queryFn: (): Promise<ConfigFiles> => fetchScopeConfig(toScopeId(library)),
    enabled: !!library,
  });
}

export interface RuntimeRunner {
  id: string;
  name?: string;
  description?: string;
}

export function useRuntimeRunners() {
  return useQuery({
    queryKey: ["choices", "runners"],
    queryFn: async () => {
      const data = await fetchChoices("runners");
      return (data.runners as RuntimeRunner[]) ?? [];
    },
  });
}

export async function analyzeBenchmark(
  library: string,
  name: string | { benchmark_id: string },
): Promise<JobSubmitResponse> {
  const item = typeof name === "string" ? name : name.benchmark_id;
  const started = await startRun({
    scope: toScopeId(library),
    kind: "experiment",
    item,
    action: "analyze",
  });
  return { job_id: started.id, status: started.status, command: "analyze" };
}

export interface BenchmarkExportRequest {
  library: string;
  experiment: string;
}

async function waitForRun(id: string) {
  let run = await fetchRun(id);
  while (run.status === "pending" || run.status === "running") {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    run = await fetchRun(id);
  }
  return run;
}

function firstArtifactId(nodes: ArtifactNode[]): string | undefined {
  for (const node of nodes) {
    if (node.artifact_id && node.kind !== "directory") return node.artifact_id;
    const nested = node.children ? firstArtifactId(node.children) : undefined;
    if (nested) return nested;
  }
  return undefined;
}

export async function exportBenchmark(request: BenchmarkExportRequest): Promise<Blob> {
  const started = await startRun({
    scope: toScopeId(request.library),
    kind: "experiment",
    item: request.experiment,
    action: "export",
  });
  const run = await waitForRun(started.id);
  if (run.status !== "completed") {
    throw new Error(run.log || `export ${run.status}`);
  }
  const arts = await fetchRunArtifacts(started.id);
  const artifactId = firstArtifactId(arts);
  if (!artifactId) {
    throw new Error("export produced no downloadable artifact");
  }
  return fetchArtifactBlob(artifactId);
}

export async function downloadBenchmarkExport(library: string, name: string): Promise<void> {
  const started = await startRun({
    scope: toScopeId(library),
    kind: "experiment",
    item: name,
    action: "export",
  });
  const run = await waitForRun(started.id);
  if (run.status !== "completed") {
    throw new Error(run.log || `export ${run.status}`);
  }
  const arts = await fetchRunArtifacts(started.id);
  const artifactId = firstArtifactId(arts);
  if (!artifactId) {
    throw new Error("export produced no downloadable artifact");
  }
  const blob = await fetchArtifactBlob(artifactId);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}.tar.gz`;
  link.click();
  URL.revokeObjectURL(url);
}

export interface BenchmarkImportRequest {
  library: string;
  upload_id: string;
}

export async function importBenchmark(request: BenchmarkImportRequest): Promise<void> {
  const started = await startRun({
    scope: toScopeId(request.library),
    kind: "experiment",
    item: request.upload_id,
    action: "import",
  });
  const run = await waitForRun(started.id);
  if (run.status !== "completed") {
    throw new Error(run.log || `import ${run.status}`);
  }
}

export async function uploadImportBenchmark(
  libraryOrFile: string | File,
  file?: File,
): Promise<JobSubmitResponse> {
  const payload = file ?? (libraryOrFile instanceof File ? libraryOrFile : undefined);
  const library = typeof libraryOrFile === "string" ? libraryOrFile : "";
  if (!payload) {
    throw new Error("no file to import");
  }
  const uploaded = await uploadFile(payload);
  const started = await startRun({
    scope: toScopeId(library),
    kind: "experiment",
    item: uploaded.upload_id,
    action: "import",
  });
  return { job_id: started.id, status: started.status, command: "import" };
}
