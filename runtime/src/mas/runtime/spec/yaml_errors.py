#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""One YAML loader for manifests and one error text that never quotes the document.

``str()`` of a PyYAML error prints the offending source line or the tag it read: either may be a secret
written in the file. Every loader that reports a YAML error shows :func:`yaml_error_text` instead.
"""

from __future__ import annotations

import yaml

# Leads a reported load error everywhere a user reads one: ``f"{YAML_ERROR_PREFIX} {yaml_error_text(exc)}"``.
YAML_ERROR_PREFIX = "YAML does not parse:"


class YamlLoadError(yaml.YAMLError, ValueError):
    """A YAML document did not load; the message is :func:`yaml_error_text`, never the document."""


class _Refused(yaml.MarkedYAMLError):
    """A construct the manifest loader refuses; ``problem`` is fixed text."""


class _ManifestLoader(yaml.SafeLoader):
    """``SafeLoader`` without ``!!set``, ``!!omap`` and ``!!pairs`` (their values sit under no mapping key).
    An unknown tag is refused, as ``SafeLoader`` does. Aliases load; the manifest walker caps their expanded size."""


def _refuse_tag(_loader: yaml.SafeLoader, node: yaml.Node) -> object:
    raise _Refused(problem="YAML tag not allowed", problem_mark=node.start_mark)


for _tag in ("set", "omap", "pairs"):
    _ManifestLoader.add_constructor(f"tag:yaml.org,2002:{_tag}", _refuse_tag)


def yaml_error_text(exc: BaseException) -> str:
    """A fixed problem class and ``(line L, column C)``; the type name for a non-YAML error (a tag
    constructor's ``ValueError`` quotes the value). Never the source line, never a token of the document."""
    if not isinstance(exc, yaml.YAMLError):
        return type(exc).__name__
    if isinstance(exc, _Refused):
        text = exc.problem
    elif isinstance(exc, yaml.composer.ComposerError):
        text = "YAML cannot be composed"
    elif isinstance(exc, yaml.constructor.ConstructorError):
        text = "YAML tag not allowed"
    elif isinstance(exc, (yaml.scanner.ScannerError, yaml.parser.ParserError)):
        text = "invalid YAML syntax"
    else:
        text = "invalid YAML"
    mark = getattr(exc, "problem_mark", None)
    return f"{text} (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else text


def safe_yaml_load(text: str, *, source: str = "") -> object:
    """Load one document with the manifest loader; any failure is raised as :class:`YamlLoadError`
    (*source* leads the message) with no cause attached (a traceback would print the original message)."""
    try:
        return yaml.load(text, Loader=_ManifestLoader)  # noqa: S506 - a SafeLoader subclass
    except Exception as exc:  # a tag constructor raises ValueError, TypeError, ... quoting the value
        message = yaml_error_text(exc)
    raise YamlLoadError(f"{source}: {message}" if source else message)
