#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The credential environment variables the code base knows, and the secret each one becomes."""

from __future__ import annotations

from types import MappingProxyType

KNOWN_ENV_NAMES = MappingProxyType(
    {
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
        "GEMINI_API_KEY": "gemini",
        "AZURE_OPENAI_API_KEY": "azure",
        "AWS_BEARER_TOKEN_BEDROCK": "bedrock",
        "VERTEX_ACCESS_TOKEN": "vertex",
        "GROQ_API_KEY": "groq",
        "OPENROUTER_API_KEY": "openrouter",
        "XAI_API_KEY": "xai",
        "LITELLM_API_KEY": "litellm",
        "CEREBRAS_API_KEY": "cerebras",
        "MISTRAL_API_KEY": "mistral",
        "VLLM_API_KEY": "vllm",
        "HF_TOKEN": "hf",
        "DATABRICKS_TOKEN": "databricks",
        "OPENRESPONSES_API_KEY": "openresponses",
        "NEO4J_PASSWORD": "neo4j",
        "CLICKHOUSE_PASSWORD": "clickhouse",
    }
)
