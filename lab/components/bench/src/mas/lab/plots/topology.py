#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Topology diagram: the agents of a MAS, what each one carries, and who talks to whom.

Reads the *specs* (``mas.yaml`` plus the agent files it references), not a trace, so the picture
cannot drift from the application::

    from mas.lab.plots.topology import plot_topology
    svg = plot_topology("sre-triage-app/mas.yaml")                 # self-contained SVG
    html = plot_topology("sre-triage-app/mas.yaml", fmt="html")    # same, with hover highlighting

    mas-lab plot topology sre-triage-app/mas.yaml -o app.svg

Layout
    Left to right. An agent's column is its distance from the entry agent along delegation edges
    (longest acyclic path); agents the entry cannot reach go in the last column. Inside a column,
    agents are ordered by the mean position of their delegators, which keeps edges short for
    trees, diamonds and pipelines alike. Nothing in the layout or the drawing depends on agent
    names.

Marks
    * agent       a card with an accent bar; the entry agent has a heavier outline
    * skill       outlined pill with a dot (instructions the agent reads); dashed and dimmed
                  when most agents carry it, so each agent's own skills stand out
    * tool        square chip with a solid edge (code the agent can call)
    * delegation  solid arrow, delegator to delegate
    * peer        dashed arrow with a head at both ends, for agents that talk as equals
                  (``peers:`` on a workflow node, or ``Topology.peers``)
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    id: str
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


@dataclass
class Topology:
    """Agents plus directed delegation edges and undirected peer edges."""

    agents: dict[str, Agent]
    delegations: list[tuple[str, str]] = field(default_factory=list)
    peers: list[tuple[str, str]] = field(default_factory=list)
    entry: str = ""
    name: str = ""


def _name(item: Any) -> str:
    """Skill or tool entry -> display name (a string, ``{name|id}``, or ``{ref: path/to/x.tool.yaml}``)."""
    if isinstance(item, dict):
        item = item.get("name") or item.get("id") or item.get("ref") or ""
    text = str(item).strip()
    if "/" in text or text.endswith((".yaml", ".yml")):
        text = Path(text).name
        for suffix in (".tool.yaml", ".skill.yaml", ".yaml", ".yml"):
            text = text.removesuffix(suffix)
    return text


