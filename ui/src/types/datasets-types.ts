//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0

/** A deprecated `{role, content}` message in `inputs.user`. */
export type UserMessage = { role?: string; content?: unknown };

/**
 * `inputs.user` per the schema: a plain string (preferred), a list of strings,
 * or the deprecated `{role, content}` message(s). `{ref: ...}` objects are file
 * references the UI cannot resolve.
 */
export type UserInput = string | UserMessage | Array<string | UserMessage>;

/** A dataset item as it appears in the YAML file. */
export interface YamlDatasetItem {
  id: string | number;
  inputs?: { user?: UserInput };
  expectations?: { ground_truth?: string };
  group?: string;
  target_agents?: string[];
  category?: string;
  tags?: string[];
}

/** An untyped YAML mapping whose unmodeled keys are carried through on save. */
export type YamlRecord = Record<string, unknown>;
