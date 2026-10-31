"""Bearer-token auth + bind policy for the MCP HTTP endpoint.

  * Default bind is 127.0.0.1. Binding anything else (0.0.0.0, a LAN address,
    a hostname) REQUIRES a token; the server refuses to start without one.
  * If a token is configured it is required on every HTTP request, loopback
    included (Authorization: Bearer <token>), compared in constant time.
  * The token comes from the environment, never from the TOML or the repo:
        MCP_AUTH_TOKEN        the token itself, or
        MCP_AUTH_TOKEN_FILE   path to a file holding it (chmod 600; preferred,
                              keeps it out of `ps` and shell history)
  * stdio transport has no network surface, so none of this applies there.

Pure ASGI, no new dependency (starlette/uvicorn already ship with `mcp`).
"""
from __future__ import annotations

import hmac
import ipaddress
import os
from typing import Mapping, Optional

MIN_TOKEN_LEN = 16


class AuthConfigError(RuntimeError):
    """Refusing to start: unsafe or unusable auth configuration."""


def is_loopback(host: str) -> bool:
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False                 # a hostname or empty string: treat as exposed


def load_token(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Token from MCP_AUTH_TOKEN_FILE, else MCP_AUTH_TOKEN, else None."""
    env = os.environ if env is None else env
    path = (env.get("MCP_AUTH_TOKEN_FILE") or "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                token = fh.read().strip()
        except OSError as e:
            raise AuthConfigError(f"MCP_AUTH_TOKEN_FILE unreadable: {e.strerror}") from None
        if not token:
            raise AuthConfigError("MCP_AUTH_TOKEN_FILE is empty")
    else:
        token = (env.get("MCP_AUTH_TOKEN") or "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LEN:
        raise AuthConfigError(f"MCP auth token shorter than {MIN_TOKEN_LEN} characters")
    return token


def check_exposure(host: str, token: Optional[str]) -> None:
    """Raise AuthConfigError if `host` is reachable from elsewhere and has no token."""
    if not is_loopback(host) and not token:
        raise AuthConfigError(
            f"refusing to bind MCP on non-loopback host {host!r} without a bearer token: "
            "set MCP_AUTH_TOKEN_FILE (or MCP_AUTH_TOKEN), or bind 127.0.0.1")


class BearerAuthMiddleware:
    """ASGI middleware: 401 unless `Authorization: Bearer <token>` matches."""

    def __init__(self, app, token: str):
        if not token:
            raise AuthConfigError("empty token")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)             # lifespan etc.
            return
        supplied = b""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                scheme, _, rest = v.partition(b" ")
                if scheme.lower() == b"bearer":
                    supplied = rest.strip()
                break
        if hmac.compare_digest(supplied, self._token):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"error":"unauthorized"}'
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="mavlink-mcp"')]})
        await send({"type": "http.response.body", "body": body})


def protect(app, token: Optional[str]):
    """Wrap `app` when a token is configured, else return it unchanged."""
    return BearerAuthMiddleware(app, token) if token else app
