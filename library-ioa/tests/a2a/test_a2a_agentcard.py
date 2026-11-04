from __future__ import annotations

from library_ioa.plugins.a2a.agentcard import agent_card_from_manifest
from library_ioa.plugins.a2a.exposure import A2AExposure


def test_agent_card_is_derived_from_manifest() -> None:
    card = agent_card_from_manifest(
        {
            "metadata": {
                "name": "qa-agent",
                "version": "1.2.3",
            },
            "spec": {
                "description": "Answer questions.",
                "skills": ["general-knowledge"],
                "provider": {"organization": "Demo Org"},
            },
        },
        url="http://127.0.0.1:9005",
        grpc_url="127.0.0.1:9006",
    )

    assert card.name == "qa-agent"
    assert card.description == "Answer questions."
    assert card.version == "1.2.3"
    assert card.provider.organization == "Demo Org"
    assert card.skills[0].id == "general-knowledge"
    assert card.supported_interfaces[0].url == "http://127.0.0.1:9005"
    assert card.supported_interfaces[2].protocol_binding == "GRPC"
    assert card.supported_interfaces[2].url == "127.0.0.1:9006"
    assert not card.capabilities.streaming


def test_webserver_is_resolved_as_a_runtime_plugin() -> None:
    from mas.runtime.registry import get_registry

    webserver = get_registry().create("webserver", binding={"type": "uvicorn"})

    assert webserver.__class__.__name__ == "UvicornWebServer"


def test_agent_card_capabilities_use_infra_overrides_with_safe_defaults() -> None:
    manifest = {"metadata": {"name": "qa-agent"}, "spec": {}}
    defaults = agent_card_from_manifest(manifest, url="http://127.0.0.1:9005")
    configured = agent_card_from_manifest(
        manifest,
        url="http://127.0.0.1:9005",
        capabilities={
            "streaming": True,
            "pushNotifications": True,
            "extensions": [{"uri": "urn:example:extension", "required": True}],
        },
    )

    assert not defaults.capabilities.streaming
    assert not defaults.capabilities.push_notifications
    assert not defaults.capabilities.extended_agent_card
    assert configured.capabilities.streaming
    assert configured.capabilities.push_notifications
    assert configured.capabilities.extensions[0].uri == "urn:example:extension"


def test_a2a_exposure_uses_application_for_bind_card_and_push() -> None:
    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "url": "http://agents.example.test:9005",
            "usage": "listen",
            "a2a": {
                "listen": {"host": "0.0.0.0", "port": 8005},
                "grpc_port": 9006,
                "capabilities": {
                    "streaming": True,
                    "pushNotifications": True,
                },
            },
        },
        webserver=object(),
    )
    app = exposure.build_app(
        {"metadata": {"name": "qa-agent"}, "spec": {}},
        lambda prompt, **_: {"text": prompt},
    )

    assert (exposure.host, exposure.port, exposure.grpc_port) == (
        "0.0.0.0",
        8005,
        9006,
    )
    card = app.state.a2a_agent_card
    assert card.supported_interfaces[0].url == "http://agents.example.test:9005"
    assert card.supported_interfaces[2].url == "agents.example.test:9006"
    assert card.capabilities.streaming
    assert card.capabilities.push_notifications
    assert app.state.a2a_request_handler._push_config_store is not None
    assert app.state.a2a_request_handler._push_sender is not None


def test_a2a_exposure_requires_listen_usage() -> None:
    import pytest

    with pytest.raises(ValueError, match="usage: listen or \[connect, listen\]"):
        A2AExposure(
            endpoint={
                "protocol": "a2a",
                "url": "http://127.0.0.1:9005",
                "usage": "connect",
                "expose": True,
            },
            webserver=object(),
        )


def test_a2a_exposure_uses_application_endpoint_for_bind_and_card() -> None:
    exposure = A2AExposure(
        endpoint={
            "protocol": "a2a",
            "url": "http://agents.example.test:9005",
            "expose": True,
            "a2a": {
                "listen": {"host": "0.0.0.0", "port": 8005},
                "grpc_port": 9006,
                "grpc_url": "agents.example.test:9006",
                "capabilities": {"streaming": True},
            },
        },
        webserver=object(),
    )
    app = exposure.build_app(
        {"metadata": {"name": "qa-agent"}, "spec": {}},
        lambda prompt, **_: {"text": prompt},
    )

    assert (exposure.host, exposure.port, exposure.grpc_port) == (
        "0.0.0.0",
        8005,
        9006,
    )
    assert app.state.a2a_agent_card.supported_interfaces[0].url == (
        "http://agents.example.test:9005"
    )
    assert app.state.a2a_agent_card.supported_interfaces[2].url == (
        "agents.example.test:9006"
    )
    assert app.state.a2a_agent_card.capabilities.streaming