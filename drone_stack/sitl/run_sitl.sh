#!/usr/bin/env bash
# run_sitl.sh - start ArduCopter SITL for the LLM stack, no ArduPilot build needed.
#
#   drone_stack/sitl/run_sitl.sh            # foreground (Ctrl-C to stop)
#   drone_stack/sitl/run_sitl.sh --daemon   # background, PID in $SITL_DIR/sitl.pid
#   drone_stack/sitl/run_sitl.sh --stop
#
# Two ways to get a simulator:
#   1. AP_DIR points at an ArduPilot checkout with sim_vehicle.py -> that is used
#      (same as launch_sitl.sh, but WITHOUT its own MAVProxy: our
#      start_mavproxy.sh is the single hub, see docs/LLM-STACK.md).
#   2. Otherwise the official prebuilt Linux x86_64 binary is downloaded once
#      from firmware.ardupilot.org into $SITL_DIR (default ~/.cache/drone_stack/sitl)
#      together with the default copter parameter file.
#
# SITL listens on TCP 5760 (SERIAL0); start_mavproxy.sh sitl connects there.
# Home defaults to the ArduPilot test field (CMAC, Canberra) - override with
# SITL_HOME="lat,lon,alt_msl,heading".
#
# NOTE: the SITL binary exits when its stdin reaches EOF, so the --daemon
# mode feeds it an endless pipe instead of /dev/null.
set -euo pipefail

SITL_DIR="${SITL_DIR:-$HOME/.cache/drone_stack/sitl}"
SITL_HOME="${SITL_HOME:--35.363261,149.165230,584,353}"
SITL_SPEEDUP="${SITL_SPEEDUP:-1}"
BIN_URL="${SITL_BIN_URL:-https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter}"
PARM_URL="${SITL_PARM_URL:-https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm}"
AP_DIR="${AP_DIR:-}"

mkdir -p "$SITL_DIR"
PIDFILE="$SITL_DIR/sitl.pid"

if [[ "${1:-}" == "--stop" ]]; then
  if [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "SITL stopped"; rm -f "$PIDFILE"
  else
    pkill -f "arducopter --model" && echo "SITL stopped" || echo "SITL not running"
  fi
  exit 0
fi

# ---- option 1: ArduPilot checkout --------------------------------------------
if [[ -n "$AP_DIR" && -x "$AP_DIR/Tools/autotest/sim_vehicle.py" ]]; then
  IFS=, read -r LAT LON ALT HDG <<<"$SITL_HOME"
  cd "$AP_DIR"
  # --no-mavproxy: our hub (start_mavproxy.sh) connects to tcp:127.0.0.1:5760
  exec Tools/autotest/sim_vehicle.py -v ArduCopter -I0 --no-mavproxy \
       --custom-location="$LAT,$LON,$ALT,$HDG" --speedup "$SITL_SPEEDUP"
fi

# ---- option 2: prebuilt binary -------------------------------------------------
cd "$SITL_DIR"
if [[ ! -x arducopter ]]; then
  echo "Downloading ArduCopter SITL binary (~5 MB) into $SITL_DIR ..."
  curl -fL --retry 3 -o arducopter.tmp "$BIN_URL"
  chmod +x arducopter.tmp && mv arducopter.tmp arducopter
fi
if [[ ! -f copter.parm ]]; then
  curl -fL --retry 3 -o copter.parm "$PARM_URL"
fi

CMD=(./arducopter --model + --speedup "$SITL_SPEEDUP" --defaults copter.parm -I0
     --home "$SITL_HOME")

if [[ "${1:-}" == "--daemon" ]]; then
  # setsid: survive the terminal; tail -f /dev/null: keep stdin open (see NOTE).
  setsid sh -c 'tail -f /dev/null | exec "$@"' sh "${CMD[@]}" > "$SITL_DIR/sitl.log" 2>&1 &
  sleep 1
  pgrep -n -f "arducopter --model" > "$PIDFILE" || true
  echo "SITL started (pid $(cat "$PIDFILE" 2>/dev/null)), log $SITL_DIR/sitl.log, TCP 5760"
else
  echo "SITL in foreground - TCP 5760. Ctrl-C to stop."
  exec "${CMD[@]}"
fi
