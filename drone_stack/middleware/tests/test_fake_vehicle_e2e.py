"""End-to-end through the real stack against fake_vehicle.py:

    MCP client (in-memory) -> FastMCP server -> DroneTools -> FlightGate
        -> MavlinkHub -> pymavlink UDP -> FakeVehicle

Exercises the actual MAVLink protocol code (COMMAND_ACK waits, PARAM read-
back, mission upload/download, pilot-override detection). Real SITL testing
is documented in docs/LLM-STACK.md; this test needs no ArduPilot binary.
"""
import asyncio
import json
import socket
import time

import pytest

from drone_tools import DroneTools
from fake_vehicle import FakeVehicle
from mav_node import MavNode
from mavlink_hub import MavlinkHub
from safety_gate import FlightGate
from stack_config import SafetyConfig

from mcp.shared.memory import create_connected_server_and_client_session
from mcp_server import build_server
from mcp_agent import result_to_text


def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for(pred, timeout=8.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


@pytest.fixture
def stack(tmp_path):
    """Yields (DroneTools, FakeVehicle). dry_run False, echo confirmations."""
    port = free_udp_port()
    node = MavNode(f"udpin:127.0.0.1:{port}", record_dir=None, send_heartbeat=True)
    node.start(wait_heartbeat=False)
    fv = FakeVehicle(out=f"127.0.0.1:{port}").start()
    cfg = SafetyConfig(actuation_enabled=True, dry_run=False, require_rc_link=True,
                       min_action_interval_s=0, max_actions_per_minute=1000,
                       geofence_radius_m=150, max_alt_m=30, confirm_mode="echo")
    gate = FlightGate(cfg)
    hub = MavlinkHub(node, flight_gate=gate, dry_run=False, log_dir=str(tmp_path))
    tools = DroneTools(node, hub, gate, log_dir=str(tmp_path), home_poll_s=0.5)
    assert wait_for(lambda: node.link_ok() and node.home() is not None), "no link/home"
    yield tools, fv
    tools.close()
    fv.stop()


def confirm(fn, **kw):
    """Two-step call: first gets the token, second uses it."""
    r = fn(**kw)
    assert r.get("confirmation_required"), r
    return fn(**kw, confirm_token=r["confirm_token"])


def test_read_only_tools(stack):
    t, fv = stack
    s = t.get_status()
    assert s["link_ok"] and s["mode"] == "STABILIZE" and s["rc_link_ok"] is True
    assert t.get_params(["WP_SPD", "NOPE"])["params"] == {"WP_SPD": 10.0, "NOPE": None}
    flow = t.get_data_flow()
    assert flow["link_ok"] and flow["total_rate_hz"] > 5
    assert t.get_recent_messages("HEARTBEAT", 3)["count"] >= 1
    assert "max_alt_m" in t.get_safety_limits()


def test_guided_flight(stack):
    t, fv = stack
    assert t.set_mode("GUIDED")["ok"]
    assert wait_for(lambda: t.node.get_status()["mode"] == "GUIDED")
    assert confirm(t.arm)["ok"]
    assert wait_for(lambda: t.node.get_status()["armed"])
    assert not t.takeoff(alt_m=60)["ok"]                      # above max_alt
    assert confirm(t.takeoff, alt_m=10)["ok"]
    assert wait_for(lambda: (t.node.get_status()["rel_alt"] or 0) > 9.5)
    assert not t.goto_relative(500, 0)["ok"]                  # outside geofence
    assert not t.set_speed(50)["ok"]                          # above max_speed
    assert t.set_speed(4)["ok"]
    r = t.goto_relative(20, 10, wait_s=15)
    assert r["ok"] and r.get("arrived"), r
    assert not t.disarm()["ok"]                               # airborne
    h = t.hold()
    assert h["ok"] and "held_at" in h
    assert t.rtl()["ok"]
    assert wait_for(lambda: not t.node.get_status()["armed"], timeout=10)
    assert "MAV_CMD_NAV_TAKEOFF" in fv.commands_seen


def test_params_guarded(stack):
    t, fv = stack
    assert t.set_param("FS_GCS_ENABLE", 0)["blocked"]
    assert fv.params["FS_GCS_ENABLE"] == 1.0                  # untouched
    assert t.set_param("WP_SPD", 50)["blocked"]
    r = confirm(t.set_param, name="WP_SPD", value=4)
    assert r["ok"] and r["value"] == 4.0 and r["previous"] == 10.0
    assert fv.params["WP_SPD"] == 4.0


def test_mission_roundtrip(stack):
    t, fv = stack
    home = t.node.home()
    wps = [{"lat": home["lat"] + 0.0003, "lon": home["lon"], "alt_m": 12},
           {"lat": home["lat"], "lon": home["lon"] + 0.0003, "alt_m": 12}]
    assert t.upload_mission(wps + [{"lat": home["lat"] + 0.05, "lon": home["lon"],
                                    "alt_m": 12}])["blocked"]
    r = confirm(t.upload_mission, waypoints=wps, rtl_at_end=True)
    assert r["ok"] and r["items_total"] == 4, r
    m = t.get_mission()
    assert m["ok"] and m["count"] == 4
    assert m["items"][3]["command"] == "MAV_CMD_NAV_RETURN_TO_LAUNCH"


def test_pilot_override_detection(stack):
    t, fv = stack
    assert t.set_mode("GUIDED")["ok"]
    assert wait_for(lambda: t.node.get_status()["mode"] == "GUIDED")
    time.sleep(0.2)
    fv.simulate_pilot_mode("LOITER")                          # RC switch flicked
    assert wait_for(lambda: t.gate.pilot_override() is not None)
    r = t.set_mode("GUIDED")
    assert r["blocked"] and "pilot override" in r["reasons"][0]
    assert t.land()["ok"]                                     # safe modes still fine
    assert confirm(t.clear_pilot_override)["ok"]
    assert t.gate.pilot_override() is None


def test_dry_run_sends_nothing(stack):
    t, fv = stack
    t.hub.dry_run = True
    before = list(fv.commands_seen)
    r = t.set_mode("GUIDED")
    assert r["ok"] and r["dry_run"] and "would_send" in r
    time.sleep(0.5)
    sent = [c for c in fv.commands_seen[len(before):] if c != "MAV_CMD_REQUEST_MESSAGE"]
    assert sent == []                      # only read-only requests went out
    assert fv.mode == "STABILIZE"


def test_over_mcp_protocol(stack):
    """Same stack, but called through a real MCP client session."""
    t, fv = stack
    srv = build_server(t)

    async def go():
        async with create_connected_server_and_client_session(srv._mcp_server) as c:
            listed = await c.list_tools()
            assert len(listed.tools) == 23
            st = json.loads(result_to_text(await c.call_tool("get_status", {})))
            assert st["link_ok"]
            r = json.loads(result_to_text(await c.call_tool("arm", {})))
            assert r["confirmation_required"]
            bad = await c.call_tool("takeoff", {"altitude": 5})      # wrong arg name
            assert bad.isError
    asyncio.run(go())
