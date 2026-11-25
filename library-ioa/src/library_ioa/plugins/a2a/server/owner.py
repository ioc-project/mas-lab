#  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
#  SPDX-License-Identifier: Apache-2.0
"""The user a request runs as once a verifying layer has said who is calling."""

from __future__ import annotations

from a2a.auth.user import User
from a2a.server.routes.common import DefaultServerCallContextBuilder
from starlette.authentication import BaseUser
from starlette.requests import Request


class VerifiedOwner(BaseUser, User):
    """The user of a request whose caller a verifying layer verified; ``user_name`` is the owner key.

    The SDK keeps tasks, and push-notification configs, per user name, so each verified owner gets its own. The
    conversation id a handler receives is made from this user: nothing else (a ``SlimUser`` from the transport, an SDK
    auth user, a user whose name merely looks like an owner key) switches that on.
    """

    def __init__(self, owner: str) -> None:
        self._owner = owner

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def display_name(self) -> str:
        return self._owner

    @property
    def user_name(self) -> str:
        return self._owner


class VerifiedOwnerContextBuilder(DefaultServerCallContextBuilder):
    """The SDK's call context, with the verified owner as the user itself: the SDK would wrap a Starlette user in an
    adapter, and the conversation key is made only from this class."""

    def build_user(self, request: Request) -> User:
        user = request.scope.get("user")
        if isinstance(user, VerifiedOwner):
            return user
        return super().build_user(request)
