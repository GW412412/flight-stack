"""The MCP surface the LLM sees: tool names, parameters and annotations.

Building the server does not touch MAVLink (the tools object is only called
when a tool runs), so a placeholder is enough here."""
import asyncio

import pytest

from drone_tools import ACTION_TOOLS, ALL_TOOLS, READ_ONLY_TOOLS, SAFE_ACTION_TOOLS
from mcp_agent import mcp_tools_to_ollama
from mcp_server import build_server


@pytest.fixture(scope="module")
def tools():
    srv = build_server(object())
    return {t.name: t for t in asyncio.run(srv.list_tools())}


def test_every_tool_registered(tools):
    assert set(tools) == set(ALL_TOOLS)
    assert len(tools) == 22


def test_read_only_annotations(tools):
    for n in READ_ONLY_TOOLS:
        assert tools[n].annotations.readOnlyHint is True, n
    for n in SAFE_ACTION_TOOLS + ACTION_TOOLS:
        assert tools[n].annotations.readOnlyHint is False, n
    for n in ACTION_TOOLS:
        assert tools[n].annotations.destructiveHint is True, n


@pytest.mark.parametrize("name,required", [
    ("takeoff", {"alt_m"}),
    ("goto", {"lat", "lon", "alt_m"}),
    ("goto_relative", {"north_m", "east_m"}),
    ("set_speed", {"speed_ms"}),
    ("set_param", {"name", "value"}),
    ("set_mode", {"mode"}),
    ("get_params", {"names"}),
    ("upload_mission", {"waypoints"}),
])
def test_required_params(tools, name, required):
    assert set(tools[name].inputSchema.get("required", [])) == required


def test_confirm_token_on_confirmed_actions(tools):
    for n in ("arm", "takeoff", "set_param", "upload_mission", "start_mission",
              "clear_pilot_override", "set_mode"):
        assert "confirm_token" in tools[n].inputSchema["properties"], n
    # stop buttons take no arguments at all
    for n in SAFE_ACTION_TOOLS:
        assert not tools[n].inputSchema.get("properties"), n


def test_waypoint_schema_is_explicit(tools):
    s = tools["upload_mission"].inputSchema
    wp = s["$defs"]["Waypoint"]
    assert set(wp["required"]) == {"lat", "lon", "alt_m"}


def test_descriptions_present(tools):
    for t in tools.values():
        assert t.description and len(t.description) > 15, t.name


def test_ollama_conversion(tools):
    out = mcp_tools_to_ollama(tools.values())
    assert len(out) == 22
    f = {o["function"]["name"]: o["function"] for o in out}
    assert f["takeoff"]["parameters"]["properties"]["alt_m"]["type"] == "number"
    assert all(o["type"] == "function" for o in out)
