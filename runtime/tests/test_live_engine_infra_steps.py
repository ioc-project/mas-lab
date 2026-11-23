#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""LiveLlmEngine.invoke / ainvoke run cache, fault, and scheduler steps."""

from __future__ import annotations

import asyncio
import threading

import pytest
from mas.runtime.engine.infra_pipeline import PipedLLMProvider
from mas.runtime.engine.llm_live import LiveLlmEngine
from mas.runtime.engine.llm_scheduler import install_overrides, reset_policy
from mas.runtime.schema.egress import InvokeEngineIo


class _StubProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def achat_completion(self, **kwargs: object) -> dict[str, str]:
        del kwargs
        self.calls += 1
        return {"role": "assistant", "content": f"live-{self.calls}"}


class _SlowProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.peak = 0
        self._inflight = 0
        self._lock = threading.Lock()

    async def achat_completion(self, **kwargs: object) -> dict[str, str]:
        del kwargs
        with self._lock:
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
            self.calls += 1
        await asyncio.sleep(0.12)
        with self._lock:
            self._inflight -= 1
        return {"role": "assistant", "content": "ok"}


class _BareProvider:
    async def achat_completion(self, **kwargs: object) -> dict[str, str]:
        del kwargs
        return {"role": "assistant", "content": "ok"}


def _engine(provider: object) -> LiveLlmEngine:
    return LiveLlmEngine(manifest=None, use_cache=False, llm_provider=provider)


def _io() -> InvokeEngineIo:
    return InvokeEngineIo(correlation_id=1, op="LLM_CALL")


async def test_live_engine_invoke_cache_hit_skips_provider(tmp_path) -> None:
    provider = _StubProvider()
    engine = _engine(provider)
    engine.install_infra_pipeline(
        [{"layer": "llm_cache", "params": {"cache_path": str(tmp_path / "cache.json")}}],
    )
    first = await engine.ainvoke(_io())
    second = await engine.ainvoke(_io())
    assert first.text == "live-1"
    assert second.text == "live-1"
    assert second.cache_status == "hit"
    assert provider.calls == 1


def test_live_engine_ainvoke_cache_hit_skips_provider(tmp_path) -> None:
    provider = _StubProvider()
    engine = _engine(provider)
    engine.install_infra_pipeline(
        [{"layer": "llm_cache", "params": {"cache_path": str(tmp_path / "cache.json")}}],
    )
    first = asyncio.run(engine.ainvoke(_io()))
    second = asyncio.run(engine.ainvoke(_io()))
    assert first.text == second.text == "live-1"
    assert second.cache_status == "hit"
    assert provider.calls == 1


async def test_live_engine_invoke_fault_rate_one_skips_provider() -> None:
    provider = _StubProvider()
    engine = _engine(provider)
    engine.install_infra_pipeline(
        [
            {
                "layer": "fault_inject",
                "per": "all",
                "params": {"rate": 1.0, "message": "injected"},
            }
        ],
    )
    ret = await engine.ainvoke(_io())
    assert ret.response_kind == "ERROR"
    assert "injected" in ret.text
    assert provider.calls == 0


def test_live_engine_ainvoke_fault_rate_one_skips_provider() -> None:
    provider = _StubProvider()
    engine = _engine(provider)
    engine.install_infra_pipeline(
        [
            {
                "layer": "fault_inject",
                "per": "all",
                "params": {"rate": 1.0, "message": "injected"},
            }
        ],
    )
    ret = asyncio.run(engine.ainvoke(_io()))
    assert ret.response_kind == "ERROR"
    assert "injected" in ret.text
    assert provider.calls == 0


@pytest.fixture(autouse=True)
def _clear_scheduler_overrides():
    yield
    install_overrides(None)


async def test_live_engine_invoke_scheduler_paces(tmp_path, monkeypatch) -> None:
    install_overrides({"state_path": str(tmp_path / "sched.json")})
    reset_policy()
    provider = _SlowProvider()
    engine = _engine(provider)
    engine.install_infra_pipeline(
        [
            {
                "layer": "llm_scheduler",
                "per": "all",
                "params": {
                    "max_in_flight": 1,
                    "requests_per_minute": 0,
                    "state_path": str(tmp_path / "sched.json"),
                    "timeout_s": 5,
                },
            }
        ],
    )
    errors: list[BaseException] = []

    async def _run() -> None:
        try:
            await engine.ainvoke(_io())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    await asyncio.gather(*(_run() for _ in range(3)))
    reset_policy()
    assert not errors
    assert provider.calls == 3
    assert provider.peak == 1


def test_install_infra_pipeline_refuses_destination_steps() -> None:
    engine = _engine(_StubProvider())
    with pytest.raises(ValueError, match="destination"):
        engine.install_infra_pipeline(
            [{"layer": "fault_inject", "params": {"rate": 1.0, "message": "injected"}}],
        )
    assert engine._infra_stack is None


def _leaf_with_scheduler(provider: object, tmp_path, *, keys: list[str] | None = None) -> PipedLLMProvider:
    params: dict[str, object] = {
        "max_in_flight": 1,
        "requests_per_minute": 0,
        "state_path": str(tmp_path / "sched.json"),
        "timeout_s": 5,
    }
    if keys is not None:
        params["keys"] = keys
    return PipedLLMProvider(
        inner=provider,
        pipeline=[{"layer": "llm_scheduler", "params": params}],
    )


async def _run_two_models(piped: PipedLLMProvider) -> list[BaseException]:
    errors: list[BaseException] = []

    async def _run(model: str) -> None:
        try:
            await piped.achat_completion(model=model, messages=[])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    await asyncio.gather(_run("model-j"), _run("model-k"))
    return errors


async def test_leaf_scheduler_two_models_get_separate_buckets(tmp_path, monkeypatch) -> None:
    install_overrides({"state_path": str(tmp_path / "sched.json")})
    reset_policy()
    provider = _SlowProvider()
    piped = _leaf_with_scheduler(provider, tmp_path)
    errors = await _run_two_models(piped)
    reset_policy()
    assert not errors
    assert provider.calls == 2
    assert provider.peak == 2


async def test_leaf_scheduler_keys_model_separates_buckets(tmp_path, monkeypatch) -> None:
    install_overrides({"state_path": str(tmp_path / "sched.json")})
    reset_policy()
    provider = _SlowProvider()
    piped = _leaf_with_scheduler(provider, tmp_path, keys=["model"])
    errors = await _run_two_models(piped)
    reset_policy()
    assert not errors
    assert provider.calls == 2
    assert provider.peak == 2


async def test_piped_provider_accepts_provider_without_kind() -> None:
    piped = PipedLLMProvider(
        inner=_BareProvider(),
        pipeline=[{"layer": "llm_scheduler", "params": {"max_in_flight": 1}}],
    )
    assert piped.kind == ""
    assert piped.api_base == ""
    assert piped.api_key == ""
    assert (await piped.achat_completion(model="m", messages=[]))["content"] == "ok"
