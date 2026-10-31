"""P1 #4: a slow COMMAND_ACK (or mission upload) in one thread must not delay
LAND / RTL / hold / pump-off in another."""
import threading
import time

from hub_helpers import ACK_WAIT_S, FAST_S, StubNode
from mavlink_hub import M, MavlinkHub


def make_hub(tmp_path):
    return MavlinkHub(StubNode(acks=[M.MAV_CMD_DO_SET_MODE, M.MAV_CMD_NAV_TAKEOFF]),
                      dry_run=False, log_dir=str(tmp_path), ack_timeout_s=ACK_WAIT_S)


def timed(fn, *a, **k):
    t0 = time.monotonic()
    r = fn(*a, **k)
    return r, time.monotonic() - t0


def start(fn, *a, **k):
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn(*a, **k)), daemon=True)
    t.start()
    return t, out


def wait_sent(node, command, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if any(c == command for c, _ in node.sent):
            return
        time.sleep(0.01)
    raise AssertionError(f"{command} never sent")


def test_land_and_rtl_are_not_queued_behind_a_pending_ack(tmp_path):
    hub = make_hub(tmp_path)
    stuck, out = start(hub.arm, True)               # FC never ACKs ARM
    wait_sent(hub.node, M.MAV_CMD_COMPONENT_ARM_DISARM)
    for mode in ("LAND", "RTL"):
        r, dt = timed(hub.send_mode, mode)
        assert r["ok"] and dt < FAST_S, (mode, dt)
    assert stuck.is_alive(), "the ARM should still be waiting for its ACK"
    stuck.join(ACK_WAIT_S + 2)
    assert out["r"]["ack"] == "TIMEOUT"


def test_goto_and_pump_off_are_not_queued_behind_a_pending_ack(tmp_path):
    hub = make_hub(tmp_path)
    stuck, _ = start(hub.arm, True)
    wait_sent(hub.node, M.MAV_CMD_COMPONENT_ARM_DISARM)
    _, dt = timed(hub.goto, -37.0, 144.0, 10)
    assert dt < FAST_S
    r, dt = timed(hub.force_pump_off)
    assert r["ok"] and dt < FAST_S
    stuck.join(ACK_WAIT_S + 2)


def test_land_is_not_queued_behind_a_mission_upload(tmp_path):
    hub = make_hub(tmp_path)
    up, _ = start(hub.upload_mission, [{"lat": -37.0, "lon": 144.0, "alt_m": 10}],
                  timeout=ACK_WAIT_S)               # nobody answers MISSION_COUNT
    wait_sent(hub.node, "mission_count")
    r, dt = timed(hub.send_mode, "LAND")
    assert r["ok"] and dt < FAST_S
    up.join(ACK_WAIT_S + 5)


def test_concurrent_commands_each_get_their_own_ack(tmp_path):
    hub = make_hub(tmp_path)
    results = []
    threads = [threading.Thread(target=lambda m=m: results.append(hub.send_mode(m)))
               for m in ("LAND", "RTL", "LOITER", "LAND")]
    [t.start() for t in threads]
    [t.join(FAST_S * 3) for t in threads]
    assert len(results) == 4 and all(r["ok"] for r in results)


def test_mission_transactions_still_serialise(tmp_path):
    """The lock is kept for multi-step transactions: two uploads never interleave."""
    hub = make_hub(tmp_path)
    wp = [{"lat": -37.0, "lon": 144.0, "alt_m": 10}]
    a, _ = start(hub.upload_mission, wp, timeout=1.0)
    wait_sent(hub.node, "mission_count")
    b, _ = start(hub.upload_mission, wp, timeout=1.0)
    time.sleep(0.3)
    assert sum(1 for c, _ in hub.node.sent if c == "mission_count") == 1
    a.join(5)
    b.join(5)
