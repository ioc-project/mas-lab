# Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
# SPDX-License-Identifier: Apache-2.0
"""Standard ASGI authentication before native A2A context construction."""

import hmac
import json
import os
from pathlib import Path

from library_ioa.plugins.webserver import UvicornWebServer
from mas.library.enforcement_proxy import require_native_hooks
from mas.runtime.boundary.webserver import WebServerTransport
from starlette.authentication import AuthCredentials, AuthenticationBackend, AuthenticationError, SimpleUser
from starlette.middleware.authentication import AuthenticationMiddleware


class ConfiguredCallers(AuthenticationBackend):
    def __init__(self, config_file: str) -> None:
        config = json.loads(Path(config_file).read_text())
        self.credentials = {}
        for name, identity in config["identities"].items():
            if env := identity.get("a2a_credential_env"):
                secret = os.environ.get(env, "")
                if len(secret) < 32 or secret in self.credentials.values():
                    raise ValueError("A2A caller credentials must be present, distinct and at least 32 characters")
                self.credentials[name] = secret
        if not self.credentials:
            raise ValueError("Configure at least one A2A caller credential")

    async def authenticate(self, request):
        if request.url.path.startswith("/.well-known/"):
            return None
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            token = header[7:]
            for name, secret in self.credentials.items():
                if hmac.compare_digest(token.encode(), secret.encode()):
                    return AuthCredentials(["authenticated"]), SimpleUser(name)
        raise AuthenticationError("A2A caller authentication required")


class AuthenticatedWebServer(UvicornWebServer):
    def __init__(self, config_file: str) -> None:
        require_native_hooks()
        self.backend = ConfiguredCallers(config_file)

    def validate_transports(self, transports: frozenset[WebServerTransport]) -> None:
        if transports != frozenset({"http"}):
            raise ValueError("Authenticated A2A serving supports HTTP only; remove gRPC listeners and advertisements")

    def _app(self, app):
        app.add_middleware(AuthenticationMiddleware, backend=self.backend)
        return app

    def serve_blocking(self, app, *, host, port):
        return super().serve_blocking(self._app(app), host=host, port=port)

    def serve_background(self, app, *, host, port):
        return super().serve_background(self._app(app), host=host, port=port)

    async def serve_async(self, app, *, host, port):
        return await super().serve_async(self._app(app), host=host, port=port)
