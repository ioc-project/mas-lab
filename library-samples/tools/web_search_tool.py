#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Web search tool using DuckDuckGo with caching.

Provides real web search via duckduckgo-search, with disk caching to avoid
redundant queries.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from mas.runtime import hostenv
from mas.runtime.contracts import ToolContract

logger = logging.getLogger(__name__)


class WebSearchTool(ToolContract):
    """Web search tool backed by DuckDuckGo with disk cache."""

    def __init__(self, cache_dir: str = None, max_results: int = 5):
        super().__init__()
        if cache_dir is None:
            cache_dir = hostenv.get("MAS_WEB_SEARCH_CACHE", "").strip() or os.path.expanduser("~/.mas-cache/web_search")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_results = max_results
        self._offline = hostenv.get("MAS_WEB_SEARCH_OFFLINE", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    def get_name(self) -> str:
        return "web-search"

    def get_description(self) -> str:
        return "Search the web for current information. Returns real results from DuckDuckGo."

    def get_parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        }

    def _get_cache_key(self, query: str) -> str:
        return hashlib.sha256(query.lower().strip().encode()).hexdigest()

    def _get_cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.json"

    def _load_from_cache(self, query: str) -> Dict[str, Any] | None:
        cache_key = self._get_cache_key(query)
        cache_path = self._get_cache_path(cache_key)
        if cache_path.exists():
            try:
                with open(cache_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("Failed to load cache for %s: %s", query[:50], e)
        return None

    def _save_to_cache(self, query: str, result: Dict[str, Any]) -> None:
        cache_key = self._get_cache_key(query)
        cache_path = self._get_cache_path(cache_key)
        try:
            with open(cache_path, "w") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            logger.warning("Failed to cache result for %s: %s", query[:50], e)

    def _perform_search(self, query: str) -> Dict[str, Any]:
        """Perform a real web search via DuckDuckGo."""
        from ddgs import DDGS

        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=self.max_results))

        if not raw:
            return {"summary": f"No results found for '{query}'.", "results": []}

        results = []
        for r in raw:
            snippet = r.get("body", "")
            # Cap each snippet to ~200 chars to keep the prompt lean.
            if len(snippet) > 200:
                snippet = snippet[:200].rsplit(" ", 1)[0] + "…"
            results.append(
                {
                    "title": r.get("title", ""),
                    "snippet": snippet,
                    "url": r.get("href", ""),
                }
            )

        summary = "\n".join(f"[{i + 1}] {r['title']}: {r['snippet']} ({r['url']})" for i, r in enumerate(results))
        return {"summary": summary, "results": results}

    def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs.get("query", "").strip()
        if not query:
            return {"error": "Query parameter is required"}

        cached = self._load_from_cache(query)
        if cached is not None:
            cached["cached"] = True
            return cached
        if self._offline:
            raise RuntimeError(f"web-search cache miss while MAS_WEB_SEARCH_OFFLINE=1 for query {query!r}")

        try:
            result = self._perform_search(query)
            result["cached"] = False
            self._save_to_cache(query, result)
            return result
        except Exception as e:
            logger.error("Web search failed for '%s': %s", query, e)
            return {"error": str(e), "query": query}
