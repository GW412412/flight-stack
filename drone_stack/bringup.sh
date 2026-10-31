#!/usr/bin/env bash
# =============================================================================
# bringup.sh - start / stop the whole "LLM behind the sticks" stack.
#
#   drone_stack/bringup.sh sitl              # SITL + MAVProxy + MCP + monitor
#   drone_stack/bringup.sh sitl --webui      # ... + mcpo bridge for Open WebUI
#   drone_stack/bringup.sh pixhawk           # real FC on /dev/ttyACM0 (USB)
#   drone_stack/bringup.sh radio             # real FC via SiK radio /dev/ttyUSB0
#   drone_stack/bringup.sh status
#   drone_stack/bringup.sh stop
#
# What runs (each as a background process, PID files + logs in logs/run/):
#
#   1. SITL         drone_stack/sitl/run_sitl.sh --daemon    (sitl profile only)
#   2. MAVProxy     drone_stack/mavproxy/start_mavproxy.sh   (the ONE link owner)
#   3. MCP server   middleware/mcp_server.py, streamable HTTP :8765/mcp
#   4. Monitor      middleware/mav_monitor.py --headless --web 8766
#   5. mcpo         (--webui) MCP -> OpenAPI on :8001 for Open WebUI
#
# The LLM itself is NOT started here - pick a front end afterwards:
#   python drone_stack/middleware/mcp_agent.py            # terminal + Ollama
#   Open WebUI -> Settings -> Tools -> http://localhost:8001   (see docs/LLM-STACK.md)
#
# Config: config/llm_stack.<profile>.toml (pixhawk/radio use the pixhawk file:
# actuation OFF + dry-run by default - edit it deliberately before a real flight).
# Python: uses drone_stack/.venv if it exists, else whatever python3 is on PATH.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RUN="$ROOT/logs/run"
mkdir -p "$RUN"
MW="$ROOT/drone_stack/middleware"

if [[ -f "$ROOT/drone_stack/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  . "$ROOT/drone_stack/.venv/bin/activate"
fi
PY="${PYTHON:-python3}"

MCP_PORT_HTTP="${MCP_HTTP_PORT:-8765}"
MONITOR_WEB="${MONITOR_WEB_PORT:-8766}"
MCPO_PORT="${MCPO_PORT:-8001}"        # 8000 is taken by /workspace/local-agent
MCPO_HOST="${MCPO_HOST:-127.0.0.1}"   # non-loopback (e.g. 0.0.0.0 for a Docker backend) needs MCPO_API_KEY[_FILE]

# ---- helpers ------------------------------------------------------------------
start_bg() {   # start_bg NAME CMD...   (setsid: survives the terminal)
  local name="$1"; shift
  if is_running "$name"; then echo "  $name already running (pid $(cat "$RUN/$name.pid"))"; return; fi
  setsid "$@" > "$RUN/$name.log" 2>&1 < /dev/null &
  echo $! > "$RUN/$name.pid"
  echo "  started $name (pid $!, log logs/run/$name.log)"
}
is_running() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
stop_one() {
  if is_running "$1"; then kill "$(cat "$RUN/$1.pid")" && echo "  stopped $1"; fi
  rm -f "$RUN/$1.pid"
}
read_secret() {  # read_secret FILE_VAR VALUE_VAR -> prints the secret named by env vars (file wins)
  local f="${!1:-}" v="${!2:-}"
  if [[ -n "$f" ]]; then tr -d '[:space:]' < "$f"; else printf '%s' "$v"; fi
}
is_loopback_host() { [[ "$1" == "127.0.0.1" || "$1" == "localhost" || "$1" == "::1" ]]; }
wait_port() {  # wait_port PORT SECONDS
  for _ in $(seq 1 "$2"); do
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
    sleep 1
  done
  return 1
}

CMD="${1:-}"; shift || true
WEBUI=0
for a in "$@"; do [[ "$a" == "--webui" ]] && WEBUI=1; done

case "$CMD" in
  stop)
    for n in mcpo monitor mcp mavproxy; do stop_one "$n"; done
    "$ROOT/drone_stack/sitl/run_sitl.sh" --stop || true
    exit 0 ;;
  status)
    for n in mavproxy mcp monitor mcpo; do
      if is_running "$n"; then echo "  $n: running (pid $(cat "$RUN/$n.pid"))"; else echo "  $n: stopped"; fi
    done
    pgrep -f "arducopter --model" >/dev/null && echo "  sitl: running" || echo "  sitl: stopped"
    exit 0 ;;
  sitl)        PROFILE=sitl;    CFG=config/llm_stack.sitl.toml ;;
  pixhawk|radio) PROFILE="$CMD"; CFG=config/llm_stack.pixhawk.toml ;;
  *) sed -n '2,30p' "$0"; exit 1 ;;
