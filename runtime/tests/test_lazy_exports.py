#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""``lazy_exports``: a name loads its module on first read, is cached, and an unknown name is an AttributeError."""

from __future__ import annotations

import pytest
from mas.runtime.lazy import lazy_exports


def _package() -> tuple[dict[str, object], object, object]:
    namespace: dict[str, object] = {"present": 1}
    getter, lister = lazy_exports("pkg", namespace, {"join": "os.path"})
    return namespace, getter, lister


def test_first_read_imports_the_module_and_caches_the_value() -> None:
    namespace, getter, _ = _package()
    assert "join" not in namespace
    value = getter("join")
    assert value("a", "b") == "a/b"
    assert namespace["join"] is value


def test_an_unknown_name_is_an_attribute_error_naming_the_package() -> None:
    _, getter, _ = _package()
    with pytest.raises(AttributeError, match="module 'pkg' has no attribute 'nope'"):
        getter("nope")


def test_dir_lists_the_namespace_and_the_exports_once() -> None:
    namespace, getter, lister = _package()
    assert lister() == ["join", "present"]
    getter("join")
    assert lister() == ["join", "present"]


def test_the_runtime_roots_expose_their_exports_lazily() -> None:
    import mas.runtime as runtime
    import mas.runtime.kernel as kernel

    assert {"RuntimeKernel", "Session"} <= set(dir(runtime))
    assert kernel.RuntimeKernel is runtime.RuntimeKernel
    with pytest.raises(AttributeError):
        runtime.not_an_export  # noqa: B018
