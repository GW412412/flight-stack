#!/usr/bin/env python3
"""MAVLink MCP server: exposes the drone to any MCP client as tools.

    MAVProxy --out udp:127.0.0.1:14560 ──► this server (udpin:127.0.0.1:14560)
                                             │
                     DroneTools (drone_tools.py) = FlightGate + MavlinkHub
                                             │
          MCP transports:  stdio            (Claude Desktop, ollmcp, mcpo spawn)
                           streamable-http  http://127.0.0.1:8765/mcp
                           sse              http://127.0.0.1:8765/sse (older clients)

Run (SITL profile, HTTP, so several clients can share ONE server instance -
which matters, because there is one UDP port, one rate limiter and one
pilot-override latch):

    python mcp_server.py --config ../config/llm_stack.sitl.toml --transport streamable-http

stdio (one client spawns its own server; make sure no other instance holds
UDP 14560):

    python mcp_server.py --config ../config/llm_stack.sitl.toml

Tool semantics, the safety envelope and the confirmation flow are documented
in drone_tools.py, safety_gate.py and docs/LLM-STACK.md.

Uses the official `mcp` Python SDK, 1.x API (`FastMCP`). The 2.x SDK renamed
FastMCP -> MCPServer; requirements.txt pins `mcp<2` because mcpo (the Open
WebUI bridge) still targets 1.x.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

# Allow `python drone_stack/middleware/mcp_server.py` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pydantic import BaseModel, Field                      # noqa: E402
from mcp.server.fastmcp import FastMCP                     # noqa: E402
from mcp.types import ToolAnnotations                      # noqa: E402

from drone_tools import SYSTEM_PROMPT, DroneTools          # noqa: E402
from stack_config import load_config                       # noqa: E402

log = logging.getLogger("mavlink-mcp")

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                       openWorldHint=True)
SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True,
                       openWorldHint=True)
ACT = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False,
                      openWorldHint=True)


class Waypoint(BaseModel):
    """One mission waypoint. Altitude is metres above HOME (not sea level)."""
    lat: float = Field(description="Latitude, decimal degrees")
    lon: float = Field(description="Longitude, decimal degrees")
    alt_m: float = Field(description="Altitude above home, metres")


def build_server(tools: DroneTools, host: str = "127.0.0.1", port: int = 8765) -> FastMCP:
    """Register every DroneTools method as an MCP tool.

    Thin wrappers on purpose: the docstring + type hints become the tool
    description/JSON schema the LLM sees, and all behaviour stays in
    DroneTools where the unit tests exercise it."""
    mcp = FastMCP("mavlink-drone", instructions=SYSTEM_PROMPT, host=host, port=port)

    # ------------------------------------------------------------ read-only
    @mcp.tool(annotations=READ)
    def get_status() -> dict:
        """Quick health check: link ok, heartbeat age, flight mode, armed, GPS fix,
        battery %, position, altitude above home, distance from home, pilot override,
        whether actuation / dry-run are on. Call this first."""
        return tools.get_status()

    @mcp.tool(annotations=READ)
    def get_telemetry() -> dict:
        """Detailed telemetry: attitude (roll/pitch/yaw deg), position and velocity,
        GPS (fix, satellites, hdop), battery (V, A, %), VFR HUD (groundspeed, climb,
        throttle), EKF flags (position_ok), RC channels + rssi, home, mission seq,
        and the last flight-controller STATUSTEXT messages."""
        return tools.get_telemetry()

    @mcp.tool(annotations=READ)
    def get_data_flow() -> dict:
        """MAVLink data-flow health: message rate per type (Hz), total rate, bytes/s,
        packets lost and loss % per source, heartbeat age per source."""
        return tools.get_data_flow()

    @mcp.tool(annotations=READ)
    def get_recent_messages(msg_type: Optional[str] = None, limit: int = 20) -> dict:
        """Last `limit` (max 200) raw MAVLink messages received, optionally only one
        type such as STATUSTEXT, COMMAND_ACK, HEARTBEAT or GPS_RAW_INT."""
        return tools.get_recent_messages(msg_type, limit)

    @mcp.tool(annotations=READ)
    def get_params(names: List[str]) -> dict:
        """Read flight-controller parameters by exact name, e.g. ["WP_SPD","RTL_ALT_M"]
        (max 20). Names differ by firmware: 4.7+ uses WP_SPD, RTL_ALT_M; older uses WPNAV_SPEED, RTL_ALT."""
        return tools.get_params(names)

    @mcp.tool(annotations=READ)
    def get_mission() -> dict:
        """Download the mission stored on the flight controller (seq 0 = home)."""
        return tools.get_mission()

    @mcp.tool(annotations=READ)
    def get_safety_limits() -> dict:
        """The safety envelope you must stay inside: max altitude, geofence radius from
        home, max speed, allowed modes, which actions need confirmation, rate limits,
        writable parameters, pilot-override state, pending confirmations."""
        return tools.get_safety_limits()

    # ------------------------------------------------------------ safe actions
    @mcp.tool(annotations=SAFE)
    def hold() -> dict:
        """Hold position now (LOITER). Always allowed - use it whenever unsure."""
        return tools.hold()

    @mcp.tool(annotations=SAFE)
    def brake() -> dict:
        """Stop as quickly as possible and hold (BRAKE). Always allowed."""
        return tools.brake()

    @mcp.tool(annotations=SAFE)
    def land() -> dict:
        """Land at the current position (LAND). Always allowed."""
        return tools.land()

    @mcp.tool(annotations=SAFE)
    def rtl() -> dict:
        """Return to launch and land (RTL). Always allowed."""
        return tools.rtl()

    # ------------------------------------------------------------ actions
    @mcp.tool(annotations=ACT)
    def set_mode(mode: str, confirm_token: Optional[str] = None) -> dict:
        """Change flight mode. Allowed: GUIDED, LOITER, BRAKE, POSHOLD, ALT_HOLD, AUTO,
        LAND, RTL, SMART_RTL (see get_safety_limits). GUIDED is needed for takeoff/goto."""
        return tools.set_mode(mode, confirm_token)

    @mcp.tool(annotations=ACT)
    def arm(confirm_token: Optional[str] = None) -> dict:
        """Arm the motors. Two-step: the first call returns confirmation_required with a
        readback; call again with confirm_token after the operator agrees."""
        return tools.arm(confirm_token)

    @mcp.tool(annotations=ACT)
    def disarm() -> dict:
        """Disarm the motors. Refused while airborne (use land)."""
        return tools.disarm()

    @mcp.tool(annotations=ACT)
    def takeoff(alt_m: float, confirm_token: Optional[str] = None) -> dict:
        """Take off to alt_m metres above home. Needs GUIDED + armed. Two-step
        confirmation like arm."""
        return tools.takeoff(alt_m, confirm_token)

    @mcp.tool(annotations=ACT)
    def goto(lat: float, lon: float, alt_m: float, wait_s: float = 0) -> dict:
        """Fly to an absolute lat/lon at alt_m above home (GUIDED, armed, airborne).
        Must be inside the geofence radius. wait_s>0 (max 120) waits and reports arrival."""
        return tools.goto(lat, lon, alt_m, wait_s)

    @mcp.tool(annotations=ACT)
    def goto_relative(north_m: float, east_m: float, alt_m: Optional[float] = None,
                      wait_s: float = 0) -> dict:
        """Move relative to the current position: north_m / east_m metres (negative =
        south / west). alt_m = new altitude above home (omit to keep). Easiest way to
        move the drone."""
        return tools.goto_relative(north_m, east_m, alt_m, wait_s)

    @mcp.tool(annotations=ACT)
    def set_speed(speed_ms: float) -> dict:
        """Set horizontal ground speed (m/s) for GUIDED and AUTO."""
        return tools.set_speed(speed_ms)

    @mcp.tool(annotations=ACT)
    def set_param(name: str, value: float, confirm_token: Optional[str] = None) -> dict:
        """Write one parameter from the allowlist (speeds, RTL_ALT ...). Failsafe, fence,
        arming and hardware parameters are refused. Confirmation required."""
        return tools.set_param(name, value, confirm_token)

    @mcp.tool(annotations=ACT)
    def upload_mission(waypoints: List[Waypoint], rtl_at_end: bool = True,
                       confirm_token: Optional[str] = None) -> dict:
        """Upload a simple waypoint mission (altitudes above home). Every point is
        checked against the geofence and altitude limits. Confirmation required."""
        return tools.upload_mission([w.model_dump() for w in waypoints], rtl_at_end,
                                    confirm_token)

    @mcp.tool(annotations=ACT)
    def start_mission(confirm_token: Optional[str] = None) -> dict:
        """Start the uploaded mission (switches to AUTO). Take off first.
        Confirmation required."""
        return tools.start_mission(confirm_token)

    @mcp.tool(annotations=ACT)
    def clear_pilot_override(confirm_token: Optional[str] = None) -> dict:
        """After the pilot took control (mode changed on the radio/GCS), hand control
        back to the LLM. Only on the operator's explicit request. Confirmation required."""
        return tools.clear_pilot_override(confirm_token)

    # ------------------------------------------------------------ prompt
    @mcp.prompt()
    def flight_rules() -> str:
        """System prompt with the flight rules for whichever model drives these tools."""
        return SYSTEM_PROMPT

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="TOML profile (default: $DRONE_STACK_CONFIG)")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"])
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--mavlink-url", help="override mavlink.url")
    a = ap.parse_args(argv)

    # stdio transport owns stdout; ALL logging must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if a.mavlink_url:
        os.environ["MAVLINK_URL"] = a.mavlink_url
    cfg = load_config(a.config)
    transport = a.transport or cfg.server.transport
    host, port = a.host or cfg.server.host, a.port or cfg.server.port
    log.info("profile=%s mavlink=%s actuation=%s dry_run=%s confirm=%s transport=%s",
             cfg.profile, cfg.mavlink.url, cfg.safety.actuation_enabled, cfg.safety.dry_run,
             cfg.safety.confirm_mode, transport)
    tools = DroneTools.from_config(cfg)
    server = build_server(tools, host=host, port=port)
    if transport != "stdio":
        log.info("MCP endpoint: http://%s:%s%s", host, port,
                 "/mcp" if transport == "streamable-http" else "/sse")
    try:
        server.run(transport=transport)
    finally:
        tools.close()


if __name__ == "__main__":
    main()
