#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library-kg source must not import or name a private ontology package."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "mas" / "library" / "kg"
_BANNED_SHA256 = {
    "2e6318f26780f21f779066fd01327dc71676484513168222794373fe95987a0b",
    "a826b8f6c9826ebd8586d48d1eede983685455118f38cf9a5c7173d9fd02b3ba",
    "8a9887469af7a9130f8b9857f86b555c5a98040fec3ffa5dc05d4fa02f5e197d",
    "d26f708fb7b3535252c549ffd0a66075bf0c0ebb1fc14b63aba45cbbb75cd338",
    "95b29ebf7d02e5cc6843ddf8dfc55faebcd51e395cc4390ee770de9ffad1d959",
    "7caed82e7fdac9b6416007fc00378d02fe8eef40365f4e61b8daab7f1f3c681e",
}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{7,}")


def test_no_private_package_names_in_library_kg_src() -> None:
    hits: list[str] = []
    for path in _SRC.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".md", ".yaml", ".yml", ".toml", ".json", ".ttl"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in set(_TOKEN_RE.findall(text)):
            digest = hashlib.sha256(token.encode()).hexdigest()
            if digest in _BANNED_SHA256:
                hits.append(str(path.relative_to(_SRC.parent.parent.parent)))
    assert hits == [], "private package names leaked into library-kg:\n" + "\n".join(hits)
