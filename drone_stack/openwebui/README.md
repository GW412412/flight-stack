# Open WebUI (and other MCP clients) → the drone

The MAVLink MCP server speaks **MCP**. Open WebUI's classic tool system speaks
**OpenAPI**, so we put [`mcpo`](https://github.com/open-webui/mcpo) in
between. Everything else (rules, limits, confirmation tokens, pilot override)
still lives in the MCP server — Open WebUI is just another chat front end.

```
Open WebUI (browser, :8080 or :3000)
   │  OpenAPI tool calls
   ▼
mcpo  http://127.0.0.1:8001          ← bringup.sh sitl --webui
   │  MCP (streamable HTTP)
   ▼
mcp_server.py  http://127.0.0.1:8765/mcp  → FlightGate → MavlinkHub → MAVProxy → FC
```

## 1. Start the stack with the bridge

```bash
drone_stack/bringup.sh sitl --webui      # or: pixhawk --webui
curl -s -X POST http://127.0.0.1:8001/get_status -H 'Content-Type: application/json' -d '{}'
```

Port 8001 because 8000 is already used by `/workspace/local-agent`
(`MCPO_PORT=...` to change). Interactive API docs: <http://127.0.0.1:8001/docs>.

Manual alternative with the config file in this folder (tools then live under
`/mavlink-drone`):

```bash
mcpo --host 127.0.0.1 --port 8001 --config drone_stack/openwebui/mcpo.json
```

## 2. Open WebUI

Use the Open WebUI you already have:

* `setup-local-ai.sh --with-docker` → <http://localhost:8080> (container
  `open-webui`, Ollama on the host)
* or `drone_stack/docker-compose.yml` → <http://localhost:3000>

Then:

1. **Settings → Tools → Manage Tool Servers → +** → URL
   `http://localhost:8001` (or `http://localhost:8001/mavlink-drone` with the
   config-file form). Leave auth empty. Save; you should see 22 tools.
   *User* tool servers are called **from your browser**, so `localhost` works
   even when Open WebUI runs in Docker.
   (If you add it as an **admin / global** tool server instead, the call comes
   from the Open WebUI backend: inside Docker use
   `http://host.docker.internal:8001` and start mcpo with `MCPO_HOST=0.0.0.0`
   — firewall it, it can fly the drone.)
2. Pick the model (default `qwen2.5:7b-instruct`, see docs/LLM-STACK.md).
   **Chat Controls → Advanced Params → Function Calling = Native** - the
   default "prompt" mode is much less reliable with small local models.
3. Paste the system prompt from the MCP server (`flight_rules` prompt, same
   text as `SYSTEM_PROMPT` in `middleware/drone_tools.py`) into the model's
   system prompt, or the chat's.
4. Enable the tool server in the chat (the ➕ / tools toggle under the input).

Newer Open WebUI releases can also talk MCP directly (Settings → External
Tools → type *MCP (Streamable HTTP)* → `http://localhost:8765/mcp`). That
skips mcpo; mcpo stays the documented path because it works on every version.

## 3. Confirmations in a chat UI

`arm`, `takeoff`, `set_param`, `upload_mission`, `start_mission` and
`clear_pilot_override` return `confirmation_required` + a read-back. In the
SITL profile (`confirm_mode = "echo"`) the token comes back to the model, it
asks you, and calls again. In the Pixhawk profile (`confirm_mode =
"operator"`) the token is **not** shown to the model: read it from the
monitor (`mav_monitor.py` shows pending confirmations) or
`logs/llm-stack/confirmations.jsonl` and type it into the chat yourself.

## 4. Other MCP clients

Anything that speaks MCP can use the same server - no mcpo needed:

| Client | How |
|---|---|
| `middleware/mcp_agent.py` | built in: Ollama + MCP, `--approve` for y/N per action |
| [ollmcp](https://github.com/jonigl/mcp-client-for-ollama) | `ollmcp --mcp-server-url http://127.0.0.1:8765/mcp --model qwen2.5:7b-instruct` |
| Claude Desktop / Cursor / Continue | see `claude_desktop_config.example.json` (via `mcp-remote`, or stdio) |
| MCP Inspector | `npx @modelcontextprotocol/inspector` → Streamable HTTP → `http://127.0.0.1:8765/mcp` |

Run **one** MCP server per vehicle link and point every client at it: the
rate limiter, confirmation book and pilot-override latch live in that
process.
