#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Discover live plot providers. mas-lab ships chat + topology; libraries add more.

Libraries register ``mas.lab.ui.plots`` entry points (same pattern as
``mas.lab.ui.folds``). Optional ``MAS_UI_PLOTS=mod:Class`` for local overrides.
mas-lab never imports a use-case package by name.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Protocol

_CACHE: list[Any] | None = None


class PlotProvider(Protocol):
    kinds: frozenset[str]

    def stream_for(self, kind: str) -> str: ...

    def render_html(
        self,
        kind: str,
        data: dict[str, Any],
        *,
        title: str = "",
        fmt: str = "html",
    ) -> str | bytes: ...

    def render_embedded(self, kind: str) -> dict[str, str]: ...


def _add(out: list[Any], seen: set[str], obj: Any, token: str) -> None:
    if token in seen or obj is None:
        return
    inst = obj() if isinstance(obj, type) else obj
    kinds = getattr(inst, "kinds", None)
    if not kinds:
        return
    seen.add(token)
    out.append(inst)


def load_plot_providers(*, refresh: bool = False) -> list[Any]:
    """HtmlDemoPlots plus optional library providers."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    providers: list[Any] = []
    seen: set[str] = set()
    from mas.lab.plots.html_demo import HtmlDemoPlots

    _add(providers, seen, HtmlDemoPlots(), "html-demo")
    for spec in str(os.environ.get("MAS_UI_PLOTS") or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        mod_name, _, attr = spec.partition(":")
        try:
            mod = importlib.import_module(mod_name)
            _add(providers, seen, getattr(mod, attr or "Plots"), spec)
        except Exception:
            continue
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        group = (
            eps.select(group="mas.lab.ui.plots")
            if hasattr(eps, "select")
            else eps.get("mas.lab.ui.plots", [])
        )
        for ep in group or []:
            try:
                _add(providers, seen, ep.load(), f"ep:{getattr(ep, 'name', ep)}")
            except Exception:
                continue
    except Exception:
        pass
    _CACHE = providers
    return providers


def provider_for(kind: str) -> Any:
    name = str(kind or "").strip()
    for provider in load_plot_providers():
        if name in provider.kinds:
            return provider
    raise KeyError(name)


def plot_info(kind: str) -> dict[str, str]:
    name = str(kind or "").strip()
    provider = provider_for(name)
    return {"kind": name, "stream": provider.stream_for(name), "artefact": name}


def render_plot(kind: str, data: dict[str, Any] | None = None, *, fmt: str = "html", title: str = "") -> str | bytes:
    name = str(kind or "").strip()
    provider = provider_for(name)
    return provider.render_html(name, dict(data or {}), title=title, fmt=fmt)


def render_embedded(kind: str) -> dict[str, str]:
    """CSS + draw JS for in-document plugin mount (no iframe)."""
    name = str(kind or "").strip()
    provider = provider_for(name)
    method = getattr(provider, "render_embedded", None)
    if not callable(method):
        raise TypeError(f"plot provider for {name!r} must implement render_embedded")
    body = method(name)
    if not isinstance(body, dict) or not str(body.get("js") or "").strip():
        raise TypeError(f"plot provider for {name!r} returned an empty embed spec")
    js = str(body.get("js") or "")
    if "masDraw" not in js:
        raise TypeError(f"plot provider for {name!r} embed JS must define masDraw")
    if "<iframe" in js.lower() or "postMessage" in js:
        raise TypeError(f"plot provider for {name!r} embed JS must not use an iframe")
    return {
        "kind": str(body.get("kind") or name),
        "css": str(body.get("css") or ""),
        "js": js,
    }


def render_draw_module(kind: str) -> str:
    """ES module that exports install(d3, window, STATE, CHART) → masDraw."""
    spec = render_embedded(kind)
    js = spec["js"]
    return (
        "export default function install(d3, window, STATE, CHART) {\n"
        f"{js}\n"
        "  return window.masDraw;\n"
        "}\n"
    )
