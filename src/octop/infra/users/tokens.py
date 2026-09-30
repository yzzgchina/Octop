"""JWT access-token signing.

The signing primitive is pure stdlib + ``jwt`` and carries no HTTP concern, so
it belongs to the domain layer: ``infra/`` must never import ``api/``
(AGENTS.md §5), and :mod:`octop.infra.server` needs it to mint the user token
the bridge advertises to its peer.

:mod:`octop.api.deps` re-exports ``sign_token`` for the existing API call
sites, so their imports and the public signature are unchanged.
"""

from __future__ import annotations

import time

import jwt


def sign_token(
    secret: bytes,
    *,
    sub: int,
    uname: str,
    role: str,
    ttl_seconds: int = 86400,
) -> str:
    now = int(time.time())
    payload = {
        "sub": str(sub),
        "uname": uname,
        "role": role,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, secret, algorithm="HS256")
