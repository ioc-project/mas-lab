#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""KG Widget — Python helpers.

Generates standalone HTML pages using the KGWidget JavaScript component.

Public API
----------
build_standalone_html(kg_data, title, opts)  ->  str
    Returns a complete self-contained HTML document embedding the widget.

serve_and_open(kg_data, title, opts, port)
    Starts a tiny HTTP server on the loopback address, opens the browser, waits for Ctrl-C.
    This is what ``mas-lab plot kg`` runs unless it is given ``-o`` or ``--no-open``.

get_widget_js()  ->  str
get_widget_css() ->  str
    Return the raw JS/CSS content for embedding in other HTML pages.
"""

from __future__ import annotations

import html
import json
import logging
import socketserver
import webbrowser
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from mas.runtime.aio import new_thread

logger = logging.getLogger(__name__)

_HERE = Path(__file__).parent

# CDN URLs for Cytoscape and layout extensions
_CDN = [
    "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js",
    "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js",
    "https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js",
    "https://cdn.jsdelivr.net/npm/layout-base@2.0.1/layout-base.js",
    "https://cdn.jsdelivr.net/npm/cose-base@2.2.0/cose-base.js",
    "https://cdn.jsdelivr.net/npm/cytoscape-cose-bilkent@4.1.0/cytoscape-cose-bilkent.js",
    "https://cdn.jsdelivr.net/npm/cytoscape-svg@0.4.0/cytoscape-svg.js",
]


def get_widget_js() -> str:
    """Return the KGWidget JavaScript source."""
    return (_HERE / "kg_widget.js").read_text(encoding="utf-8")


def get_widget_css() -> str:
    """Return the KGWidget CSS source."""
    return (_HERE / "kg_widget.css").read_text(encoding="utf-8")


def _json_for_script(value: Any) -> str:
    """JSON that is safe inside a ``<script>`` element: a ``</script>`` in a label cannot end the element."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


def build_standalone_html(
    kg_data: dict | str,
    title: str = "Knowledge Graph",
    opts: dict[str, Any] | None = None,
) -> str:
    """Build a complete standalone HTML page embedding the KGWidget.

    The widget's own JS and CSS are inlined; Cytoscape and its layout extensions load from a CDN, so the
    page needs network access.

    Parameters
    ----------
    kg_data:
        The KG JSON object ``{"nodes": [...], "edges": [...]}`` or its
        serialised form.
    title:
        Page / widget title displayed in the toolbar.
    opts:
        Widget options forwarded to ``new KGWidget(el, data, opts)`` (or
        ``new KGGraph(...)`` when ``component='graph'``).
        Keys: ``panelMode`` ('open'|'closed'), ``layout``, ``maxNodes``,
        ``title``, ``filterStyle`` ('flat'|'layered'),
        ``component`` ('widget'|'graph').
    """
    if isinstance(kg_data, str):
        kg_data = json.loads(kg_data)

    merged_opts: dict[str, Any] = {"panelMode": "open", "layout": "dagre", "title": title}
    if opts:
        merged_opts.update(opts)

    kg_json = _json_for_script(kg_data)
    opts_json = _json_for_script(merged_opts)

    cdn_scripts = "\n".join(f'<script src="{url}"></script>' for url in _CDN)
    widget_tags = f"<style>{get_widget_css()}</style><script>{get_widget_js()}</script>"

    node_count = len(kg_data.get("nodes", []))
    edge_count = len(kg_data.get("edges", []))
    subtitle = f"{node_count} nodes · {edge_count} edges"
    page_title = html.escape(title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{page_title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d1117; color: #e2e8f0; font-family: 'Inter', 'Segoe UI', sans-serif;
      height: 100vh; overflow: hidden; display: flex; flex-direction: column;
    }}
    #kg-page-header {{
      flex-shrink: 0; padding: 8px 14px; background: #161b22; border-bottom: 1px solid #21262d;
      display: flex; align-items: center; gap: 12px;
    }}
    #kg-page-header h1 {{ font-size: 14px; font-weight: 600; color: #e6edf3; }}
    #kg-page-header .sub {{ font-size: 11px; color: #6e7681; }}
    #kg-root {{ flex: 1; min-height: 0; }}
  </style>
  {cdn_scripts}
  {widget_tags}
</head>
<body class="kg-widget-page">
  <div id="kg-page-header">
    <h1>{page_title}</h1>
    <span class="sub">{subtitle}</span>
  </div>
  <div id="kg-root"></div>
  <script>
    (function () {{
      var kg   = {kg_json};
      var opts = {opts_json};
      var root = document.getElementById('kg-root');
      // Give Cytoscape layout extensions time to register
      var Ctor = (opts.component === 'graph') ? window.KGGraph : window.KGWidget;
      window._kgWidget = new Ctor(root, kg, opts);
    }})();
  </script>
</body>
</html>"""


def serve_and_open(
    kg_data: dict | str,
    title: str = "Knowledge Graph",
    opts: dict[str, Any] | None = None,
    port: int = 0,
) -> None:
    """Generate the widget HTML and open it in the default browser.

    Opens a temporary in-process HTTP server on ``127.0.0.1:port`` (0 = random free
    port) and launches the system browser.  Blocks until Ctrl-C.
    """
    page = build_standalone_html(kg_data, title=title, opts=opts).encode("utf-8")

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/", "/index.html"):
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt: str, *args: Any) -> None:  # silence the access log
            pass

    with socketserver.TCPServer(("127.0.0.1", port), _Handler) as httpd:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/"
        print(f"  KG viewer → {url}  (Ctrl-C to stop)")
        # the browser is started off this thread: some of them block until they close
        new_thread(target=lambda: webbrowser.open(url), daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.debug("viewer stopped", exc_info=True)
