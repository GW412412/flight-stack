#!/usr/bin/env bash
# =============================================================================
# install.sh - install the LLM-stack systemd *user* units (no root needed).
#
#   drone_stack/systemd/install.sh pixhawk     # USB Pixhawk, pixhawk profile
#   drone_stack/systemd/install.sh radio       # SiK radio,   pixhawk profile
#   drone_stack/systemd/install.sh sitl        # SITL (start SITL yourself:
#                                              #   drone_stack/sitl/run_sitl.sh --daemon)
#   systemctl --user start llm-stack-mavproxy llm-stack-mcp llm-stack-monitor
#   systemctl --user start llm-stack-mcpo      # optional, for Open WebUI
#   journalctl --user -u llm-stack-mcp -f
#
# Units are NOT enabled at boot on purpose: an LLM that can fly should be
# started deliberately. `systemctl --user enable ...` if you really want it.
# Do not run these and bringup.sh at the same time (same ports).
# =============================================================================
set -euo pipefail
LINK="${1:-pixhawk}"
case "$LINK" in
  sitl) CONFIG=llm_stack.sitl.toml ;;
  pixhawk|radio) CONFIG=llm_stack.pixhawk.toml ;;
  *) echo "usage: $0 [pixhawk|radio|sitl]"; exit 1 ;;
esac
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
[ -x "$ROOT/drone_stack/.venv/bin/python" ] || {
  echo "Create the venv first: python3 -m venv drone_stack/.venv && drone_stack/.venv/bin/pip install -r drone_stack/middleware/requirements.txt"; exit 1; }
mkdir -p "$DEST"
for u in "$HERE"/llm-stack-*.service; do
  sed -e "s|@ROOT@|$ROOT|g" -e "s|@LINK@|$LINK|g" -e "s|@CONFIG@|$CONFIG|g" "$u" > "$DEST/$(basename "$u")"
  echo "installed $DEST/$(basename "$u")"
done
systemctl --user daemon-reload
echo "Start with: systemctl --user start llm-stack-mavproxy llm-stack-mcp llm-stack-monitor"
