#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The KG viewer page and the loopback server behind ``mas-lab plot kg``."""

from __future__ import annotations

import contextvars
import json
import re
import socketserver
import time
import urllib.error
import urllib.request

import pytest
from mas.lab.plots import kg_widget

_CALLER: contextvars.ContextVar[str] = contextvars.ContextVar("kg_widget_test_caller", default="empty")

_KG = {"nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}], "edges": [{"source": "a", "target": "b"}]}


def _embedded(page: str, name: str) -> dict:
    match = re.search(rf"var {name}\s*=\s*(.*?);\n", page)
    assert match, name
    return json.loads(match.group(1).replace("<\\/", "</").replace("<\\!--", "<!--"))


def test_the_page_embeds_the_graph_and_the_options() -> None:
    page = kg_widget.build_standalone_html(_KG, title="Run 1", opts={"layout": "cose-bilkent"})
    assert _embedded(page, "kg") == _KG
    opts = _embedded(page, "opts")
    assert opts["layout"] == "cose-bilkent" and opts["panelMode"] == "open" and opts["title"] == "Run 1"
    assert "2 nodes · 1 edges" in page
    assert "kg_widget" not in page and "KGWidget" in page  # the widget source is inlined


def test_a_serialised_graph_is_accepted() -> None:
    assert _embedded(kg_widget.build_standalone_html(json.dumps(_KG)), "kg") == _KG


def test_the_title_is_escaped_in_the_page() -> None:
    page = kg_widget.build_standalone_html(_KG, title="<img src=x onerror=alert(1)> & co")
    assert page.count("&lt;img src=x onerror=alert(1)&gt; &amp; co") == 2  # <title> and the heading
    # the raw title appears once more, as a JSON string in the options, which the widget escapes itself
    assert page.count("<img src=x") == 1
    assert "<img src=x" in page.split("var opts", 1)[1].split(";\n", 1)[0]


def test_a_script_end_tag_in_a_label_cannot_end_the_script_element() -> None:
    hostile = {"nodes": [{"id": "x", "label": "</script><script>alert(1)</script><!--"}], "edges": []}
    page = kg_widget.build_standalone_html(hostile)
    assert "</script><script>alert(1)" not in page
    assert "<!--" not in page.split("var kg", 1)[1]
    assert _embedded(page, "kg") == hostile


def test_serve_and_open_serves_the_page_to_the_browser_from_a_clean_thread(monkeypatch, capsys) -> None:
    seen: dict = {}

    def fake_open(url: str) -> bool:
        seen["url"] = url
        seen["context"] = _CALLER.get()
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - the loopback test server
            seen["status"] = response.status
            seen["body"] = response.read().decode()
        return True

    def one_request_then_stop(self: socketserver.TCPServer, *_a: object) -> None:
        self.handle_request()  # the "browser" fetches the page
        raise KeyboardInterrupt

    monkeypatch.setattr(kg_widget.webbrowser, "open", fake_open)
    monkeypatch.setattr(socketserver.TCPServer, "serve_forever", one_request_then_stop)
    _CALLER.set("caller")

    kg_widget.serve_and_open(_KG, title="Run 1")

    deadline = time.monotonic() + 5
    while "body" not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert seen["url"].startswith("http://127.0.0.1:") and seen["status"] == 200
    assert _embedded(seen["body"], "kg") == _KG
    assert seen["context"] == "empty"
    assert seen["url"] in capsys.readouterr().out


def test_the_server_answers_only_the_page(monkeypatch) -> None:
    statuses: list[int] = []

    def fake_open(url: str) -> bool:
        for path in ("index.html", "nope"):
            try:
                with urllib.request.urlopen(url + path, timeout=5) as response:  # noqa: S310
                    statuses.append(response.status)
            except urllib.error.HTTPError as exc:
                statuses.append(exc.code)
        return True

    def two_requests_then_stop(self: socketserver.TCPServer, *_a: object) -> None:
        self.handle_request()
        self.handle_request()
        raise KeyboardInterrupt

    monkeypatch.setattr(kg_widget.webbrowser, "open", fake_open)
    monkeypatch.setattr(socketserver.TCPServer, "serve_forever", two_requests_then_stop)

    kg_widget.serve_and_open(_KG)

    deadline = time.monotonic() + 5
    while len(statuses) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert statuses == [200, 404]


def test_the_composer_assets_are_gone() -> None:
    assert not hasattr(kg_widget, "get_composer_js") and not hasattr(kg_widget, "get_composer_css")
    assert not list(kg_widget._HERE.glob("kg_composer.*"))


def test_offline_flag_is_gone() -> None:
    with pytest.raises(TypeError):
        kg_widget.build_standalone_html(_KG, offline=True)  # type: ignore[call-arg]
