#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The host environment keys MAS Lab names, each written once. None of them is a credential.

This module imports nothing but the standard library. :mod:`mas.runtime.hostenv` keeps ``MAS_*``
(not ``MAS_SECRET_*``) and :data:`DECLARED_KEYS`; :func:`mas.runtime.secrets.child_env` hands every child
:data:`CHILD_KEYS`, and a lab or ctl child (``lab=True``) :data:`LAB_KEYS`, :data:`PROCESS_KEEP` and the keys
a library declared with :func:`declare_lab_keys` as well.
"""

from __future__ import annotations

from collections.abc import Iterable

# What a program needs to run at all.
BASE_KEYS = frozenset(
    "PATH HOME LANG LC_ALL LC_CTYPE LC_MESSAGES TMPDIR TEMP TMP TERM SYSTEMROOT COMSPEC PATHEXT USERPROFILE "
    "WINDIR".split()
)

# Every child: run, trust a TLS server (CA bundles) and reach the network (proxies) as the host does.
CHILD_KEYS = BASE_KEYS | frozenset(
    "SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE NO_PROXY no_proxy "
    "HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy".split()
)

# A lab or ctl child on top: XDG and user, Python/uv/conda tooling, logging, the OpenAI-compatible address
# (the key comes from the secret store), the lab services' addresses and names (never a password), the controller.
LAB_KEYS = frozenset(
    "XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME XDG_STATE_HOME XDG_RUNTIME_DIR PWD USER LOGNAME "
    "VIRTUAL_ENV VIRTUAL_ENV_PROMPT PYTHONPATH PYTHONDONTWRITEBYTECODE PYTHONUNBUFFERED LD_LIBRARY_PATH "
    "UV_PROJECT_ENVIRONMENT UV_PYTHON UV_CACHE_DIR UV_NATIVE_TLS UV_OFFLINE CONDA_PREFIX "
    "LOG_LEVEL LOG_FORMAT OPENAI_BASE_URL OPENAI_API_BASE "
    "CLICKHOUSE_DATABASE CLICKHOUSE_HOST CLICKHOUSE_PORT CLICKHOUSE_USER NEO4J_DB_AGENT NEO4J_URI NEO4J_USER "
    "HOOKS METRICS_HOST METRICS_INCLUDE_BASIC METRICS_PORT OTLP_LOCAL_HOST OTLP_LOCAL_LOG OTLP_LOCAL_PORT "
    "UI_FEED_PATH UI_RUN_ID UI_RUN_ID_FILE MAS_HOME MAS_WORKSPACE_ROOT MAS_CONTROLLER_DIR MAS_CONTROLLER_SOCKET "
    "MAS_CONTROLLER_PID MAS_CONTROLLER_LOG MAS_CONTROLLER_NO_HTTP MAS_CONTROLLER_IDLE_SEC MAS_CONTROLLER_PORT "
    "MAS_CONTROLLER_VERSION_CHECK".split()
)

# The LLM model, cache and HTTP retry keys: a controller compares them with its client's, a ``.env`` may set them.
# The pacing keys (``MAS_LLM_RPM`` and the rest) belong to the pacing middleware's library, which declares them
# with :func:`declare_lab_keys`.
LLM_KEYS = frozenset(
    "MAS_CTL_MODEL MAS_LLM_CACHE MAS_LLM_HTTP_RETRIES MAS_LLM_HTTP_RETRY_BACKOFF MAS_LLM_MODEL".split()
)

# What the scrubbed ``os.environ`` keeps for third-party code that reads it (the OpenTelemetry, observe and
# A2A SDKs). ``OTEL_EXPORTER_OTLP_*HEADERS`` carry credentials and are not among them.
PROCESS_KEEP = LLM_KEYS | frozenset(
    "OBSERVE_REALTIME_OBSERVABILITY_ENABLED OTEL_SDK_DISABLED OTEL_SERVICE_NAME OTEL_RESOURCE_ATTRIBUTES "
    "OTEL_PROPAGATORS OTEL_TRACES_EXPORTER OTEL_METRICS_EXPORTER OTEL_LOGS_EXPORTER OTEL_TRACES_SAMPLER "
    "OTEL_TRACES_SAMPLER_ARG OTEL_LOG_LEVEL OTEL_INSTRUMENTATION_A2A_SDK_ENABLED OTEL_TRACES_PATH "
    "OTEL_EXPORTER_OTLP_ENDPOINT OTEL_EXPORTER_OTLP_TRACES_ENDPOINT OTEL_EXPORTER_OTLP_METRICS_ENDPOINT "
    "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT OTEL_EXPORTER_OTLP_PROTOCOL OTEL_EXPORTER_OTLP_TRACES_PROTOCOL "
    "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL OTEL_EXPORTER_OTLP_LOGS_PROTOCOL OTEL_EXPORTER_OTLP_CERTIFICATE "
    "OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE OTEL_EXPORTER_OTLP_METRICS_CERTIFICATE "
    "OTEL_EXPORTER_OTLP_LOGS_CERTIFICATE OTEL_EXPORTER_OTLP_COMPRESSION OTEL_EXPORTER_OTLP_TIMEOUT "
    "OTEL_EXPORTER_OTLP_INSECURE".split()
)

DECLARED_KEYS = CHILD_KEYS | LAB_KEYS | PROCESS_KEEP

# Keys a library names for itself, added to what a lab child receives. Empty until an entry point declares some.
_DECLARED_BY_LIBRARIES: set[str] = set()


def declare_lab_keys(keys: Iterable[str]) -> None:
    """Forward *keys* from the host to every lab child, as :data:`LAB_KEYS` are.

    For a library that owns host variables (the pacing middleware's ``MAS_LLM_*``). They must not be
    credentials: a credential goes through the secret store. Call it from an entry point or from the code the
    entry point runs, once; declaring a key twice is harmless.
    """
    _DECLARED_BY_LIBRARIES.update(str(key) for key in keys)


def declared_lab_keys() -> frozenset[str]:
    """The keys libraries declared with :func:`declare_lab_keys`."""
    return frozenset(_DECLARED_BY_LIBRARIES)


# URL-valued keys every process hands on (proxies, OTLP endpoints, the lab's Neo4j, the OpenAI-compatible
# address): one with credentials is refused.
CREDENTIAL_FREE_URLS = frozenset(
    k for k in DECLARED_KEYS if k.upper().endswith(("_PROXY", "_ENDPOINT", "_URI")) and k.upper() != "NO_PROXY"
) | {"OPENAI_BASE_URL", "OPENAI_API_BASE"}
