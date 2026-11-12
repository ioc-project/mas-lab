#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
from mas.ctl.infra.models import InfraManifest


def test_load_llm_local_manifest_maps_server_and_available_models(tmp_path) -> None:
    manifest_path = tmp_path / "ollama-local.yaml"
    manifest_path.write_text(
        """
apiVersion: infra/v1
kind: LLMLocal
metadata:
  name: ollama-local
spec:
  server:
    api_base: http://localhost:11434/v1
    api_key_env: ""
  models:
    available:
      - qwen2.5:latest
      - nomic-embed-text:latest
    defaults:
      llm: qwen2.5:latest
      embed: nomic-embed-text:latest
""".strip()
        + "\n",
        encoding="utf-8",
    )

    infra = InfraManifest.load(manifest_path)

    assert infra.kind == "LLMLocal"
    assert infra.proxy.api_base == "http://localhost:11434/v1"
    assert infra.proxy.api_key_env == ""
    assert infra.models.allowed == ["qwen2.5:latest", "nomic-embed-text:latest"]
    assert infra.models.defaults.llm == "qwen2.5:latest"
    assert infra.models.defaults.embed == "nomic-embed-text:latest"


def test_llm_proxy_yaml_retry_is_not_a_proxy_field() -> None:
    infra = InfraManifest.from_document(
        {
            "apiVersion": "infra/v1",
            "kind": "LLMProxy",
            "metadata": {"name": "retry-leaf"},
            "spec": {
                "proxy": {
                    "api_base": "https://x.example/v1",
                    "api_key_env": "X_KEY",
                    "retry": {"max_attempts": 9, "jitter": False},
                }
            },
        }
    )
    proxy = infra.to_llm_proxy_dict()
    assert "retry" not in proxy
    assert "retry" not in infra.to_document()["spec"]["proxy"]
