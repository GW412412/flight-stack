"""mcp_agent.Agent loop with a SCRIPTED fake Ollama (no model needed).

Checks the plumbing: tool schemas handed to the model, tool calls executed
over MCP, results fed back, final text returned, --approve denial."""
import asyncio
import json
from types import SimpleNamespace

from mcp_agent import Agent


class FakeSession:
    def __init__(self):
        self.calls = []

    async def list_tools(self):
        t = SimpleNamespace(name="get_status", description="Vehicle status summary.",
                            inputSchema={"type": "object", "properties": {}})
        a = SimpleNamespace(name="arm", description="Arm the motors (confirmation).",
                            inputSchema={"type": "object", "properties": {
                                "confirm_token": {"type": "string"}}})
        return SimpleNamespace(tools=[t, a])

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        body = {"mode": "GUIDED", "link_ok": True} if name == "get_status" else {"ok": True}
        return SimpleNamespace(structuredContent={"result": body}, content=[])


def _msg(content="", calls=None):
    tc = None
    if calls:
        tc = [SimpleNamespace(function=SimpleNamespace(name=n, arguments=a)) for n, a in calls]
    m = SimpleNamespace(content=content, tool_calls=tc)
    m.model_dump = lambda exclude_none=True: {"role": "assistant", "content": content}
    return m


class FakeOllama:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    async def chat(self, **kw):
        self.seen.append(kw)
        return SimpleNamespace(message=self.script.pop(0))


def run_agent(script, approve=False, monkeypatch=None):
    sess = FakeSession()
    ag = Agent(sess, "fake", None, approve, 5, {}, None)
    ag.ollama = FakeOllama(script)

    async def go():
        await ag.init()
        return await ag.ask("what mode?")
    return asyncio.run(go()), sess, ag


def test_tool_call_roundtrip():
    out, sess, ag = run_agent([_msg(calls=[("get_status", {})]), _msg("We are in GUIDED.")])
    assert out == "We are in GUIDED."
    assert sess.calls == [("get_status", {})]
    tool_msgs = [m for m in ag.history if m.get("role") == "tool"]
    assert json.loads(tool_msgs[0]["content"])["mode"] == "GUIDED"
    # the model was given both tool schemas
    assert {t["function"]["name"] for t in ag.ollama.seen[0]["tools"]} == {"get_status", "arm"}


def test_approve_denial(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    out, sess, ag = run_agent([_msg(calls=[("arm", {})]), _msg("Not armed.")], approve=True)
    assert sess.calls == []                       # never reached the server
    assert "operator denied" in [m for m in ag.history if m.get("role") == "tool"][0]["content"]


def test_step_limit():
    script = [_msg(calls=[("get_status", {})]) for _ in range(5)]
    out, sess, ag = run_agent(script)
    assert "too many tool steps" in out