def _load_yaml(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc.get("spec", doc) if isinstance(doc, dict) else {}


def load_topology(path: str | Path) -> Topology:
    """Build a :class:`Topology` from a ``mas.yaml`` (agent files resolved relative to it)."""
    path = Path(path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    spec = doc.get("spec", {})
    agents: dict[str, Agent] = {}
    for entry in (spec.get("agency") or {}).get("agents") or []:
        aid, ref = str(entry.get("id", "")).strip(), entry.get("ref")
        if not aid:
            continue
        body = _load_yaml(path.parent / ref) if ref and (path.parent / ref).exists() else {}
        agents[aid] = Agent(
            aid,
            [n for n in map(_name, body.get("skills") or []) if n],
            [n for n in map(_name, body.get("tools") or []) if n],
        )
    workflow = spec.get("workflow") or {}
    delegations: list[tuple[str, str]] = []
    peers: list[tuple[str, str]] = []
    for node in workflow.get("nodes") or []:
        src = str(node.get("id", "")).strip()
        for dst in node.get("delegates_to") or []:
            delegations.append((src, str(dst)))
        for dst in node.get("peers") or []:
            if (str(dst), src) not in peers:
                peers.append((src, str(dst)))
    for a, b in [*delegations, *peers]:
        agents.setdefault(a, Agent(a))
        agents.setdefault(b, Agent(b))
    return Topology(
        agents, delegations, peers, str(workflow.get("entry") or ""), str((doc.get("metadata") or {}).get("name", ""))
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _columns(topo: Topology) -> list[list[str]]:
    """Agents grouped by distance from the entry, ordered inside each column by their delegators."""
    ids = list(topo.agents)
    entry = (
        topo.entry
        if topo.entry in topo.agents
        else next((i for i in ids if all(d != i for _, d in topo.delegations)), ids[0] if ids else "")
    )
    out: dict[str, list[str]] = {}
    for s, d in topo.delegations:
        out.setdefault(s, []).append(d)
    # Drop back edges (found by a DFS from the entry) so cycles cannot inflate the ranks.
    back: set[tuple[str, str]] = set()
    state: dict[str, int] = {}  # 1 = on the DFS stack, 2 = done

    def visit(n: str) -> None:
        state[n] = 1
        for d in out.get(n, []):
            if state.get(d) == 1:
                back.add((n, d))
            elif d not in state:
                visit(d)
        state[n] = 2

    for root in [entry, *ids]:
        if root not in state:
            visit(root)
    dag = [(s, d) for s, d in topo.delegations if (s, d) not in back and s != d]
    rank = {entry: 0}
    for _ in range(len(ids)):  # longest path from the entry on the DAG
        changed = False
        for s, d in dag:
            if s in rank and rank.get(d, -1) < rank[s] + 1:
                rank[d] = rank[s] + 1
                changed = True
        if not changed:
            break
    last = max(rank.values(), default=0)
    for i in ids:  # unreachable from the entry
        rank.setdefault(i, last + 1 if last or len(ids) > 1 else 0)
    cols: list[list[str]] = [[] for _ in range(max(rank.values(), default=0) + 1)]
    for i in ids:
        cols[rank[i]].append(i)
    order: dict[str, int] = {}  # first mention as a delegate, else declaration order
    for _, d in topo.delegations:
        order.setdefault(d, len(order))
    for i in ids:
        order.setdefault(i, len(order))
    pos: dict[str, float] = {i: k for k, i in enumerate(cols[0])}
    for c in range(1, len(cols)):

        def bary(i: str, c: int = c) -> float:
            ps = [pos[s] for s, d in topo.delegations if d == i and s in pos and rank[s] < c]
            return sum(ps) / len(ps) if ps else float(len(ids))

        cols[c].sort(key=lambda i, c=c: (bary(i, c), order[i]))
        for k, i in enumerate(cols[c]):
            pos[i] = k
    return [c for c in cols if c]


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

BG, INK, MUTED, LINE, CARD = "#0B0F0C", "#E6E8E3", "#8E968C", "#2A312B", "#111511"
ACCENTS = ["#40CDF0", "#FFBD33", "#B376EA", "#94E147", "#FF9F43", "#FF6B9D", "#5EEAD4", "#C4B5FD"]
ENTRY_ACCENT, ALERT = "#94E147", "#FF5A4F"
MONO = "ui-monospace, Menlo, Consolas, monospace"
SANS = "Aptos, Helvetica, Arial, sans-serif"

CHAR, CHIP_H, GAP, LINE_H = 7.0, 22, 7, 29  # monospace advance at 11.5px, chip height, gaps
CARD_W, COL_GAP, ROW_GAP, PAD = 360, 110, 22, 18
LEFT, TOP = 112, 78


def _shared(topo: Topology) -> set[str]:
    """Skills most agents carry (shown dimmed)."""
    n = len(topo.agents)
    seen: dict[str, int] = {}
    for a in topo.agents.values():
        for s in set(a.skills):
            seen[s] = seen.get(s, 0) + 1
    return {s for s, k in seen.items() if k >= 3 and k * 2 >= n}


def _chip_w(label: str, kind: str) -> float:
    return len(label) * CHAR + (26 if kind == "skill" else 28)


def _flow(
    items: list[str], x: float, y: float, width: float, kind: str, accent: str, shared: set[str], emit: list[str] | None
) -> float:
    """Place chips left to right, wrapping at ``width``; return the y below the last line."""
    cx, cy = x, y
    for label in items:
        w = _chip_w(label, kind)
        if cx + w > x + width and cx > x:
            cx, cy = x, cy + LINE_H
        if emit is not None:
            dim = kind == "skill" and label in shared
            esc = _html.escape(label)
            if kind == "skill":
                col = MUTED if dim else accent
                dash = ' stroke-dasharray="3 3"' if dim else ""
                emit.append(
                    f'<g class="chip skill"><title>skill: {esc}</title>'
                    f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{w:.1f}" height="{CHIP_H}" rx="{CHIP_H / 2}" fill="none" '
                    f'stroke="{LINE if dim else accent}" stroke-width="1.2"{dash}/>'
                    f'<circle cx="{cx + 11:.1f}" cy="{cy + CHIP_H / 2}" r="2.5" fill="{col}"/>'
                    f'<text x="{cx + 20:.1f}" y="{cy + 15}" font-family="{MONO}" font-size="11.5" '
                    f'fill="{MUTED if dim else INK}">{esc}</text></g>'
                )
            else:
                emit.append(
                    f'<g class="chip tool"><title>tool: {esc}</title>'
                    f'<rect x="{cx:.1f}" y="{cy:.1f}" width="{w:.1f}" height="{CHIP_H}" rx="2" fill="{accent}" '
                    f'fill-opacity=".14" stroke="{accent}" stroke-width="1.2"/>'
                    f'<rect x="{cx:.1f}" y="{cy:.1f}" width="6" height="{CHIP_H}" rx="2" fill="{accent}"/>'
                    f'<text x="{cx + 15:.1f}" y="{cy + 15}" font-family="{MONO}" font-size="11.5" '
                    f'fill="{INK}">{esc}</text></g>'
                )
        cx += w + GAP
    return cy + CHIP_H


def _card_height(agent: Agent, shared: set[str]) -> float:
    inner = CARD_W - 2 * PAD
    y = 40.0
    if agent.skills:
        y = _flow(agent.skills, 0, y, inner, "skill", "", shared, None)
    if agent.tools:
        y = _flow(agent.tools, 0, y + (12 if agent.skills else 0), inner, "tool", "", shared, None)
    return y + PAD - 2


def _curve(x1: float, y1: float, x2: float, y2: float) -> str:
    dx = max(40.0, (x2 - x1) / 2)
    return f"M{x1:.1f} {y1:.1f} C{x1 + dx:.1f} {y1:.1f} {x2 - dx:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"


def _head(x: float, y: float, direction: int, color: str) -> str:
    """Open arrowhead at (x, y) pointing right (+1) or left (-1)."""
    return (
        f'<path d="M{x - 8 * direction:.1f} {y - 4.5:.1f} L{x:.1f} {y:.1f} L{x - 8 * direction:.1f} {y + 4.5:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
    )


def plot_topology(
    source: "str | Path | Topology", fmt: str = "svg", *, title: str | None = None, entry_label: str = "input"
) -> str:
    """Render a topology as ``fmt`` = ``"svg"`` or ``"html"``.

    ``source`` is a ``mas.yaml`` path or a :class:`Topology`. ``title`` defaults to the MAS name;
    pass ``""`` for none. ``entry_label`` names what enters the first agent (for example ``incident``).
    """
    topo = source if isinstance(source, Topology) else load_topology(source)
    if not topo.agents:
        raise ValueError("topology has no agents")
    cols = _columns(topo)
    shared = _shared(topo)
    heights = {i: _card_height(topo.agents[i], shared) for c in cols for i in c}
    col_h = [sum(heights[i] for i in c) + ROW_GAP * (len(c) - 1) for c in cols]
    body_h = max(col_h)
    box: dict[str, tuple[float, float, float, float]] = {}  # id -> x, y, w, h
    for ci, c in enumerate(cols):
        x = LEFT + ci * (CARD_W + COL_GAP)
        y = TOP + (body_h - col_h[ci]) / 2
        for i in c:
            box[i] = (x, y, CARD_W, heights[i])
            y += heights[i] + ROW_GAP
    entry = topo.entry if topo.entry in box else cols[0][0]
    accent: dict[str, str] = {}
    for c in cols:
        for i in c:
            accent[i] = ENTRY_ACCENT if i == entry else ACCENTS[(len(accent) - (entry in accent)) % len(ACCENTS)]

    legend_y = TOP + body_h + 44
    W = LEFT + len(cols) * CARD_W + (len(cols) - 1) * COL_GAP + 56
    H = legend_y + 36
    heading = topo.name if title is None else title
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
        f'font-family="{SANS}"><rect width="{W:.0f}" height="{H:.0f}" rx="12" fill="{BG}"/>'
    ]
    if heading:
        out.append(f'<text x="32" y="42" fill="#fff" font-size="21" font-weight="300">{_html.escape(heading)}</text>')

    # entry marker: an alert glyph and one word, no card
    ex, ey, _, eh = box[entry]
    my = ey + eh / 2
    gx = 34
    out.append(
        f'<g class="entry"><path d="M{gx} {my + 11:.1f} L{gx + 15} {my - 14:.1f} L{gx + 30} {my + 11:.1f} Z" '
        f'fill="none" '
        f'stroke="{ALERT}" stroke-width="1.9" stroke-linejoin="round"/>'
        f'<line x1="{gx + 15}" y1="{my - 5:.1f}" x2="{gx + 15}" y2="{my + 2:.1f}" stroke="{ALERT}" '
        f'stroke-width="1.9" stroke-linecap="round"/>'
        f'<circle cx="{gx + 15}" cy="{my + 6.5:.1f}" r="1.3" fill="{ALERT}"/>'
        f'<text x="{gx + 15}" y="{my + 33:.1f}" text-anchor="middle" fill="{MUTED}" font-family="{MONO}" '
        f'font-size="11">{_html.escape(entry_label)}</text>'
        f'<line x1="{gx + 38}" y1="{my:.1f}" x2="{ex - 4:.1f}" y2="{my:.1f}" stroke="{MUTED}" stroke-width="1.5"/>'
        + _head(ex - 2, my, 1, MUTED)
        + "</g>"
    )

    # edges first, so cards sit on top of them
    edges: list[str] = []
    out_count: dict[str, int] = {}
    for s, d in topo.delegations:
        if s not in box or d not in box:
            continue
        sx, sy, sw, sh = box[s]
        dx, dy, dw, dh = box[d]
        n = out_count[s] = out_count.get(s, 0) + 1
        if dx > sx:  # forward: right side to left side
            x1, y1, x2, y2 = sx + sw, sy + sh / 2, dx - 2, dy + dh / 2
            path = _curve(x1, y1, x2, y2)
            head = _head(x2, y2, 1, MUTED)
        else:  # back or sideways: loop around the left of the column
            x1, y1, x2, y2 = sx, sy + sh / 2, dx, dy + dh / 2
            bulge = 38 + 8 * n
            path = f"M{x1:.1f} {y1:.1f} C{x1 - bulge:.1f} {y1:.1f} {x2 - bulge:.1f} {y2:.1f} {x2 - 2:.1f} {y2:.1f}"
            head = _head(x2 - 2, y2, 1, MUTED)
        edges.append(
            f'<g class="edge deleg" data-from="{_html.escape(s)}" data-to="{_html.escape(d)}">'
            f'<path d="{path}" fill="none" stroke="{MUTED}" stroke-width="1.4" opacity=".75"/>{head}</g>'
        )
    for a, b in topo.peers:
        if a not in box or b not in box:
            continue
        (ax, ay, _aw, ah), (bx, by, _bw, bh) = box[a], box[b]
        if ax == bx:  # same column: arc on the left of the cards
            ya, yb = ay + ah / 2, by + bh / 2
            bulge = 54
            path = f"M{ax:.1f} {ya:.1f} C{ax - bulge:.1f} {ya:.1f} {ax - bulge:.1f} {yb:.1f} {ax - 2:.1f} {yb:.1f}"
            h1, h2 = _head(ax + 1, ya, 1, "#C9CCC6"), _head(ax - 1, yb, 1, "#C9CCC6")
        else:  # different columns: dashed curve between facing sides
            left, r = (box[a], box[b]) if ax < bx else (box[b], box[a])
            x1, y1, x2, y2 = left[0] + left[2], left[1] + left[3] / 2 + 10, r[0] - 2, r[1] + r[3] / 2 + 10
            path = _curve(x1, y1, x2, y2)
            h1, h2 = _head(x1 - 1, y1, -1, "#C9CCC6"), _head(x2, y2, 1, "#C9CCC6")
        edges.append(
            f'<g class="edge peer" data-from="{_html.escape(a)}" data-to="{_html.escape(b)}">'
            f'<path d="{path}" fill="none" stroke="#C9CCC6" stroke-width="1.4" stroke-dasharray="5 4" '
            f'opacity=".85"/>{h1}{h2}</g>'
        )
    out.extend(edges)

    for c in cols:
        for i in c:
            x, y, w, h = box[i]
            col, agent = accent[i], topo.agents[i]
            out.append(
                f'<g class="agent" data-id="{_html.escape(i)}"><rect x="{x:.1f}" y="{y:.1f}" width="{w}" '
                f'height="{h:.1f}" rx="9" fill="{CARD}" '
                f'stroke="{col if i == entry else LINE}" stroke-width="{2 if i == entry else 1}"/>'
                f'<rect x="{x:.1f}" y="{y:.1f}" width="5" height="{h:.1f}" rx="2.5" fill="{col}"/>'
                f'<text x="{x + PAD + 4:.1f}" y="{y + 27:.1f}" fill="{col}" font-size="16" font-weight="700">'
                f"{_html.escape(i)}</text>"
            )
            if i == entry:
                out.append(
                    f'<text x="{x + w - PAD:.1f}" y="{y + 26:.1f}" text-anchor="end" fill="{MUTED}" '
                    f'font-family="{MONO}" font-size="10.5">entry</text>'
                )
            inner = w - 2 * PAD - 4
            cy = y + 40
            if agent.skills:
                cy = _flow(agent.skills, x + PAD + 4, cy, inner, "skill", col, shared, out)
            if agent.tools:
                _flow(agent.tools, x + PAD + 4, cy + (12 if agent.skills else 0), inner, "tool", col, shared, out)
            out.append("</g>")

    # legend, only for the marks that appear
    lx = 32
    legend = [
        ("skill", "skill: instructions the agent reads", any(a.skills for a in topo.agents.values())),
        ("tool", "tool: code the agent can call", any(a.tools for a in topo.agents.values())),
        ("deleg", "delegates to", bool(topo.delegations)),
        ("peer", "talks to (peer)", bool(topo.peers)),
        ("shared", "dashed pill: skill most agents share", bool(shared)),
    ]
    for kind, text, show in legend:
        if not show:
            continue
        y = legend_y
        if kind == "skill":
            out.append(
                f'<rect x="{lx}" y="{y - 11}" width="34" height="18" rx="9" fill="none" stroke="{MUTED}" '
                f'stroke-width="1.2"/><circle cx="{lx + 10}" cy="{y - 2}" r="2.4" fill="{MUTED}"/>'
            )
            adv = 34
        elif kind == "tool":
            out.append(
                f'<rect x="{lx}" y="{y - 11}" width="34" height="18" rx="2" fill="{MUTED}" fill-opacity=".14" '
                f'stroke="{MUTED}" stroke-width="1.2"/><rect x="{lx}" y="{y - 11}" width="6" height="18" '
                f'rx="2" fill="{MUTED}"/>'
            )
            adv = 34
        elif kind == "shared":
            out.append(
                f'<rect x="{lx}" y="{y - 11}" width="34" height="18" rx="9" fill="none" stroke="{MUTED}" '
                f'stroke-width="1.2" stroke-dasharray="3 3"/>'
            )
            adv = 34
        elif kind == "deleg":
            out.append(
                f'<line x1="{lx}" y1="{y - 2}" x2="{lx + 32}" y2="{y - 2}" stroke="{MUTED}" stroke-width="1.4"/>'
                + _head(lx + 34, y - 2, 1, MUTED)
            )
            adv = 36
        else:
            out.append(
                f'<line x1="{lx + 2}" y1="{y - 2}" x2="{lx + 32}" y2="{y - 2}" stroke="#C9CCC6" '
                f'stroke-width="1.4" stroke-dasharray="5 4"/>'
                + _head(lx + 34, y - 2, 1, "#C9CCC6")
                + _head(lx, y - 2, -1, "#C9CCC6")
            )
            adv = 36
        out.append(f'<text x="{lx + adv + 8}" y="{y + 2}" fill="{MUTED}" font-size="12">{_html.escape(text)}</text>')
        lx += adv + 8 + len(text) * 6.4 + 30
    out.append("</svg>")
    svg = "".join(out)
    if fmt == "svg":
        return svg
    if fmt == "html":
        return _HTML.replace("@TITLE@", _html.escape(heading or "MAS topology")).replace("@SVG@", svg)
    raise ValueError(f"unknown format {fmt!r}: expected 'svg' or 'html'")


_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>@TITLE@</title>
<style>html,body{margin:0;background:#0B0F0C}body{display:flex;justify-content:center;padding:2rem}
svg{max-width:100%;height:auto}.agent,.edge{transition:opacity .15s}.dim{opacity:.18}</style></head>
<body>@SVG@<script>
const agents=[...document.querySelectorAll('.agent')],edges=[...document.querySelectorAll('.edge')];
agents.forEach(a=>{a.addEventListener('mouseenter',()=>{const id=a.dataset.id,on=new Set([id]);
edges.forEach(e=>{const hit=e.dataset.from===id||e.dataset.to===id;if(hit){on.add(e.dataset.from);on.add(e.dataset.to)}
e.classList.toggle('dim',!hit)});
agents.forEach(x=>x.classList.toggle('dim',!on.has(x.dataset.id)))});
a.addEventListener('mouseleave',()=>[...agents,...edges].forEach(x=>x.classList.remove('dim')))});
</script></body></html>"""

__all__ = ["Agent", "Topology", "load_topology", "plot_topology"]
