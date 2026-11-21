#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Common base for pipeline steps that draw figures.

Every plot step accepts the same figure keys in its ``config``:

``theme``
    ``dark`` or ``light``: background and text tokens. Default ``dark`` for
    live HTML, ``light`` for file formats (``png``, ``svg``, ``pdf``).
``palette``
    A named palette (``pastel``, ``okabe-ito``, ``petrol``) or a list of
    colours, assigned to series in order. Default ``pastel`` for live HTML,
    ``okabe-ito`` for file formats.
``format``
    Output format; each step lists what it can write in ``FORMATS``.
``labels``
    ``{series id: display label}``, for legends and path labels. A series named
    like a figure text key (``x``, ``y``, ``title``) goes under ``labels.series``. ggplot-style
    keys ``title`` / ``subtitle`` / ``x`` / ``y`` / ``caption`` in this map
    are also accepted as figure text (first-class keys below win).
``title``
    Figure title. No title is drawn when unset.
``subtitle``
    Line under the title.
``xlabel`` / ``ylabel``
    Axis titles. Trajectory plots default to ``PC1`` / ``PC2`` when unset.
``caption``
    Small line under the figure.

Live folding is the step-level YAML key ``realtime:``, not a figure key.
``realtime: false`` turns off ``on_batch``; the step then draws once, from
its inputs, when the pipeline seals.

Any of those keys (and any key a plot invents) may be free text or a
``$path`` binding into the pipeline context. ``title: Session trajectories``
is a constant (ggplot ``labs()``). ``legend: $scenario.metadata.legend``
is an accessor (ggplot ``aes()``, d3 ``d => …``). Write ``$$foo`` for a
literal ``$foo``. See ``mas.lab.benchmark.pipeline.binding``.

A step lists the keys it implements in ``HONORS``. Setting a key it does not
implement logs a warning instead of being silently ignored.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, Sequence

from mas.lab.benchmark.pipeline.binding import context_bag, resolve
from mas.lab.benchmark.pipeline.core import PipelineStep

logger = logging.getLogger(__name__)

FIGURE_KEYS: tuple[str, ...] = (
    "theme",
    "palette",
    "format",
    "labels",
    "title",
    "subtitle",
    "xlabel",
    "ylabel",
    "caption",
)

_LABEL_TEXT_KEYS = {
    "title": "title",
    "subtitle": "subtitle",
    "caption": "caption",
    "xlabel": "xlabel",
    "ylabel": "ylabel",
    "x": "xlabel",
    "y": "ylabel",
}

_LIVE_FORMATS = frozenset({"html"})
_COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}|[a-zA-Z]{3,30}|(?:rgb|rgba|hsl|hsla)\(\s*[0-9.%\s,/-]{1,40}\)",
)

PALETTES: dict[str, tuple[str, ...]] = {
    # Readable on the dark default background.
    "pastel": ("#8fd3c1", "#f9c88b", "#c7b8ea", "#f4a6b7", "#9fd1f5", "#d6e8a0", "#f2d0a4"),
    # Colour-blind safe (Okabe & Ito), for light backgrounds and print.
    "okabe-ito": ("#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#d55e00", "#f0e442", "#000000"),
    "petrol": ("#1f4e5f", "#2e7d8c", "#4fa3b0", "#86c5c9", "#c2e3e0"),
}

THEMES: dict[str, dict[str, str]] = {
    "dark": {"bg": "#0d1117", "surface": "#161b22", "text": "#e6edf3", "muted": "#8b949e", "grid": "#30363d"},
    "light": {"bg": "#fafafa", "surface": "#ffffff", "text": "#1f2328", "muted": "#656d76", "grid": "#d0d7de"},
}


def defaults_for_format(fmt: str | None) -> tuple[str, str]:
    """Return ``(theme, palette)`` defaults for an output format."""
    key = str(fmt or "").strip().lower()
    if key in _LIVE_FORMATS:
        return "dark", "pastel"
    return "light", "okabe-ito"


def resolve_theme(name: str | None, *, default: str = "light") -> dict[str, str]:
    key = str(name or default).strip().lower()
    if key not in THEMES:
        raise ValueError(f"theme must be one of {sorted(THEMES)}, got {name!r}")
    return dict(THEMES[key])


def resolve_palette(value: str | Sequence[str] | None, *, default: str = "okabe-ito") -> tuple[str, ...]:
    if value is None or value == "":
        return PALETTES[default]
    if isinstance(value, str):
        key = value.strip().lower()
        if key not in PALETTES:
            raise ValueError(f"palette must be one of {sorted(PALETTES)} or a list of colours, got {value!r}")
        return PALETTES[key]
    colours = tuple(str(c).strip() for c in value)
    if not colours:
        raise ValueError("palette list is empty")
    for colour in colours:
        if not _COLOUR.fullmatch(colour):
            # A colour lands in SVG/HTML attributes: refuse anything that is not a plain colour.
            raise ValueError(f"palette colour must be #hex, rgb()/hsl() or a colour name, got {colour!r}")
    return colours


