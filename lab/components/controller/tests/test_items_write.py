#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Create / update / fork / rename / delete / revision / atomicity."""

from __future__ import annotations

from pathlib import Path

import pytest
from scope_fixtures import build_store, copy_scope_fixtures, make_client


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    roots = copy_scope_fixtures(tmp_path)
    return make_client(build_store(roots), monkeypatch), roots


def test_create_mas(client):
    http, roots = client
    resp = http.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "application", "name": "fresh", "version": "v1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "fresh@v1"
    assert (roots["versioned_lib"] / "apps" / "fresh" / "v1" / "mas.yaml").is_file()
    assert "fresh" in body["children"]


def test_create_agent_in_mas(client):
    http, roots = client
    resp = http.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "agent", "name": "helper", "parent": "plain"},
    )
    assert resp.status_code == 201, resp.text
    mas = (roots["versioned_lib"] / "apps" / "plain" / "mas.yaml").read_text(encoding="utf-8")
    assert "helper" in mas
    assert (roots["versioned_lib"] / "apps" / "plain" / "agents" / "helper.yaml").is_file()


def test_standalone_agent(client):
    http, roots = client
    resp = http.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "agent", "name": "solo"},
    )
    assert resp.status_code == 201, resp.text
    assert (roots["versioned_lib"] / "agents" / "solo.yaml").is_file()


def test_create_experiment_in_lab(client):
    http, roots = client
    resp = http.post(
        "/api/scopes/lab:lonely/items",
        json={"kind": "experiment", "name": "second"},
    )
    assert resp.status_code == 201, resp.text
    assert (roots["lonely"] / "experiments" / "second.yaml").is_file()


