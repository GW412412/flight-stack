"""P1 #8: MCP HTTP endpoint is loopback by default and token-protected when
exposed. No real token appears in this file; they are generated per test."""
import secrets
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

import mcp_server
from mcp_auth import (AuthConfigError, BearerAuthMiddleware, check_exposure, is_loopback,
                      load_token, protect)
from mcp_server import build_server
from stack_config import ServerConfig, load_config

TOKEN = secrets.token_urlsafe(24)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("MCP_AUTH_TOKEN", "MCP_AUTH_TOKEN_FILE", "MCP_HOST", "DRONE_STACK_CONFIG"):
        monkeypatch.delenv(k, raising=False)


# ------------------------------------------------------------ policy helpers
@pytest.mark.parametrize("host,loop", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True),
    ("0.0.0.0", False), ("::", False), ("192.168.1.20", False), ("drone.local", False),
    ("", False)])
def test_is_loopback(host, loop):
    assert is_loopback(host) is loop


def test_default_bind_is_loopback():
    assert ServerConfig().host == "127.0.0.1"
    assert load_config().server.host == "127.0.0.1"


def test_exposed_bind_needs_a_token():
    check_exposure("127.0.0.1", None)                     # fine
    check_exposure("0.0.0.0", TOKEN)                      # fine
    with pytest.raises(AuthConfigError, match="without a bearer token"):
        check_exposure("0.0.0.0", None)
    with pytest.raises(AuthConfigError):
        check_exposure("192.168.1.20", None)


def test_token_from_env_and_file(monkeypatch, tmp_path):
    assert load_token() is None
    monkeypatch.setenv("MCP_AUTH_TOKEN", f"  {TOKEN}\n")
    assert load_token() == TOKEN
    f = tmp_path / "tok"
    f.write_text(TOKEN + "-file\n")
    monkeypatch.setenv("MCP_AUTH_TOKEN_FILE", str(f))
    assert load_token() == TOKEN + "-file"                # file wins over env


def test_bad_token_sources_are_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "short")
    with pytest.raises(AuthConfigError, match="shorter"):
        load_token()
    monkeypatch.delenv("MCP_AUTH_TOKEN")
    monkeypatch.setenv("MCP_AUTH_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(AuthConfigError, match="unreadable"):
        load_token()
    empty = tmp_path / "empty"
    empty.write_text("\n")
    monkeypatch.setenv("MCP_AUTH_TOKEN_FILE", str(empty))
    with pytest.raises(AuthConfigError, match="empty"):
        load_token()


def test_no_token_means_no_wrapping():
    app = object()
    assert protect(app, None) is app
    assert isinstance(protect(app, TOKEN), BearerAuthMiddleware)


# ------------------------------------------------------------ the middleware
def _echo_app():
    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


@pytest.mark.parametrize("hdr", [
    None, "", "Bearer", "Bearer ", "Bearer wrong", f"Basic {TOKEN}", TOKEN,
    f"Bearer {TOKEN}x", f"Bearer x{TOKEN}"])
def test_requests_without_the_exact_token_get_401(hdr):
    c = TestClient(protect(_echo_app(), TOKEN))
    r = c.get("/anything", headers={} if hdr is None else {"Authorization": hdr})
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert r.content != b"ok"


def test_correct_token_passes_and_scheme_is_case_insensitive():
    c = TestClient(protect(_echo_app(), TOKEN))
    assert c.get("/x", headers={"Authorization": f"Bearer {TOKEN}"}).text == "ok"
    assert c.get("/x", headers={"Authorization": f"bearer {TOKEN}"}).text == "ok"


# ------------------------------------------------------------ the real MCP app
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "t", "version": "0"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}


def test_real_mcp_endpoint_rejects_then_accepts():
    server = build_server(MagicMock(), host="127.0.0.1", port=8765)
    with TestClient(protect(server.streamable_http_app(), TOKEN),
                    base_url="http://127.0.0.1:8765") as c:
        assert c.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code == 401
        bad = {**MCP_HEADERS, "Authorization": "Bearer nope"}
        assert c.post("/mcp", json=INIT, headers=bad).status_code == 401
        good = {**MCP_HEADERS, "Authorization": f"Bearer {TOKEN}"}
        r = c.post("/mcp", json=INIT, headers=good)
        assert r.status_code == 200 and "serverInfo" in r.text


# ------------------------------------------------------------ startup policy
def run_main(*argv):
    return mcp_server.main(list(argv))


def test_server_refuses_to_start_exposed_without_token(capsys):
    with pytest.raises(SystemExit) as e:
        run_main("--transport", "streamable-http", "--host", "0.0.0.0")
    assert e.value.code == 2
    assert "bearer token" in capsys.readouterr().err


def test_env_mcp_host_cannot_expose_without_token(monkeypatch):
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    with pytest.raises(SystemExit):
        run_main("--transport", "sse")


def test_exposed_start_with_token_reaches_uvicorn(monkeypatch, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text(TOKEN)
    monkeypatch.setenv("MCP_AUTH_TOKEN_FILE", str(tok))
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setattr(mcp_server.DroneTools, "from_config", lambda cfg: MagicMock())
    run_main("--transport", "streamable-http", "--host", "0.0.0.0")
    assert isinstance(seen["app"], BearerAuthMiddleware) and seen["host"] == "0.0.0.0"


def test_loopback_start_without_token_is_unwrapped(monkeypatch):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setattr(mcp_server.DroneTools, "from_config", lambda cfg: MagicMock())
    run_main("--transport", "streamable-http")
    assert not isinstance(seen["app"], BearerAuthMiddleware) and seen["host"] == "127.0.0.1"


def test_stdio_needs_no_token(monkeypatch):
    ran = {}
    monkeypatch.setattr(mcp_server.DroneTools, "from_config", lambda cfg: MagicMock())
    monkeypatch.setattr(mcp_server.FastMCP, "run", lambda self, transport: ran.update(t=transport))
    run_main("--transport", "stdio", "--host", "0.0.0.0")
    assert ran["t"] == "stdio"
