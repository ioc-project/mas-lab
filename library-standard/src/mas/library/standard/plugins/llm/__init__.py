#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""library-standard LLM-provider plugins (wire protocol + cache decorator)."""

from mas.library.standard.plugins.llm.anthropic import AnthropicLLMProvider
from mas.library.standard.plugins.llm.azure import AzureLLMProvider
from mas.library.standard.plugins.llm.bedrock import BedrockLLMProvider
from mas.library.standard.plugins.llm.cache import CacheLLMProvider
from mas.library.standard.plugins.llm.compatible import (
    CerebrasLLMProvider,
    DatabricksLLMProvider,
    GroqLLMProvider,
    HuggingFaceLLMProvider,
    LiteLLMLLMProvider,
    MistralLLMProvider,
    OpenRouterLLMProvider,
    VllmLLMProvider,
    XaiLLMProvider,
)
from mas.library.standard.plugins.llm.gemini import GeminiLLMProvider
from mas.library.standard.plugins.llm.openai import OpenAILLMProvider
from mas.library.standard.plugins.llm.openresponses import OpenResponsesLLMProvider
from mas.library.standard.plugins.llm.vertex import VertexLLMProvider

__all__ = [
    "AnthropicLLMProvider",
    "AzureLLMProvider",
    "BedrockLLMProvider",
    "CacheLLMProvider",
    "CerebrasLLMProvider",
    "DatabricksLLMProvider",
    "GeminiLLMProvider",
    "GroqLLMProvider",
    "HuggingFaceLLMProvider",
    "LiteLLMLLMProvider",
    "MistralLLMProvider",
    "OpenAILLMProvider",
    "OpenResponsesLLMProvider",
    "OpenRouterLLMProvider",
    "VertexLLMProvider",
    "VllmLLMProvider",
    "XaiLLMProvider",
]
