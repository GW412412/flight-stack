#!/usr/bin/env bash
# Classic sim_vehicle.py wrapper (starts its OWN MAVProxy with map+console).
# For the LLM stack use run_sitl.sh instead - it leaves the MAVProxy hub to
# drone_stack/mavproxy/start_mavproxy.sh so there is only one owner of the link.
# Launch wrappers around sim_vehicle.py from the ArduPilot repo.
# Usage: launch_sitl.sh [copter|plane] [lat lon]
set -e
AP_DIR="${AP_DIR:-$HOME/ardupilot}"
MODE="${1:-copter}"
LAT="${2:--35.1234}"; LON="${3:-147.1234}"

cd "$AP_DIR"
case "$MODE" in
  copter) sim_vehicle.py -v ArduCopter -I 0 --map --console \
            --custom-lat="$LAT" --custom-lon="$LON" --custom-alt=200 ;;
  plane)  sim_vehicle.py -v ArduPlane -I 1 --console \
            --custom-lat="$LAT" --custom-lon="$LON" --custom-alt=200 ;;
  *) echo "usage: launch_sitl.sh [copter|plane] [lat lon]"; exit 1 ;;
esac
