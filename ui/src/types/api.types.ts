//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

// --- Libraries ---

export interface Library {
  dir: string;
  name: string;
  description: string;
}

export interface ValidateRequest {
  library: string;
  manifest_yaml: string;
}

// --- Tools ---

export interface ToolOption {
  name: string;
  description: string;
}

// --- Skills ---

export interface SkillOption {
  name: string;
  description: string;
}

// --- Run Agent ---

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

export interface JobSummary {
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
}

export interface JobDetail extends JobSummary {
  stdout: string;
  stderr: string;
  error: string | null;
  request_body?: Record<string, unknown>;
}

// --- Run MAS ---

export interface RunMasRequest {
  library: string;
  manifest_yaml: string;
  query: string;
  overlays?: string[];
  flavour?: string;
  verbose?: boolean;
  timeout?: number;
}

// --- Benchmark ---

export interface BenchmarkRunRequest {
  library: string;
  experiment_yaml: string;
  progress?: boolean;
  n_runs?: number;
  timeout?: number;
}

export interface BenchmarkExportRequest {
  benchmark_id: string;
  output?: string;
  include_trace_cache?: boolean;
  dry_run?: boolean;
  timeout?: number;
}

export interface BenchmarkImportRequest {
  tarball: string;
  output_dir?: string;
  trace_cache_dir?: string;
  dry_run?: boolean;
  timeout?: number;
}

// --- MAS Resources ---

export interface MasResourceEntry {
  mas_yaml: string;
  agents: Record<string, string>;
}

export interface MasResourceCreateRequest {
  library: string;
  mas_name: string;
  mas_yaml: string;
  agents: Record<string, string>;
}

export interface MasResourceUpdateRequest {
  library: string;
  old_mas_name: string;
  mas_name: string;
  mas_yaml: string;
  agents: Record<string, string>;
}

export interface MasResourceCreateResponse {
  mas_name: string;
  path: string;
  files: string[];
}

export interface MasResourceDetail {
  mas_name: string;
  mas_yaml: string;
  agents: Record<string, string>;
}

// --- Scenarios ---

export interface ScenarioEntry {
  name: string;
  path: string;
}

// --- Datasets ---

export interface DatasetEntry {
  name: string;
  path: string;
}

export interface DatasetSummary {
  name: string;
  path: string;
  description: string;
}

export interface DatasetDetail {
  name: string;
  content: string;
}

export interface UpdateDatasetPayload {
  name: string;
  content: string;
}

// --- Experiments ---

export interface ExperimentSummary {
  name: string;
  description: string;
  version: string;
  scenarios: string[];
  dataset: string;
  library?: string;
  path?: string;
}

export interface ExperimentContentResponse {
  name: string;
  content: string;
}

export interface FileTreeEntry {
  name: string;
  type: "file" | "directory";
  children?: FileTreeEntry[];
}

export interface ExperimentDetail {
  name: string;
  metadata: Record<string, unknown>;
  tree: FileTreeEntry[];
}

export interface ExperimentFileResponse {
  path: string;
  content: string;
}

// --- Pipeline Step Types ---

export interface PipelineStepTypeConfigField {
  type: string;
  required?: boolean;
  default?: unknown;
  description?: string;
  enum?: string[];
}

export interface PipelineStepTypeEntry {
  type: string;
  label: string;
  description: string;
  phase: string;
  category: string;
  requires?: string;
  config: Record<string, PipelineStepTypeConfigField>;
}

export interface PipelineStepTypesResponse {
  step_types: PipelineStepTypeEntry[];
  categories: { id: string; label: string; color: string }[];
}

// --- Design Patterns ---

export interface DesignPatternEntry {
  type: string;
  label: string;
  description: string;
  /** Source library used to group patterns (e.g. "standard" vs "lab"). */
  library: string;
  config_keys: string[];
}

export interface DesignPatternsResponse {
  patterns: DesignPatternEntry[];
  default: string;
}

// --- Pipelines ---

export interface PipelineStepSummary {
  name: string;
  type: string;
  depends_on: string[];
}

export interface PipelineSummary {
  filename: string;
  name: string;
  description: string;
  steps: PipelineStepSummary[];
  experiment: string;
}

export interface PipelineDetail {
  name: string;
  content: string;
}

// --- Overlays ---

export interface OverlayEntry {
  name: string;
  description?: string;
  namespace?: string;
  path?: string;
}

export interface OverlayDetail {
  name: string;
  content: string;
}

// --- Config Files ---

export type ConfigFiles = Record<string, Record<string, string>>;

// --- Runtime Runners ---

export interface RuntimeRunner {
  id: string;
  label: string;
}
