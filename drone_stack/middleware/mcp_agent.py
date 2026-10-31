#!/usr/bin/env python3
"""Ollama tool-calling agent that flies through the MAVLink MCP server.

    you> take off to 10 m and hold
        │
        ▼
    Ollama (local, CPU)  ──tool call──►  MCP client (this file)
        ▲                                   │  streamable-HTTP (default) or stdio
        └──────── tool result ◄─────────────┤
                                            ▼
                                   mcp_server.py  → FlightGate → MavlinkHub → MAVProxy → FC

The agent knows nothing about MAVLink: it lists the server's tools, hands
their JSON schemas to Ollama, executes whatever the model calls, and feeds
the results back - looping until the model answers in plain text. All the
safety lives in the server, so this file is safe to modify or replace
(Open WebUI, ollmcp, Claude Desktop ... are drop-in alternatives).

Examples:
    # server already running (bring-up script) on http://127.0.0.1:8765/mcp
    python mcp_agent.py
    python mcp_agent.py --model llama3.1:8b --approve
    python mcp_agent.py --once "what mode are we in and is the GPS ok?"
    # spawn a private server over stdio instead (UDP 14560 must be free)
    python mcp_agent.py --stdio "python mcp_server.py --config ../../config/llm_stack.sitl.toml --transport stdio"

Model choice (CPU only, no GPU): default qwen2.5:7b-instruct - reliable
tool calling, ~4.7 GB, usable speed on a 40-core Xeon. Alternatives:
llama3.1:8b, qwen3:8b (pass --no-think), or qwen2.5:3b/1.5b for quick tests.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from contextlib import AsyncExitStack

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp import ClientSession, StdioServerParameters      # noqa: E402
from mcp.client.stdio import stdio_client                 # noqa: E402
from mcp.client.streamable_http import streamablehttp_client  # noqa: E402
from ollama import AsyncClient                            # noqa: E402

from drone_tools import READ_ONLY_TOOLS, SYSTEM_PROMPT    # noqa: E402

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct")
DEFAULT_MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8765/mcp")

# ANSI colours for a readable terminal transcript (disabled when piped).
_TTY = sys.stdout.isatty()
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s


def mcp_tools_to_ollama(tools) -> list:
    """MCP Tool objects -> Ollama/OpenAI function-calling schema."""
    out = []
    for t in tools:
        schema = dict(t.inputSchema or {"type": "object", "properties": {}})
        schema.setdefault("properties", {})
        out.append({"type": "function", "function": {
            "name": t.name, "description": (t.description or "").strip(),
            "parameters": schema}})
    return out


def auth_headers() -> dict:
    """Bearer header from MCP_AUTH_TOKEN_FILE / MCP_AUTH_TOKEN (same vars as the server)."""
    from mcp_auth import load_token
    token = load_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def result_to_text(res) -> str:
    """CallToolResult -> compact text for the model (prefers structured JSON)."""
    if getattr(res, "structuredContent", None):
        sc = res.structuredContent
        sc = sc.get("result", sc) if isinstance(sc, dict) else sc
        return json.dumps(sc, default=str)
    parts = [getattr(c, "text", str(c)) for c in res.content]
    return "\n".join(parts)


class Agent:
    def __init__(self, session: ClientSession, model: str, ollama_host: str | None,
                 approve: bool, max_steps: int, options: dict, think):
        self.session, self.model = session, model
        self.ollama = AsyncClient(host=ollama_host) if ollama_host else AsyncClient()
        self.approve, self.max_steps = approve, max_steps
        self.options, self.think = options, think
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.tools: list = []

    async def init(self):
        listed = await self.session.list_tools()
        self.tools = mcp_tools_to_ollama(listed.tools)
        print(_c("2", f"[mcp] {len(self.tools)} tools: "
                      + ", ".join(t['function']['name'] for t in self.tools)))

    async def _chat(self):
        kw = dict(model=self.model, messages=self.history, tools=self.tools,
                  options=self.options)
        if self.think is not None:
            kw["think"] = self.think
        t0 = time.time()
        resp = await self.ollama.chat(**kw)
        print(_c("2", f"[llm] {time.time() - t0:.1f}s"))
        return resp.message

    async def ask(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        for _step in range(self.max_steps):
            msg = await self._chat()
            self.history.append(msg.model_dump(exclude_none=True))
            if not msg.tool_calls:
                return msg.content or ""
            for call in msg.tool_calls:
                name, args = call.function.name, dict(call.function.arguments or {})
                print(_c("36", f"[tool] {name}({json.dumps(args)})"))
                if self.approve and name not in READ_ONLY_TOOLS:
                    ans = input(_c("33", f"  approve {name}? [y/N] ")).strip().lower()
                    if ans not in ("y", "yes"):
                        content = json.dumps({"ok": False, "error": "operator denied this tool call"})
                        self.history.append({"role": "tool", "tool_name": name, "content": content})
                        continue
                try:
                    res = await self.session.call_tool(name, args)
                    content = result_to_text(res)
                except Exception as exc:
                    content = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                print(_c("2", f"[tool->] {content[:400]}"))
                self.history.append({"role": "tool", "tool_name": name, "content": content})
        return "(stopped: too many tool steps - ask again or check the tool results)"


async def run(a):
    async with AsyncExitStack() as stack:
        if a.stdio:
            parts = shlex.split(a.stdio)
            params = StdioServerParameters(command=parts[0], args=parts[1:],
                                           env=dict(os.environ))
            read, write = await stack.enter_async_context(stdio_client(params))
        else:
            read, write, _ = await stack.enter_async_context(streamablehttp_client(a.mcp_url, headers=auth_headers() or None))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        options = {"num_ctx": a.num_ctx, "temperature": a.temperature}
        if a.num_thread:
            options["num_thread"] = a.num_thread
        think = False if a.no_think else None
        agent = Agent(session, a.model, a.ollama_host, a.approve, a.max_steps, options, think)
        await agent.init()
        if a.once:
            print(_c("32", "drone> ") + await agent.ask(a.once))
            return
        print(f"LLM behind the sticks - model {a.model}. The RC pilot always has authority. "
              "Type 'quit' to exit.")
        while True:
            try:
                text = (await asyncio.to_thread(input, _c("1", "you> "))).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ("quit", "exit"):
                break
            if text:
                print(_c("32", "drone> ") + await agent.ask(text))


def main():
    ap = argparse.ArgumentParser(description="Ollama + MAVLink MCP chat agent")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST"))
    ap.add_argument("--mcp-url", default=DEFAULT_MCP_URL)
    ap.add_argument("--stdio", help="command line to spawn the MCP server over stdio")
    ap.add_argument("--approve", action="store_true",
                    help="ask y/N before every non-read-only tool call (recommended on hardware)")
    ap.add_argument("--once", help="ask one question and exit (scripted tests)")
    ap.add_argument("--max-steps", type=int, default=10)
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--num-thread", type=int, default=int(os.environ.get("OLLAMA_NUM_THREAD", 0)))
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--no-think", action="store_true", help="disable reasoning (qwen3 etc.)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
