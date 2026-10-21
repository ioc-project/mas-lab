//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { http, HttpResponse } from "msw";

const API = "http://localhost:8090";

export const handlers = [
  http.get(`${API}/api/health`, () => HttpResponse.json({ status: "ok" })),
  http.get(`${API}/api/info`, () =>
    HttpResponse.json({
      api_version: "0.3.0",
      features: ["scopes", "items", "runs", "edit_targets", "create_scope"],
      auth: "none",
      cors_ok: true,
      writable_default: true,
      edit_targets: [
        {
          id: "library:workspace",
          kind: "library",
          name: "workspace",
          origin: "workspace",
          kinds: ["application", "agent", "overlay", "dataset"],
        },
        {
          id: "library:versioned_lib",
          kind: "library",
          name: "versioned_lib",
          origin: "path",
          kinds: ["application", "agent"],
        },
        {
          id: "lab:lonely",
          kind: "lab",
          name: "lonely",
          origin: "lab",
          kinds: ["experiment"],
        },
      ],
    }),
  ),
  http.get(`${API}/api/scopes`, () =>
    HttpResponse.json({
      scopes: [
        {
          id: "library:workspace",
          kind: "library",
          name: "workspace",
          title: "Workspace",
          description: "",
          origin: "workspace",
          writable: true,
          kinds: ["application", "agent", "overlay", "dataset"],
        },
        {
          id: "library:versioned_lib",
          kind: "library",
          name: "versioned_lib",
          title: "Versioned Lib",
          description: "",
          origin: "path",
          writable: true,
          kinds: ["application", "agent"],
        },
        {
          id: "library:packaged",
          kind: "library",
          name: "packaged",
          title: "Packaged",
          description: "",
          origin: "package",
          writable: false,
          kinds: ["application", "agent"],
        },
        {
          id: "lab:lonely",
          kind: "lab",
          name: "lonely",
          title: "Lonely",
          description: "",
          origin: "lab",
          writable: true,
          kinds: ["experiment"],
        },
      ],
    }),
  ),
  http.post(`${API}/api/scopes`, async ({ request }) => {
    const body = (await request.json()) as { name?: string; description?: string };
    const name = body.name || "fresh";
    return HttpResponse.json(
      {
        id: `lab:${name}`,
        kind: "lab",
        name,
        title: name,
        description: body.description || "",
        origin: "lab",
        writable: true,
        kinds: ["experiment", "pipeline", "overlay", "dataset"],
        libraries: [`library:${name.replace(/-/g, "_")}_lab`],
        counts: { experiment: 0 },
      },
      { status: 201 },
    );
  }),
  http.get(`${API}/api/kinds`, () =>
    HttpResponse.json({
      kinds: [{ id: "application", plural: "applications", creatable: true, versioned: true, children: ["agent"], name_pattern: ".", template_ids: ["blank"], display_fields: [] }],
    }),
  ),
  http.get(`${API}/api/schemas/:id`, () => HttpResponse.json({ type: "object" })),
  http.get(`${API}/api/libraries`, () =>
    HttpResponse.json({ error: "deprecated" }, { status: 410 }),
  ),
  http.get(`${API}/api/scopes/:scopeId/config`, () =>
    HttpResponse.json({ infra: {}, flavours: {}, workspace: {} }),
  ),
  http.get(`${API}/api/scopes/:scopeId`, ({ params }) =>
    HttpResponse.json({
      id: params.scopeId,
      kind: String(params.scopeId).startsWith("lab:") ? "lab" : "library",
      name: String(params.scopeId).split(":")[1],
      title: String(params.scopeId),
      description: "",
      origin: "workspace",
      writable: true,
      kinds: String(params.scopeId).startsWith("lab:")
        ? ["experiment", "pipeline", "overlay", "dataset"]
        : ["application", "agent"],
    }),
  ),
  http.get(`${API}/api/scopes/:scopeId/visible-libraries`, () =>
    HttpResponse.json({
      libraries: [
        {
          id: "library:versioned_lib",
          kind: "library",
          name: "versioned_lib",
          title: "Versioned Lib",
          origin: "workspace",
          writable: true,
          kinds: ["application"],
        },
      ],
    }),
  ),
  http.get(`${API}/api/scopes/:scopeId/items`, ({ request }) => {
    const url = new URL(request.url);
    const kind = url.searchParams.get("kind");
    if (kind === "application") {
      return HttpResponse.json({
        items: [
          {
            name: "triage",
            kind: "application",
            title: "triage",
            summary: "",
            versions: ["v1", "v2"],
            latest: "v2",
            items: [
              { id: "triage@v1", version: "v1", ref: "versioned_lib:triage@v1", writable: true },
              { id: "triage@v2", version: "v2", ref: "versioned_lib:triage@v2", writable: true },
            ],
          },
        ],
      });
    }
    return HttpResponse.json({ items: [] });
  }),
  http.post(`${API}/api/scopes/:scopeId/items/check`, () =>
    HttpResponse.json({ ok: true, id: "demo", ref: "demo" }),
  ),
  http.get(`${API}/api/runs`, () => HttpResponse.json({ runs: [] })),
  http.get(`${API}/api/runs/:id`, ({ params }) =>
    HttpResponse.json({
      id: params.id,
      scope: "library:versioned_lib",
      kind: "experiment",
      item: "only",
      status: "completed",
      log: "",
    }),
  ),
  http.get(`${API}/api/choices/:kind`, () =>
    HttpResponse.json({ models: [{ id: "generic-model", label: "generic-model" }], default: "generic-model" }),
  ),
];