def test_version_fork(client):
    http, _roots = client
    resp = http.post(
        "/api/scopes/library:versioned_lib/items/application/triage@v2/fork",
        json={"version": "v3"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == "triage@v3"


def test_application_canvas_persists_to_layout_overlay(client):
    http, roots = client
    current = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    mas = current["manifest"] + "x-canvas-positions:\n  coordinator: {x: 10, y: 20}\n"
    agents = dict(current["children"])
    first = next(iter(agents))
    agents[first] = agents[first] + "x-text-input: hello from canvas\n"
    resp = http.put(
        "/api/scopes/library:versioned_lib/items/application/plain",
        json={"manifest": mas, "children": agents, "revision": current["revision"]},
    )
    assert resp.status_code == 200, resp.text
    app_dir = roots["versioned_lib"] / "apps" / "plain"
    on_disk = (app_dir / "mas.yaml").read_text(encoding="utf-8")
    assert "x-canvas-positions" not in on_disk
    overlay = app_dir / "overlays" / "plain.yaml"
    assert overlay.is_file()
    overlay_text = overlay.read_text(encoding="utf-8")
    assert "kind: Overlay" in overlay_text
    assert "name: plain" in overlay_text
    assert "x-ui-canvas" not in overlay_text
    listed = http.get("/api/scopes/library:versioned_lib/items?kind=overlay").json()["items"]
    assert not any(g["name"] == "plain" for g in listed)
    detail = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    assert "x-canvas-positions" in detail["manifest"]
    assert "hello from canvas" in detail["children"][first]


def test_library_layout_overlay_attaches_without_app_overlays_dir(tmp_path, monkeypatch):
    """Canvas overlay in library overlays/ attaches even if apps/<id>/overlays is absent."""
    roots = copy_scope_fixtures(tmp_path)
    overlays = roots["versioned_lib"] / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    (overlays / "plain-canvas.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata: {name: plain}",
                "spec:",
                "  target: {kind: MAS, name: plain}",
                "  patch: {}",
                "x-canvas-positions:",
                "  coordinator: {x: 11, y: 22}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    http = make_client(build_store(roots), monkeypatch)
    assert not (roots["versioned_lib"] / "apps" / "plain" / "overlays").exists()
    detail = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    assert "x-canvas-positions" in detail["manifest"]
    assert "x: 11" in detail["manifest"]


def test_layout_overlay_attaches_after_app_already_loaded(tmp_path, monkeypatch):
    """App first, canvas overlay later — GET attaches after the registry cache invalidates."""
    roots = copy_scope_fixtures(tmp_path)
    store = build_store(roots)
    http = make_client(store, monkeypatch)
    before = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    assert "x-canvas-positions" not in before["manifest"]
    overlays = roots["versioned_lib"] / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    (overlays / "plain-canvas.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata: {name: plain}",
                "spec:",
                "  target: {kind: MAS, name: plain}",
                "  patch: {}",
                "x-canvas-positions:",
                "  coordinator: {x: 7, y: 8}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    store.invalidate()
    after = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    assert "x: 7" in after["manifest"]


def test_application_get_ignores_spec_overlay_canvas(client):
    http, roots = client
    overlays = roots["versioned_lib"] / "apps" / "plain" / "overlays"
    overlays.mkdir(parents=True, exist_ok=True)
    (overlays / "CC-1.yaml").write_text(
        "\n".join(
            [
                "apiVersion: mas/v1",
                "kind: Overlay",
                "metadata: {name: CC-1}",
                "spec:",
                "  target: {kind: MAS, name: plain}",
                "  patch: {params: {injected: true}}",
                "x-canvas-positions:",
                "  coordinator: {x: 4, y: 5}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    detail = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    assert "x-canvas-positions" not in detail["manifest"]
    assert "injected" not in detail["manifest"]


def test_overlay_canvas_is_not_mixed_into_spec(client):
    http, roots = client
    created = http.post(
        "/api/scopes/lab:x/items",
        json={"kind": "overlay", "name": "cot-style"},
    ).json()
    manifest = "\n".join(
        [
            "apiVersion: mas/v1",
            "kind: Overlay",
            "metadata: {name: cot-style}",
            "spec:",
            "  target: {kind: MAS}",
            "  patch: {agents: {moderator: {design_pattern: {type: cot}}}}",
            "x-canvas-positions:",
            "  n1: {x: 8, y: 9}",
            "",
        ]
    )
    resp = http.put(
        "/api/scopes/lab:x/items/overlay/cot-style",
        json={"manifest": manifest, "children": {}, "revision": created["revision"]},
    )
    assert resp.status_code == 200, resp.text
    on_disk = (roots["x"] / "overlays" / "cot-style.yaml").read_text(encoding="utf-8")
    assert "x-canvas-positions" not in on_disk
    assert "design_pattern" in on_disk
    layout = roots["x"] / "overlays" / "cot-style-layout.yaml"
    assert layout.is_file()
    listed = http.get("/api/scopes/lab:x/items?kind=overlay").json()["items"]
    assert "cot-style" in {g["name"] for g in listed}
    assert "cot-style-layout" not in {g["name"] for g in listed}
    detail = http.get("/api/scopes/lab:x/items/overlay/cot-style").json()
    assert "x-canvas-positions" in detail["manifest"]
    assert "design_pattern" in detail["manifest"]


def test_stale_revision(client):
    http, _roots = client
    current = http.get("/api/scopes/library:versioned_lib/items/application/plain").json()
    resp = http.put(
        "/api/scopes/library:versioned_lib/items/application/plain",
        json={"manifest": current["manifest"], "children": current["children"], "revision": "deadbeef"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "stale_revision"


def test_update_and_delete(client):
    http, roots = client
    created = http.post(
        "/api/scopes/lab:x/items",
        json={"kind": "overlay", "name": "extra"},
    ).json()
    updated = http.put(
        "/api/scopes/lab:x/items/overlay/extra",
        json={
            "manifest": created["manifest"].replace("extra", "extra-renamed-desc"),
            "children": {},
            "revision": created["revision"],
        },
    )
    assert updated.status_code == 200, updated.text
    deleted = http.delete("/api/scopes/lab:x/items/overlay/extra")
    assert deleted.status_code == 200
    assert not (roots["x"] / "overlays" / "extra.yaml").exists()


def test_atomicity_nothing_written_on_validate_failure(client, monkeypatch):
    http, roots = client
    from mas.lab.controller import item_service

    def fail_validate(*_a, **_k):
        from mas.lab.controller.item_service import ItemError

        raise ItemError(422, {"reason": "invalid_manifest", "errors": ["injected"]})

    monkeypatch.setattr(item_service, "_validate_writes", fail_validate)
    before = list((roots["versioned_lib"] / "apps").iterdir())
    resp = http.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "application", "name": "boom", "version": "v1"},
    )
    assert resp.status_code == 422
    after = list((roots["versioned_lib"] / "apps").iterdir())
    assert {p.name for p in before} == {p.name for p in after}


def test_rename_reports_changed_items(client):
    http, roots = client
    resp = http.post(
        "/api/scopes/lab:x/items/experiment/smoke/rename",
        json={"name": "smoke-renamed"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "smoke-renamed"
    assert "changed_items" in body
    overlay = (roots["x"] / "overlays" / "tune.yaml").read_text(encoding="utf-8")
    assert "smokestack" in overlay
    assert "smoke-renamedstack" not in overlay


def test_create_non_creatable_kind_rejected(client):
    http, _roots = client
    resp = http.post(
        "/api/scopes/library:versioned_lib/items",
        json={"kind": "skill", "name": "nope"},
    )
    assert resp.status_code == 400


def test_cross_scope_fork_copies_manifest(client):
    http, _roots = client
    source = http.get("/api/scopes/lab:x/items/experiment/smoke")
    assert source.status_code == 200, source.text
    resp = http.post(
        "/api/scopes/lab:x/items/experiment/smoke/fork",
        json={"target_scope": "lab:lonely", "name": "copied"},
    )
    assert resp.status_code in {200, 201}, resp.text
    dest = http.get("/api/scopes/lab:lonely/items/experiment/copied")
    assert dest.status_code == 200, dest.text
    assert "smoke experiment" in dest.json()["manifest"]
    assert "hello" in dest.json()["manifest"]


def test_cross_scope_fork_conflict(client):
    http, _roots = client
    resp = http.post(
        "/api/scopes/lab:x/items/experiment/smoke/fork",
        json={"target_scope": "lab:lonely", "name": "only"},
    )
    assert resp.status_code == 409
