//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { describe, expect, it } from "vitest";
import { parse as parseYaml } from "yaml";
import { editorContentToYaml, yamlToEditorContent } from "./datasets";

const YAML = `apiVersion: lab/v1
kind: Dataset
metadata:
  name: x
  labels: {a: b}
spec:
  defaults: {k: 1}
  items:
    - id: a
      inputs:
        user: hello
        hitl: [ok]
      expectations:
        ground_truth: "2"
        details: {shows: keep}
    - id: b
      inputs:
        user:
          - role: user
            content: old
    - id: c
      inputs:
        user: [one, two]
    - id: d
      inputs:
        user: {ref: f.yaml}
`;

describe("yamlToEditorContent", () => {
  it("reads string, role/content, list and ref forms of inputs.user", () => {
    const { items } = yamlToEditorContent(YAML);
    expect(items.map((i) => i.prompt)).toEqual(["hello", "old", "one\ntwo", ""]);
  });
});

describe("editorContentToYaml", () => {
  it("preserves fields the editor does not model when nothing changed", () => {
    const out = parseYaml(editorContentToYaml(yamlToEditorContent(YAML), "x", "", YAML));
    expect(out.metadata.labels).toEqual({ a: "b" });
    expect(out.spec.defaults).toEqual({ k: 1 });
    expect(out.spec.items[0].inputs.hitl).toEqual(["ok"]);
    expect(out.spec.items[0].expectations.details.shows).toBe("keep");
    expect(out.spec.items[2].inputs.user).toEqual(["one", "two"]);
    expect(out.spec.items[3].inputs.user).toEqual({ ref: "f.yaml" });
  });

  it("applies edits as plain strings and removes cleared fields", () => {
    const editor = yamlToEditorContent(YAML);
    editor.items[0].prompt = "new";
    editor.items[0].ground_truth = "";
    editor.items[1].prompt = "edited";
    const out = parseYaml(editorContentToYaml(editor, "x", "", YAML));
    expect(out.spec.items[0].inputs).toEqual({ user: "new", hitl: ["ok"] });
    expect(out.spec.items[0].expectations).toEqual({ details: { shows: "keep" } });
    expect(out.spec.items[1].inputs.user).toBe("edited");
  });

  it("writes a new dataset with plain-string user input", () => {
    const out = parseYaml(
      editorContentToYaml({ items: [{ id: 1, prompt: "p", ground_truth: "g" }] }, "n", "d"),
    );
    expect(out.metadata).toEqual({ name: "n", description: "d" });
    expect(out.spec.items[0]).toEqual({
      id: 1,
      inputs: { user: "p" },
      expectations: { ground_truth: "g" },
    });
  });
});