esac

echo "== LLM stack bring-up: $PROFILE ($CFG)"

# 1. SITL ------------------------------------------------------------------------
if [[ "$PROFILE" == sitl ]]; then
  pgrep -f "arducopter --model" >/dev/null || "$ROOT/drone_stack/sitl/run_sitl.sh" --daemon
  wait_port 5760 30 || { echo "SITL did not open TCP 5760"; exit 1; }
fi

# 2. MAVProxy (non-interactive; its console is not needed - QGC is the GCS) --------
export INTERACTIVE=0
start_bg mavproxy "$ROOT/drone_stack/mavproxy/start_mavproxy.sh" "$PROFILE"

# 3. MCP server over streamable HTTP (one shared instance for all LLM clients) ---
start_bg mcp "$PY" "$MW/mcp_server.py" --config "$CFG" \
    --transport streamable-http --port "$MCP_PORT_HTTP"
wait_port "$MCP_PORT_HTTP" 20 || { echo "MCP server did not start - see logs/run/mcp.log"; exit 1; }

# 4. Passive monitor (tlog + CSV in logs/monitor, web view) ------------------------
start_bg monitor "$PY" "$MW/mav_monitor.py" --headless --web "$MONITOR_WEB"

# 5. Optional Open WebUI bridge ----------------------------------------------------
if [[ "$WEBUI" == 1 ]]; then
  command -v mcpo >/dev/null || { echo "mcpo not installed: pip install mcpo"; exit 1; }
  # Tokens come from the environment (see docs/P1-SAFETY-NOTES.md); never put one in this repo.
  #   MCP_AUTH_TOKEN[_FILE]  mcpo -> MCP server (only if the server was started with a token)
  #   MCPO_API_KEY[_FILE]    Open WebUI -> mcpo  (REQUIRED when MCPO_HOST is not loopback)
  MCP_TOK="$(read_secret MCP_AUTH_TOKEN_FILE MCP_AUTH_TOKEN)"
  MCPO_KEY="$(read_secret MCPO_API_KEY_FILE MCPO_API_KEY)"
  if ! is_loopback_host "$MCPO_HOST" && [[ -z "$MCPO_KEY" ]]; then
    echo "MCPO_HOST=$MCPO_HOST is not loopback: set MCPO_API_KEY_FILE (or MCPO_API_KEY) first"; exit 1
  fi
  MCPO_ARGS=()
  [[ -n "$MCPO_KEY" ]] && MCPO_ARGS+=(--api-key "$MCPO_KEY" --strict-auth)
  [[ -n "$MCP_TOK" ]] && MCPO_ARGS+=(--header "{\"Authorization\": \"Bearer $MCP_TOK\"}")
  start_bg mcpo mcpo --host "$MCPO_HOST" --port "$MCPO_PORT" ${MCPO_ARGS[@]+"${MCPO_ARGS[@]}"} \
      --server-type streamable-http -- "http://127.0.0.1:$MCP_PORT_HTTP/mcp"
fi

cat <<INFO

== up. Ports:
   QGroundControl     udp 14550   (just open QGC - it auto-connects)
   MCP (MAVLink in)   udp 14560
   Monitor (in)       udp 14565   web http://127.0.0.1:$MONITOR_WEB/
   MCP server         http://127.0.0.1:$MCP_PORT_HTTP/mcp
$( [[ "$WEBUI" == 1 ]] && echo "   mcpo (Open WebUI) http://127.0.0.1:$MCPO_PORT  (docs at /docs)" )

Next:
   python drone_stack/middleware/mcp_agent.py                 # chat + fly (Ollama)
   python drone_stack/middleware/mav_monitor.py               # live terminal dashboard
   drone_stack/bringup.sh stop
INFO
