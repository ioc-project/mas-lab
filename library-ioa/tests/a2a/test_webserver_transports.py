# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Native exposure validates plugin transports before constructing serving resources."""

from unittest.mock import Mock

import pytest
from library_ioa.plugins.a2a.exposure import A2AExposure
from library_ioa.plugins.webserver import UvicornWebServer


@pytest.mark.parametrize("binding", [None, {"type": "custom", "options": {"example": True}}])
def test_endpoint_selects_registered_webserver(monkeypatch, binding):
    registry = Mock()
    monkeypatch.setattr("mas.runtime.registry.get_registry", lambda: registry)
    endpoint = {"protocol": "a2a", "usage": "listen", "url": "http://localhost:9003"}
    if binding:
        endpoint["a2a"] = {"webserver": binding}
    exposure = A2AExposure(endpoint=endpoint)
    registry.create.assert_called_once_with("webserver", binding=binding or {"type": "uvicorn"})
    assert exposure._webserver is registry.create.return_value


@pytest.mark.parametrize("entry", ["build_app", "serve_background", "serve_blocking"])
@pytest.mark.parametrize("grpc", [{"grpc_port": 9004}, {"grpc_url": "localhost:9004"}])
def test_validation_precedes_cards_handlers_and_listeners(monkeypatch, entry, grpc):
    calls = []

    class HttpOnly(UvicornWebServer):
        def validate_transports(self, transports):
            calls.append(transports)
            if "grpc" in transports:
                raise ValueError("HTTP only")

    card = Mock(side_effect=AssertionError("card constructed before validation"))
    monkeypatch.setattr("library_ioa.plugins.a2a.exposure.agent_card_from_manifest", card)
    server = HttpOnly()
    server.serve_blocking = Mock()
    server.serve_background = Mock()
    handler = Mock()
    exposure = A2AExposure(
        endpoint={"protocol": "a2a", "usage": "listen", "url": "http://localhost:9003", "a2a": grpc},
        webserver=server,
    )
    with pytest.raises(ValueError, match="HTTP only"):
        getattr(exposure, entry)({}, handler)
    assert calls == [frozenset({"http", "grpc"})]
    card.assert_not_called()
    handler.assert_not_called()
    server.serve_blocking.assert_not_called()
    server.serve_background.assert_not_called()


def test_direct_configuration_and_optional_validation():
    server = UvicornWebServer()
    A2AExposure(webserver=server, grpc_port=9004).validate_transports()
    server.validate_transports = Mock()
    A2AExposure(webserver=server).validate_transports()
    server.validate_transports.assert_called_once_with(frozenset({"http"}))
    A2AExposure(webserver=server, grpc_port=9004).validate_transports()
    server.validate_transports.assert_called_with(frozenset({"http", "grpc"}))


def test_selected_server_preserves_https_listen_validation():
    with pytest.raises(ValueError, match="listen"):
        A2AExposure(
            endpoint={"protocol": "a2a", "usage": "listen", "url": "https://example.test"},
            webserver=UvicornWebServer(),
        )


@pytest.mark.asyncio
async def test_push_retains_configured_authentication():
    import httpx
    from a2a.server.tasks import InMemoryPushNotificationConfigStore
    from a2a.types import Task, TaskPushNotificationConfig
    from library_ioa.plugins.a2a.exposure import A2APushNotificationSender

    requests = []

    async def receive(request):
        requests.append(request)
        return httpx.Response(200)

    sender = A2APushNotificationSender(
        httpx.AsyncClient(transport=httpx.MockTransport(receive)), InMemoryPushNotificationConfigStore()
    )
    config = TaskPushNotificationConfig(url="https://example.test/callback")
    config.authentication.scheme = "Bearer"
    config.authentication.credentials = "test-token"
    assert await sender._dispatch_notification(Task(id="task"), config, "task")
    assert requests[0].headers["Authorization"] == "Bearer test-token"
    await sender.aclose()
