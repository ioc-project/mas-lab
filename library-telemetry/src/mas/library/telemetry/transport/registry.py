#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""In-process map of instrumentation scope → classifier. Not a plugin registry.

Classifiers are constructed by the caller (the observability binder) from the
existing plugin catalog and passed in. This module only routes by scope name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mas.runtime.transport.classifier import TransportClassifier


@dataclass
class ClassifierSet:
    """Scope-name routing table. Empty scopes are skipped (hooks-only plugins)."""

    classifiers: tuple[TransportClassifier, ...] = ()
    _by_scope: dict[str, TransportClassifier] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        by_scope: dict[str, TransportClassifier] = {}
        for classifier in self.classifiers:
            for scope in classifier.scope_names():
                by_scope[scope] = classifier
        self._by_scope = by_scope

    def for_scope(self, scope_name: str) -> TransportClassifier | None:
        return self._by_scope.get(scope_name)

    def ids(self) -> tuple[str, ...]:
        return tuple(type(c).__name__ for c in self.classifiers)


__all__ = ["ClassifierSet"]
