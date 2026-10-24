#!/usr/bin/env bash
# =============================================================================
# start_mavproxy.sh - MAVProxy as the ground-station MAVLink hub.
#
# MAVProxy is the ONLY program that opens the flight-controller link (USB
# serial, telemetry radio, or SITL TCP). It then fans the stream out over UDP
# to every consumer, and forwards their replies back to the FC:
#
#   Pixhawk 1 /dev/ttyACM0 ─┐                    ┌─► udp 14550  QGroundControl
#   (or SiK radio /dev/ttyUSB0)├─► MAVProxy ─────┼─► udp 14560  MAVLink MCP server
#   (or SITL tcp:127.0.0.1:5760)┘  (+ .tlog)     └─► udp 14565  data-flow monitor
#
# Usage:
#   start_mavproxy.sh sitl                     # tcp:127.0.0.1:5760
#   start_mavproxy.sh pixhawk                  # /dev/ttyACM0 @ 115200 (USB)
#   start_mavproxy.sh radio                    # /dev/ttyUSB0 @ 57600 (SiK)
#   MASTER=/dev/serial/by-id/usb-ArduPilot... BAUD=115200 start_mavproxy.sh custom
#
# Environment overrides (all optional):
#   MASTER, BAUD              link to the FC
#   QGC_PORT=14550 MCP_PORT=14560 MONITOR_PORT=14565
#   EXTRA_OUTS="udp:192.168.1.50:14570 udp:..."   more consumers (watchers ...)
#   LOG_DIR=logs/mavproxy     where MAVProxy writes its .tlog
#   INTERACTIVE=0|1           1 = MAVProxy console in this terminal (default
#                             1 when run from a terminal, 0 under systemd)
#   MAVPROXY_HEARTBEAT=1      0 = MAVProxy sends no heartbeat (see note)
#
# Note on heartbeats / GCS failsafe (stack report Trap 1): MAVProxy and QGC
# both send heartbeats as sysid 255, which is what ArduPilot's GCS failsafe
# listens to. While either runs, a crashed LLM/MCP server does NOT trip the
# GCS failsafe. The MCP server uses sysid 250 so it never masks it. Which
# client should own the failsafe heartbeat is an open decision (DECISIONS.md).
#
# Alternative hub: config/mavlink-router.conf (mavlink-routerd) uses the same
# UDP ports. Run ONE of them, never both - only one program may own the port.
# =============================================================================
set -euo pipefail

PROFILE="${1:-sitl}"
case "$PROFILE" in
  sitl)    MASTER="${MASTER:-tcp:127.0.0.1:5760}"; BAUD="${BAUD:-115200}" ;;
  pixhawk) MASTER="${MASTER:-/dev/ttyACM0}";       BAUD="${BAUD:-115200}" ;;
  radio)   MASTER="${MASTER:-/dev/ttyUSB0}";       BAUD="${BAUD:-57600}" ;;
  custom)  : "${MASTER:?set MASTER=...}";          BAUD="${BAUD:-57600}" ;;
  *) echo "usage: $0 [sitl|pixhawk|radio|custom]"; exit 1 ;;
esac

QGC_PORT="${QGC_PORT:-14550}"
MCP_PORT="${MCP_PORT:-14560}"
MONITOR_PORT="${MONITOR_PORT:-14565}"
LOG_DIR="${LOG_DIR:-logs/mavproxy}"
if [ -t 0 ]; then INTERACTIVE="${INTERACTIVE:-1}"; else INTERACTIVE="${INTERACTIVE:-0}"; fi

command -v mavproxy.py >/dev/null || {
  echo "mavproxy.py not found: pip install MAVProxy (inside drone_stack/.venv)"; exit 1; }

# Serial sanity checks (the usual first-time Linux traps).
if [[ "$MASTER" == /dev/* ]]; then
  [ -e "$MASTER" ] || { echo "No $MASTER - is the Pixhawk plugged in? ls /dev/serial/by-id/"; exit 1; }
  [ -r "$MASTER" ] && [ -w "$MASTER" ] || {
    echo "No permission on $MASTER: sudo usermod -aG dialout \$USER, then log out/in"; exit 1; }
  if systemctl is-active --quiet ModemManager 2>/dev/null; then
    echo "WARNING: ModemManager is running and may grab $MASTER:"
    echo "         sudo systemctl mask --now ModemManager.service"
  fi
fi

mkdir -p "$LOG_DIR"
OUTS=(--out "udp:127.0.0.1:${QGC_PORT}"      # QGroundControl (auto-connects)
      --out "udp:127.0.0.1:${MCP_PORT}"      # MAVLink MCP server (udpin)
      --out "udp:127.0.0.1:${MONITOR_PORT}") # data-flow monitor (passive)
for o in ${EXTRA_OUTS:-}; do OUTS+=(--out "$o"); done

ARGS=(--master "$MASTER" --baudrate "$BAUD" "${OUTS[@]}"
      --state-basedir "$LOG_DIR" --aircraft llm-stack
      --streamrate 10)   # ask the FC for 10 Hz streams on this link
[ "$INTERACTIVE" = "1" ] || ARGS+=(--non-interactive --daemon)
[ "${MAVPROXY_HEARTBEAT:-1}" = "1" ] || ARGS+=(--cmd "set heartbeat 0")

echo "MAVProxy: $MASTER @ $BAUD -> QGC:$QGC_PORT MCP:$MCP_PORT MONITOR:$MONITOR_PORT ${EXTRA_OUTS:-}"
echo "tlogs in $LOG_DIR/llm-stack/"
exec mavproxy.py "${ARGS[@]}"
