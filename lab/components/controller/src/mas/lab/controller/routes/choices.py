#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Registry-driven picker lists for the UI (models, patterns, tools, …)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from mas.lab.controller.deps import store_from_request
from mas.lab.controller.manifest_layout import item_ref, layout_for
from mas.lab.controller.scope_catalog import get_scope_catalog

router = APIRouter()

_CHOICE_KINDS = (
    "models",
    "design-patterns",
    "step-types",
    "runners",
    "tools",
    "skills",
    "overlays",
)

_CHOICE_KIND_TO_LAYOUT = {
    "tools": "tool",
    "skills": "skill",
    "overlays": "overlay",
}


@router.get("/api/choices/{choice_kind}", tags=["Choices"])
async def get_choices(
    choice_kind: str,
    request: Request,
    scope: str | None = Query(default=None),
):
    if choice_kind not in _CHOICE_KINDS:
        raise HTTPException(status_code=404, detail=f"unknown choices {choice_kind!r}")
    if choice_kind == "models":
        return _models()
    if choice_kind == "design-patterns":
        return await _design_patterns(request)
    if choice_kind == "step-types":
        from mas.lab.controller.lab_registry import get_lab_registry

        return get_lab_registry().pipeline_step_types()
    if choice_kind == "runners":
        from mas.lab.controller.api import ControllerAPI

        return {"runners": ControllerAPI().list_runtime_runners()}
    layout_kind = _CHOICE_KIND_TO_LAYOUT.get(choice_kind)
    if layout_kind is not None:
        return _scope_items(request, layout_kind, scope)
    raise HTTPException(status_code=404, detail=f"unknown choices {choice_kind!r}")


def _models() -> dict:
    from mas.lab.controller.lab_registry import get_lab_registry

    reg = get_lab_registry()
    default = reg.default_model()
    aliases: list[dict] = []
    seen: set[str] = set()
    # Infra aliases from the active workspace plus the logical default.
    try:
        defaults = reg.agent_defaults()
        model = (defaults.get("models") or [{}])
        if isinstance(model, list):
            for entry in model:
                ident = str((entry or {}).get("model") or "")
                if ident and ident not in seen:
                    aliases.append({"id": ident, "label": ident, "source": "defaults"})
                    seen.add(ident)
    except Exception:
        pass
    if default and default not in seen:
        aliases.insert(0, {"id": default, "label": default, "source": "default"})
        seen.add(default)
    # generic-model and other infra aliases if the runtime exposes them.
    try:
        from mas.runtime.registry import get_registry

        for item in get_registry().list("llm") or []:
            ident = str(item.get("urn") or item.get("name") or "")
            if ident and ident not in seen:
                aliases.append({"id": ident, "label": ident, "source": "infra"})
                seen.add(ident)
    except Exception:
        pass
    return {"models": aliases, "default": default}


async def _design_patterns(request: Request) -> dict:
    from mas.lab.controller.routes.design_patterns import list_design_patterns

    return await list_design_patterns()


def _scope_items(request: Request, kind: str, scope_id: str | None) -> dict:
    store = store_from_request(request)
    catalog = get_scope_catalog(store)
    if scope_id:
        try:
            scopes = [catalog.get(scope_id)]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"scope {scope_id!r} not found") from exc
    else:
        scopes = [s for s in catalog.all() if s.kind == "library"]
    items: list[dict] = []
    for scope in scopes:
        try:
            located = layout_for(kind).list_located(scope)
        except KeyError:
            continue
        for item in located:
            namespace = "global"
            if kind == "overlay":
                try:
                    from mas.runtime.spec.source import load_yaml_file

                    doc = load_yaml_file(item.manifest_path) or {}
                    namespace = str(
                        (doc.get("metadata") or {}).get("namespace")
                        or doc.get("x-namespace")
                        or "global"
                    )
                except Exception:
                    namespace = "global"
            items.append(
                {
                    "id": item.item_id,
                    "name": item.name,
                    "title": item.title or item.name,
                    "summary": item.summary,
                    "ref": item_ref(scope, item.item_id),
                    "namespace": namespace,
                    "scope": scope.id,
                }
            )
    key = {"tool": "tools", "skill": "skills", "overlay": "overlays"}[kind]
    return {key: items}
