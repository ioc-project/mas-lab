"""Schema-backed command-line overrides implemented as synthetic overlays."""

from mas.ctl.overrides.apply import apply_cli_overrides, apply_root_overrides, overrides_for_root
from mas.ctl.overrides.bind import combine_overrides, expand_binds, expand_listens
from mas.ctl.overrides.model import OverridePath, ParsedOverride, PathSegment
from mas.ctl.overrides.parser import parse_override
from mas.ctl.overrides.shortcuts import max_tokens_overrides

__all__ = [
    "OverridePath",
    "ParsedOverride",
    "PathSegment",
    "apply_cli_overrides",
    "apply_root_overrides",
    "combine_overrides",
    "expand_binds",
    "expand_listens",
    "max_tokens_overrides",
    "overrides_for_root",
    "parse_override",
]
