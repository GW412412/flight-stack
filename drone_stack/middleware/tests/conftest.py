"""pytest setup: make the middleware modules importable as top-level modules
(they import each other as `from mav_node import ...`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================ shared fakes
# Used by test_visual_centering.py and test_vision_bridge.py (and any later
# test that needs a fake copter). Moved here from test_visual_centering.py
# after Grok's review of PR #3, so one test module no longer imports another.
import math  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from visual_centering import CameraMapping  # noqa: E402

CAM = CameraMapping(yaw_offset_deg=0.0, hfov_deg=80.0, vfov_deg=64.0, calibrated=True)
M_PER_DEG = 111_320.0


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class FakeGate:
    def __init__(self):
        self.override = None
        self.rate = SimpleNamespace(check=lambda: None, min_interval_s=1.0)

    def pilot_override(self):
        return self.override


class FakeTools:
    """Instant-move copter. Records every call the controller makes."""

    def __init__(self, heading=0.0, mode="GUIDED", dry_run=False):
        self.state = dict(link_ok=True, mode=mode, armed=True, rel_alt=6.0,
                          lat=-35.363261, lon=149.165230, heading=heading)
        self.gate = FakeGate()
        self.hub = SimpleNamespace(dry_run=dry_run)
        self.calls = []
        self.refuse = None

    def get_status(self):
        return dict(self.state)

    def goto_relative(self, north_m, east_m, alt_m=None, wait_s=0):
        self.calls.append(("goto_relative", north_m, east_m))
        if self.refuse:
            return {"ok": False, "blocked": True, "reasons": [self.refuse]}
        lat = self.state["lat"] + north_m / M_PER_DEG
        lon = self.state["lon"] + east_m / (M_PER_DEG * math.cos(math.radians(self.state["lat"])))
        if not self.hub.dry_run:
            self.state.update(lat=lat, lon=lon)
        return {"ok": True, "dry_run": self.hub.dry_run,
                "target": {"lat": lat, "lon": lon, "alt_m": 6.0}}

    def hold(self):
        self.calls.append(("hold",))
        return {"ok": True, "held_at": {}}

    def set_mode(self, mode):          # must never be called by the controller
        self.calls.append(("set_mode", mode))
        return {"ok": True}

    def moves(self):
        return [c for c in self.calls if c[0] == "goto_relative"]


# ======================================================= end-to-end helpers
def free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for(pred, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def airborne(tmp_path):
    """Real DroneTools stack on the fake vehicle, hovering at 6 m in GUIDED.
    The TEST arms and takes off; the controller never does."""
    from drone_tools import DroneTools
    from fake_vehicle import FakeVehicle
    from mav_node import MavNode
    from mavlink_hub import MavlinkHub
    from safety_gate import FlightGate
    from stack_config import SafetyConfig

    port = free_udp_port()
    node = MavNode(f"udpin:127.0.0.1:{port}", record_dir=None, send_heartbeat=True)
    node.start(wait_heartbeat=False)
    fv = FakeVehicle(out=f"127.0.0.1:{port}").start()
    cfg = SafetyConfig(actuation_enabled=True, dry_run=False, geofence_radius_m=60,
                       max_alt_m=20, min_action_interval_s=0, max_actions_per_minute=1000)
    gate = FlightGate(cfg)
    hub = MavlinkHub(node, flight_gate=gate, dry_run=False, log_dir=str(tmp_path))
    t = DroneTools(node, hub, gate, log_dir=str(tmp_path), home_poll_s=0.5)
    assert wait_for(lambda: node.link_ok() and node.home() is not None)
    assert t.set_mode("GUIDED")["ok"]
    assert wait_for(lambda: node.get_status()["mode"] == "GUIDED")
    r = t.arm()
    assert t.arm(confirm_token=r["confirm_token"])["ok"]
    assert wait_for(lambda: node.get_status()["armed"])
    r = t.takeoff(alt_m=6)
    assert t.takeoff(alt_m=6, confirm_token=r["confirm_token"])["ok"]
    assert wait_for(lambda: (node.get_status()["rel_alt"] or 0) > 5.9)
    time.sleep(0.5)
    yield t, fv
    t.close()
    fv.stop()
