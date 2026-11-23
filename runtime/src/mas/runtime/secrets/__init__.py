#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""Secret contracts. The runtime defines types; stores are plugins. Anything else: its submodule."""

from mas.runtime.secrets.errors import SecretDenied, SecretError, SecretMissing, SecretRefError
from mas.runtime.secrets.loopback import is_loopback_host
from mas.runtime.secrets.manifest import check_manifest, load_checked_document, prepare_manifest
from mas.runtime.secrets.process import child_env
from mas.runtime.secrets.ref import SecretRef
from mas.runtime.secrets.resolver import NullResolver, SecretResolver, current_resolver, set_process_resolver
from mas.runtime.secrets.store import SecretStore
from mas.runtime.secrets.transport import check_plain_http
from mas.runtime.secrets.value import SecretValue

__all__ = [
    "NullResolver",
    "SecretDenied",
    "SecretError",
    "SecretMissing",
    "SecretRef",
    "SecretRefError",
    "SecretResolver",
    "SecretStore",
    "SecretValue",
    "check_manifest",
    "check_plain_http",
    "child_env",
    "current_resolver",
    "is_loopback_host",
    "load_checked_document",
    "prepare_manifest",
    "set_process_resolver",
]