@dataclass(frozen=True)
class FigureStyle:
    """Resolved figure keys, ready for a renderer."""

    theme: str = "light"
    tokens: dict[str, str] = field(default_factory=lambda: resolve_theme("light"))
    colors: tuple[str, ...] = PALETTES["okabe-ito"]
    labels: Mapping[str, str] = field(default_factory=dict)
    title: str | None = None
    subtitle: str | None = None
    xlabel: str | None = None
    ylabel: str | None = None
    caption: str | None = None

    def color(self, index: int) -> str:
        return self.colors[index % len(self.colors)]

    def label(self, series: str) -> str:
        """Display label: an exact match, else the first path segment's match."""
        if series in self.labels:
            return str(self.labels[series])
        head = series.split("/", 1)[0]
        return str(self.labels.get(head, series))

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> FigureStyle:
        fmt = str(config.get("format") or "").strip().lower()
        default_theme, default_palette = defaults_for_format(fmt)
        theme = str(config.get("theme") or default_theme).strip().lower()
        raw = config.get("labels") or {}
        if not isinstance(raw, Mapping):
            raise ValueError("labels must be a mapping of series id to display label")
        series: dict[str, str] = {}
        inherited: dict[str, str] = {}
        for key, value in raw.items():
            if str(key) == "series":
                # `series:` is the unambiguous form: a scenario called `x` or `title` keeps its label.
                if not isinstance(value, Mapping):
                    raise ValueError("labels.series must be a mapping of series id to display label")
                series.update({str(k): str(v) for k, v in value.items()})
                continue
            slot = _LABEL_TEXT_KEYS.get(str(key))
            if slot:
                inherited[slot] = str(value)
            else:
                series[str(key)] = str(value)

        def _text(name: str) -> str | None:
            value = config.get(name)
            if value:
                return str(value)
            got = inherited.get(name)
            return str(got) if got else None

        return cls(
            theme=theme,
            tokens=resolve_theme(theme, default=default_theme),
            colors=resolve_palette(config.get("palette"), default=default_palette),
            labels=series,
            title=_text("title"),
            subtitle=_text("subtitle"),
            xlabel=_text("xlabel"),
            ylabel=_text("ylabel"),
            caption=_text("caption"),
        )


class FigureStep(PipelineStep):
    """Base class for steps that draw figures (see module docstring)."""

    role: ClassVar[str] = "plot"
    HONORS: ClassVar[frozenset[str]] = frozenset()
    FORMATS: ClassVar[tuple[str, ...]] = ("svg",)

    def __init__(self, name: str, config: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        super().__init__(name, config, *args, **kwargs)
        ignored = [key for key in FIGURE_KEYS if key in (config or {}) and key not in self.HONORS]
        if ignored:
            logger.warning(
                "step %r (%s) does not implement figure key(s) %s; they have no effect",
                name,
                type(self).__name__,
                ", ".join(ignored),
            )
        fmt = (config or {}).get("format")
        if "format" in self.HONORS and fmt and str(fmt).lower() not in self.FORMATS:
            raise ValueError(f"step {name!r}: format must be one of {list(self.FORMATS)}, got {fmt!r}")

    def bind(self, value: Any, ctx: Any = None, datum: Mapping[str, Any] | None = None) -> Any:
        """Resolve a config value: ``$path`` against the pipeline context, else free text.

        *datum* overlays ``scenario`` / ``test`` / ``run`` / ``artefact`` /
        ``row`` for one series or tidy-table row (ggplot ``aes``). Omit it
        for figure-level keys (ggplot ``labs``). Works for keys this class
        defines and for keys a subclass invents.
        """
        return resolve(value, context_bag(ctx, datum))

    def style_for(self, ctx: Any = None) -> FigureStyle:
        """``FigureStyle`` with figure-level ``$path`` bindings resolved."""
        cfg = dict(self.config or {})
        bag = context_bag(ctx)
        for key in ("title", "subtitle", "xlabel", "ylabel", "caption", "theme", "palette"):
            if key in cfg and isinstance(cfg[key], str):
                cfg[key] = resolve(cfg[key], bag)
        labels = cfg.get("labels")
        if isinstance(labels, Mapping):
            cfg["labels"] = {name: resolve(val, bag) if isinstance(val, str) else val for name, val in labels.items()}
        if "format" not in cfg:
            cfg["format"] = self.output_format
        return FigureStyle.from_config(cfg)

    def apply_style(self, config: Mapping[str, Any] | None = None, ctx: Any = None) -> dict[str, Any]:
        """Copy *config* with HONORS keys filled from :meth:`style_for`."""
        style = self.style_for(ctx)
        out = dict(config or self.config or {})
        if "title" in self.HONORS and style.title:
            out["title"] = style.title
        if "subtitle" in self.HONORS and style.subtitle:
            out["subtitle"] = style.subtitle
        if "xlabel" in self.HONORS and style.xlabel:
            out["xlabel"] = style.xlabel
        if "ylabel" in self.HONORS and style.ylabel:
            out["ylabel"] = style.ylabel
        if "caption" in self.HONORS and style.caption:
            out["caption"] = style.caption
        if "theme" in self.HONORS:
            out["theme"] = style.theme
        if "palette" in self.HONORS:
            out["palette"] = list(style.colors)
        if "labels" in self.HONORS and style.labels:
            out["labels"] = dict(style.labels)
        if "format" in self.HONORS:
            out["format"] = str(out.get("format") or self.output_format).lower()
        return out

    @property
    def style(self) -> FigureStyle:
        cfg = dict(self.config or {})
        if "format" not in cfg:
            cfg["format"] = self.output_format
        return FigureStyle.from_config(cfg)

    @property
    def output_format(self) -> str:
        return str(self.config.get("format") or self.FORMATS[0]).lower()
