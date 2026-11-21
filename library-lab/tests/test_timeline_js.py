#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E402
"""Run timeline.js against a tiny fake DOM (node), the way the live page calls it.

The page hands ``masDraw`` a d3 selection; an embedding may hand it the element. Neither was tested
until a browser showed the first one throwing and drawing nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSET = Path(__file__).resolve().parents[1] / "src" / "mas" / "library" / "lab" / "ui" / "assets" / "timeline.js"

HARNESS = r"""
const fs = require("fs");
class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this._text = ""; this.shadowRoot = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { this.children.push(c); return c; }
  set innerHTML(v) { if (v === "") this.children = []; else throw new Error("innerHTML must only be cleared"); }
  get innerHTML() { return ""; }
  set textContent(v) { this._text = String(v); }
  get textContent() { return this._text; }
}
global.document = { createElementNS: (_ns, tag) => new El(tag) };
eval(fs.readFileSync(process.argv[2], "utf8") + "; global.masDraw = masDraw;");
const payload = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = {};
const hostA = new El("div");
masDraw({ node: () => hostA }, payload);          // a d3 selection
const hostB = new El("div");
masDraw(hostB, payload);                           // the element itself
const walk = (el, acc = []) => { acc.push(el); el.children.forEach((c) => walk(c, acc)); return acc; };
for (const [name, host] of [["selection", hostA], ["element", hostB]]) {
  const all = walk(host);
  out[name] = {
    svgs: all.filter((e) => e.tag === "svg").length,
    rects: all.filter((e) => e.tag === "rect").length,
    polys: all.filter((e) => e.tag === "polygon").length,
    texts: all.filter((e) => e.tag === "text").map((e) => e.textContent),
  };
}
masDraw(null, payload);                            // must not throw
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_timeline_draws_for_a_selection_and_for_an_element(tmp_path: Path) -> None:
    events = [
        {"name": "fixture.ready", "kind": "fixture", "source": {"id": "issuer"}, "time": {"elapsed_ms": 1}},
        {"name": "fixture.stopped", "kind": "fixture", "source": {"id": "issuer"}, "time": {"elapsed_ms": 90}},
        {
            "name": "trigger.matched",
            "kind": "trigger",
            "source": {"id": "revoke"},
            "payload": {"occurrence": 2},
            "time": {"elapsed_ms": 40},
        },
        {
            "name": "action.started",
            "kind": "action",
            "source": {"id": "revoke", "step": "http_call"},
            "time": {"elapsed_ms": 41},
        },
        {
            "name": "action.done",
            "kind": "action",
            "source": {"id": "revoke", "step": "http_call"},
            "time": {"elapsed_ms": 45},
        },
        {
            "name": "fixture.ready",
            "kind": "fixture",
            "source": {"id": "<img src=x onerror=1>and-a-very-long-name"},
            "time": {"elapsed_ms": 50},
        },
    ]
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    data = tmp_path / "events.json"
    data.write_text(json.dumps({"events": events}), encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ASSET), str(data)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    for kind in ("selection", "element"):
        got = out[kind]
        assert got["svgs"] == 1 and got["polys"] == 1  # one figure, one trigger diamond
        assert got["rects"] >= 3  # fixture bars, the action span and a marker
        assert "revoke #2" in got["texts"]  # the diamond says which trigger and which occurrence
        assert all(len(text) <= 24 for text in got["texts"])  # long ids are cut, never overflow
        assert "<img src=x onerror=1>and-a-very-long-name"[:16] in got["texts"]
    assert out["selection"] == out["element"]


def test_a_skipped_trigger_is_an_outline_in_the_file_format() -> None:
    from mas.library.lab.ui.timeline import render_svg

    events = [
        {"name": "trigger.matched", "kind": "trigger", "source": {"id": "t"}, "time": {"elapsed_ms": 1}},
        {"name": "trigger.skipped", "kind": "trigger", "source": {"id": "t"}, "time": {"elapsed_ms": 2}},
    ]
    svg = render_svg("timeline", {"events": events})
    assert svg.count("<polygon") == 2 and svg.count('fill="none"') == 1
