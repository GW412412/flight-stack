# Tests written by Maya, an AI agent from Sesame, at the direction of G Wichita (GW412412),
# the project owner.
"""Visual centering controller.

Part 1: unit tests with a scripted fake copter and a fake clock (instant).
Part 2: end-to-end through the real stack against fake_vehicle.py:
    CenteringController -> DroneTools.goto_relative/hold -> FlightGate
        -> MavlinkHub -> pymavlink UDP -> FakeVehicle
"""
import math
import socket
import time
from types import SimpleNamespace

import pytest

# Shared fakes and the airborne fixture live in conftest.py (pytest loads the
# fixture automatically; the plain helpers are imported from it).
from conftest import CAM, M_PER_DEG, Clock, FakeTools, free_udp_port, wait_for  # noqa: F401
from visual_centering import (MAX_STEP_CAP_M, CameraMapping, CenteringController,
                              CenteringLimits, Detection, HeadingHistory,
                              SimulatedDownCamera, all_finite, body_to_ned,
                              detection_from_xyxy, proportional_model, valid_heading,
                              validate_cli_args)

def det(clock, cx=320, cy=240, conf=0.9, tid="w1", age=0.0, **kw):
    return Detection(t=clock() - age, target_id=tid, confidence=conf, cx=cx, cy=cy,
                     image_w=640, image_h=480, **kw)


class Feed:
    """Returns whatever `fn(clock)` produces each call (fresh timestamps)."""

    def __init__(self, fn):
        self.fn = fn

    def latest(self):
        return self.fn()


def make(tools, source, clock, camera=CAM, **lim):
    lim.setdefault("poll_s", 0.05)
    return CenteringController(tools, source, camera, CenteringLimits(**lim),
                               clock=clock, sleep=clock.sleep)


def sim_source(tools, clock, fwd, right, camera=CAM, model=None, **kw):
    """Target placed fwd/right (body frame) of the fake copter's start."""
    s = tools.get_status()
    n, e = body_to_ned(fwd, right, s["heading"])
    tgt = (s["lat"] + n / M_PER_DEG,
           s["lon"] + e / (M_PER_DEG * math.cos(math.radians(s["lat"]))))
    return SimulatedDownCamera(tools.get_status, tgt, camera, vision_nudge=model,
                               clock=clock, **kw)


# =============================================================== unit tests
def test_model_nudge_is_used_clamped_and_rotated_by_heading():
    clock, tools = Clock(), FakeTools(heading=90.0)       # nose points east
    feed = Feed(lambda: det(clock, cx=320, cy=100, nudge_forward_m=2.0, nudge_right_m=0.0))
    ctl = make(tools, feed, clock, max_iterations=1)
    ctl.run()
    _, north, east = tools.moves()[0]
    assert abs(north) < 1e-6 and east == pytest.approx(1.0)  # clamped to max_step 1.0, east
    move = [e for e in ctl.log if e.event == "move"][0]
    assert move.detail["source"] == "model+clamped"


def test_model_chooses_a_different_nudge_each_frame():
    clock, tools = Clock(), FakeTools()
    src = sim_source(tools, clock, 1.6, 0.0, model=proportional_model(0.5))
    res = make(tools, src, clock).run()
    sizes = [round(c[1], 3) for c in tools.moves()]
    assert res["centred"]
    assert len(sizes) >= 2 and sizes[0] > sizes[1] > 0        # 0.8, 0.4, ... shrinking


def test_fixed_half_metre_step_without_model():
    clock, tools = Clock(), FakeTools()
    src = sim_source(tools, clock, 1.3, 0.0)
    res = make(tools, src, clock).run()
    sizes = [round(c[1], 3) for c in tools.moves()]
    assert res["centred"] and sizes[:2] == [0.5, 0.5]
    assert sizes[2] == pytest.approx(0.3, abs=0.02)          # last step only what is left


def test_camera_mount_yaw_and_mirroring():
    m = CameraMapping(yaw_offset_deg=90.0)                  # image top = right side
    f, r = m.camera_to_body(1.0, 0.0)
    assert f == pytest.approx(0.0, abs=1e-9) and r == pytest.approx(1.0)
    f, r = CameraMapping(yaw_offset_deg=0.0, mirrored=True).camera_to_body(0.0, 1.0)
    assert r == pytest.approx(-1.0)
    assert body_to_ned(1.0, 0.0, 180.0) == pytest.approx((-1.0, 0.0), abs=1e-9)


def test_rotated_mount_and_heading_still_converge():
    clock, tools = Clock(), FakeTools(heading=135.0)
    cam = CameraMapping(yaw_offset_deg=-90.0, mirrored=True, hfov_deg=80, vfov_deg=64,
                        calibrated=True)
    src = sim_source(tools, clock, 0.9, 1.1, camera=cam)
    res = make(tools, src, clock, camera=cam).run()
    assert res["centred"], res["reason"]


def test_wrong_mount_assumption_is_caught_by_travel_bound():
    """Controller told 0 deg, camera really mounted 180 deg: it walks away from
    the target, and the travel bound stops it."""
    clock, tools = Clock(), FakeTools()
    real = CameraMapping(yaw_offset_deg=180.0, hfov_deg=80, vfov_deg=64)
    src = sim_source(tools, clock, 0.8, 0.0, camera=real)
    res = make(tools, src, clock, max_total_travel_m=1.2).run()
    assert not res["centred"]
    assert "max_total_travel_m" in res["reason"] or "no usable frame" in res["reason"]


@pytest.mark.parametrize("kw,reason", [
    ({"age": 2.0}, "stale frame"),
    ({"conf": 0.2}, "low confidence"),
    ({"cx": 900}, "target outside the frame"),
])
def test_bad_frames_never_move(kw, reason):
    clock, tools = Clock(), FakeTools()
    feed = Feed(lambda: det(clock, cx=kw.get("cx", 500), cy=100,
                            conf=kw.get("conf", 0.9), age=kw.get("age", 0.0)))
    res = make(tools, feed, clock, frame_timeout_s=1.0).run()
    assert tools.moves() == [] and not res["centred"]
    assert "no usable frame" in res["reason"] and reason in res["reason"]


def test_lost_target_no_detection_stops():
    clock, tools = Clock(), FakeTools()
    res = make(tools, Feed(lambda: None), clock, frame_timeout_s=1.0).run()
    assert tools.moves() == [] and "no detection" in res["reason"]


def test_other_target_identity_ignored():
    clock, tools = Clock(), FakeTools()
    feed = Feed(lambda: det(clock, cx=500, cy=100, tid="w2"))
    ctl = CenteringController(tools, feed, CAM, CenteringLimits(frame_timeout_s=1.0),
                              target_id="w1", clock=clock, sleep=clock.sleep)
    res = ctl.run()
    assert tools.moves() == [] and "different target" in res["reason"]


def test_one_move_then_waits_for_fresh_post_move_frame():
    clock, tools = Clock(), FakeTools()
    frozen = det(clock, cx=500, cy=100)                    # same old frame forever
    res = make(tools, Feed(lambda: frozen), clock, frame_timeout_s=1.0,
               max_detection_age_s=50.0).run()
    assert len(tools.moves()) == 1
    assert "frame older than the last move" in res["reason"]


def test_pilot_mode_change_stops_without_reissuing_guided_or_hold():
    clock, tools = Clock(), FakeTools()
    n = {"i": 0}

    def frames():
        n["i"] += 1
        if n["i"] == 3:
            tools.state["mode"] = "LOITER"                 # pilot flicks the switch
        return det(clock, cx=500, cy=100)
    res = make(tools, Feed(frames), clock).run()
    assert "not GUIDED" in res["reason"]
    assert not any(c[0] == "set_mode" for c in tools.calls)
    holds = sum(1 for c in tools.calls if c[0] == "hold")
    assert holds == len(tools.moves())                     # no extra hold/LOITER at the stop


def test_pilot_override_latch_stops_run():
    clock, tools = Clock(), FakeTools()
    tools.gate.override = {"reason": "mode GUIDED->LOITER not requested"}
    res = make(tools, Feed(lambda: det(clock, cx=500)), clock).run()
    assert tools.moves() == [] and "pilot override" in res["reason"]
    assert not any(c[0] in ("set_mode", "hold") for c in tools.calls)


def test_model_nudge_pointing_away_is_refused():
    clock, tools = Clock(), FakeTools()
    # Target is up-right of centre; model says go back-left.
    feed = Feed(lambda: det(clock, cx=500, cy=100, nudge_forward_m=-0.5, nudge_right_m=-0.5))
    res = make(tools, feed, clock, frame_timeout_s=1.0).run()
    assert tools.moves() == []
    assert any(l["event"] == "no_move" and "points away" in l["detail"]["reason"]
               for l in res["log"])


def test_heading_must_match_frame_time():
    clock, tools = Clock(), FakeTools(heading=10.0)
    # Frame stamped with a heading 30 deg different from now: copter yawed.
    feed = Feed(lambda: det(clock, cx=500, cy=100, heading_deg=40.0))
    res = make(tools, feed, clock, frame_timeout_s=1.0).run()
    assert tools.moves() == []
    assert any(l["event"] == "no_move" and "yawed" in l["detail"]["reason"] for l in res["log"])


def test_no_heading_sample_near_frame_time():
    clock, tools = Clock(), FakeTools()
    tools.state["heading"] = None                             # no heading telemetry
    feed = Feed(lambda: det(clock, cx=500, cy=100))           # and none on the frame
    res = make(tools, feed, clock, frame_timeout_s=1.0).run()
    assert tools.moves() == []
    assert any("no heading sample" in l["detail"].get("reason", "") for l in res["log"])


def test_iteration_cap():
    clock, tools = Clock(), FakeTools()
    feed = Feed(lambda: det(clock, cx=500, cy=100))        # never gets centred
    res = make(tools, feed, clock, max_iterations=3, max_total_travel_m=100).run()
    assert len(tools.moves()) == 3 and "max_iterations" in res["reason"]


def test_gate_refusal_stops_run():
    clock, tools = Clock(), FakeTools()
    tools.refuse = "target is 70 m from home > geofence 60 m"
    res = make(tools, Feed(lambda: det(clock, cx=500, cy=100)), clock).run()
    assert len(tools.moves()) == 1 and "geofence" in res["reason"]


def test_rate_limit_waits_and_redecides():
    clock, tools = Clock(), FakeTools()
    blocked = {"n": 2}

    def check():
        if blocked["n"]:
            blocked["n"] -= 1
            return "rate limit: wait 1.0s between actions"
        return None
    tools.gate.rate = SimpleNamespace(check=check, min_interval_s=0.5)
    ctl = make(tools, Feed(lambda: det(clock, cx=500, cy=100)), clock, max_iterations=1)
    ctl.run()
    assert len(tools.moves()) == 1
    assert sum(1 for e in ctl.log if e.event == "wait_rate_limit") == 2


def test_dry_run_plans_one_move_and_stops():
    clock, tools = Clock(), FakeTools(dry_run=True)
    res = make(tools, Feed(lambda: det(clock, cx=500, cy=100)), clock).run()
    assert len(tools.moves()) == 1 and "dry run" in res["reason"]


def test_already_centred_no_move():
    clock, tools = Clock(), FakeTools()
    res = make(tools, Feed(lambda: det(clock, cx=322, cy=241)), clock).run()
    assert res["centred"] and tools.moves() == []


def test_refuses_to_start_on_ground():
    clock, tools = Clock(), FakeTools()
    tools.state.update(armed=False, rel_alt=0.0)
    res = make(tools, Feed(lambda: det(clock, cx=500)), clock).run()
    assert not res["centred"] and tools.moves() == [] and "not armed" in res["reason"]


def test_helpers():
    h = HeadingHistory()
    h.add(10.0, 90.0)
    h.add(10.5, 95.0)
    assert h.at(10.4, 0.2) == 95.0 and h.at(12.0, 0.2) is None
    d = detection_from_xyxy((100, 100, 140, 160), 0.8, "w1", 640, 480, t=1.0)
    assert (d.cx, d.cy) == (120, 130)
    assert Detection.from_dict({"t": 1, "target_id": 3, "confidence": "0.7", "cx": 1,
                                "cy": 2, "image_w": 640, "image_h": 480,
                                "nudge_forward_m": 0.3}).nudge_forward_m == 0.3


def test_udp_json_detection_input():
    import json as _json
    from visual_centering import UdpJsonDetectionSource
    port = free_udp_port()
    src = UdpJsonDetectionSource("127.0.0.1", port)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        tx.sendto(b"not json", ("127.0.0.1", port))
        for t in (5.0, 7.0, 6.0):                  # out-of-order packet is ignored
            tx.sendto(_json.dumps({"t": t, "target_id": "w1", "confidence": 0.9,
                                   "cx": 10, "cy": 20, "image_w": 640, "image_h": 480,
                                   "nudge_right_m": -0.2}).encode(), ("127.0.0.1", port))
        assert wait_for(lambda: src.latest() is not None and src.bad_packets == 1, 3)
        time.sleep(0.3)
        d = src.latest()
        assert d.t == 7.0 and d.nudge_right_m == -0.2 and d.nudge_forward_m is None
    finally:
        tx.close()
        src.close()


def test_cli_refuses_unmeasured_camera(capsys):
    from visual_centering import main
    with pytest.raises(SystemExit):
        main(["--config", "x.toml"])
    assert "camera-calibrated" in capsys.readouterr().err


# ------------------------------------------------ review follow-ups (PR #1)
class SeqFeed:
    """One scripted frame per clock tick, each with a fresh timestamp.

    Each entry is a dict of det() keyword arguments. The last entry repeats."""

    def __init__(self, clock, frames):
        self.clock, self.frames, self.i, self.last_t = clock, list(frames), -1, None

    def latest(self):
        if self.last_t != self.clock():
            self.last_t = self.clock()
            self.i = min(self.i + 1, len(self.frames) - 1)
        return det(self.clock, **self.frames[self.i])


def _events(res, name):
    return [e for e in res["log"] if e["event"] == name]


CENTRED = {"cx": 321, "cy": 241}
OFF = {"cx": 500, "cy": 100}


def test_success_needs_three_consecutive_fresh_centred_frames():
    clock, tools = Clock(), FakeTools()
    res = make(tools, SeqFeed(clock, [CENTRED]), clock).run()
    assert res["centred"] and tools.moves() == []
    assert len(_events(res, "centred_frame")) == 2
    assert len(_events(res, "centred")[0]["detail"]["frames"]) == 3


def test_one_centred_frame_repeated_is_not_success():
    clock, tools = Clock(), FakeTools()
    frozen = det(clock, **CENTRED)                      # the same frame forever
    res = make(tools, Feed(lambda: frozen), clock, frame_timeout_s=1.0,
               max_detection_age_s=50.0).run()
    assert not res["centred"] and tools.moves() == []
    assert "no usable frame" in res["reason"]


def test_off_centre_frame_resets_the_streak_and_counts_restart_after_the_hold():
    clock, tools = Clock(), FakeTools()
    frames = [CENTRED, CENTRED, OFF] + [CENTRED] * 40
    res = make(tools, SeqFeed(clock, frames), clock).run()
    assert res["centred"] and len(tools.moves()) == 1
    names = [e["event"] for e in res["log"]]
    hold_i = names.index("hold")
    assert names[hold_i + 1:].count("centred_frame") == 2      # fresh streak after the hold
    assert names[-1] == "centred"


def test_confirm_frames_is_configurable():
    clock, tools = Clock(), FakeTools()
    res = make(tools, SeqFeed(clock, [CENTRED]), clock, confirm_frames=2).run()
    assert res["centred"] and len(_events(res, "centred_frame")) == 1


def test_t9_heading_65535_is_not_a_heading():
    assert valid_heading(655.35) is None and valid_heading(float("nan")) is None
    assert valid_heading(360.0) is None and valid_heading(-1.0) is None
    assert valid_heading(None) is None and valid_heading(359.99) == 359.99
    h = HeadingHistory()
    h.add(10.0, 655.35)
    h.add(10.1, float("inf"))
    assert h.at(10.0, 0.5) is None
    h.add(10.2, 90.0)
    assert h.at(10.0, 0.5) == 90.0


def test_t9_unknown_vehicle_heading_never_moves():
    clock, tools = Clock(), FakeTools(heading=655.35)
    res = make(tools, Feed(lambda: det(clock, **OFF)), clock, frame_timeout_s=1.0).run()
    assert tools.moves() == [] and not res["centred"]


def test_t9_unknown_heading_on_the_frame_is_skipped():
    clock, tools = Clock(), FakeTools()
    res = make(tools, Feed(lambda: det(clock, heading_deg=655.35, **OFF)), clock,
               frame_timeout_s=1.0).run()
    assert tools.moves() == [] and "invalid heading" in res["reason"]


@pytest.mark.parametrize("bad", [
    {"cx": float("nan"), "cy": 100},
    {"cx": 500, "cy": float("inf")},
    {"cx": 500, "cy": 100, "age": float("nan")},
    {"cx": 500, "cy": 100, "conf": float("nan")},
    {"cx": 500, "cy": 100, "nudge_right_m": float("nan")},
    {"cx": 500, "cy": 100, "nudge_forward_m": float("-inf")},
])
def test_non_finite_frame_is_skipped_and_the_run_carries_on(bad):
    clock, tools = Clock(), FakeTools()
    res = make(tools, SeqFeed(clock, [bad, bad, CENTRED]), clock).run()
    assert res["centred"], res["reason"]
    assert tools.moves() == []
    assert any("non-finite" in e["detail"].get("reason", "") for e in _events(res, "skip_frame"))


def test_all_finite_helper():
    assert all_finite(1, 2.5, None) and not all_finite(1, float("nan"))
    assert not all_finite(float("inf")) and not all_finite("x")


def test_max_step_capped_at_one_metre():
    clock, tools = Clock(), FakeTools()
    assert MAX_STEP_CAP_M == 1.0
    with pytest.raises(ValueError, match="max_step_m"):
        make(tools, Feed(lambda: None), clock, max_step_m=1.5)
    make(tools, Feed(lambda: None), clock, max_step_m=0.6)        # lower is fine


def test_uncalibrated_camera_refused_at_run_start():
    clock, tools = Clock(), FakeTools()
    cam = CameraMapping(yaw_offset_deg=0.0, hfov_deg=80.0, vfov_deg=64.0)
    res = make(tools, Feed(lambda: det(clock, **OFF)), clock, camera=cam).run()
    assert not res["centred"] and tools.moves() == [] and "not calibrated" in res["reason"]


def _cli(**kw):
    a = dict(camera_yaw_deg=0.0, camera_calibrated=False, plan_only=False, live=False,
             max_step_m=1.0, confirm_frames=3)
    a.update(kw)
    return SimpleNamespace(**a)


def test_cli_plan_only_needs_no_calibration_flag():
    assert validate_cli_args(_cli(plan_only=True)) is None
    assert "camera-calibrated" in validate_cli_args(_cli())
    assert validate_cli_args(_cli(camera_calibrated=True)) is None
    assert "camera-yaw-deg" in validate_cli_args(_cli(camera_yaw_deg=None, plan_only=True))
    assert "--live" in validate_cli_args(_cli(plan_only=True, live=True))


def test_cli_caps_max_step_and_checks_confirm_frames(capsys):
    assert "max-step-m" in validate_cli_args(_cli(camera_calibrated=True, max_step_m=1.01))
    assert "max-step-m" in validate_cli_args(_cli(camera_calibrated=True, max_step_m=0.0))
    assert "confirm-frames" in validate_cli_args(_cli(camera_calibrated=True, confirm_frames=0))
    from visual_centering import main
    with pytest.raises(SystemExit):
        main(["--config", "x.toml", "--camera-yaw-deg", "0", "--camera-calibrated",
              "--max-step-m", "2"])
    assert "max-step-m" in capsys.readouterr().err


def test_plan_only_skips_non_finite_frame():
    clock, tools = Clock(), FakeTools()
    ctl = make(tools, Feed(lambda: None), clock)
    out = ctl.plan_only(det(clock, cx=float("nan")), tools.get_status())
    assert "non-finite" in out["skipped"]
    out = ctl.plan_only(det(clock, **OFF), dict(tools.get_status(), heading=655.35))
    assert "ned" not in out                      # no valid heading: no earth-frame move shown


# =============================================================== end to end
def _e2e_source(t, fwd, right, model=None):
    s = t.node.get_status()
    tgt = (s["lat"] + fwd / M_PER_DEG,       # fake vehicle heading is 0 (north)
           s["lon"] + right / (M_PER_DEG * math.cos(math.radians(s["lat"]))))
    return SimulatedDownCamera(t.node.get_status, tgt, CAM, vision_nudge=model), tgt


def test_e2e_model_nudges_centre_target_through_gate(airborne):
    t, fv = airborne
    src, tgt = _e2e_source(t, 1.4, -0.9, model=proportional_model(0.8))
    seen_before = list(fv.commands_seen)
    res = CenteringController(t, src, CAM).run()
    assert res["centred"], res["reason"]
    s = t.node.get_status()
    from mav_node import haversine_m
    assert haversine_m(s["lat"], s["lon"], *tgt) < 0.35     # under the crosshair
    new = fv.commands_seen[len(seen_before):]
    assert new.count("SET_POSITION_TARGET_GLOBAL_INT") >= res["moves"]   # moves + holds
    assert "MAV_CMD_DO_SET_MODE" not in new and "MAV_CMD_COMPONENT_ARM_DISARM" not in new
    assert fv.mode == "GUIDED"


def test_e2e_fixed_steps(airborne):
    t, fv = airborne
    src, _ = _e2e_source(t, 0.0, 1.3)
    res = CenteringController(t, src, CAM).run()
    assert res["centred"], res["reason"]
    steps = [l["detail"]["ned"]["east"] for l in res["log"] if l["event"] == "move"]
    assert steps[0] == pytest.approx(0.5, abs=0.01) and all(s <= 0.501 for s in steps)


def test_e2e_pilot_takeover_mid_run(airborne):
    t, fv = airborne
    src, _ = _e2e_source(t, 2.5, 0.0)
    calls = {"n": 0}
    real_latest = src.latest

    def latest():
        calls["n"] += 1
        if calls["n"] == 15:
            fv.simulate_pilot_mode("LOITER")              # pilot grabs the sticks
        return real_latest()
    src.latest = latest
    before = list(fv.commands_seen)
    res = CenteringController(t, src, CAM, CenteringLimits(default_step_m=0.3)).run()
    assert not res["centred"]
    assert "pilot override" in res["reason"] or "not GUIDED" in res["reason"]
    time.sleep(0.5)
    assert fv.mode == "LOITER"                               # left with the pilot
    assert "MAV_CMD_DO_SET_MODE" not in fv.commands_seen[len(before):]


def test_e2e_dry_run_transmits_no_movement(airborne):
    t, fv = airborne
    t.hub.dry_run = True
    src, _ = _e2e_source(t, 1.0, 0.0)
    target_before = fv.target
    before = list(fv.commands_seen)
    res = CenteringController(t, src, CAM).run()
    assert res["moves"] == 1 and "dry run" in res["reason"]
    time.sleep(0.3)
    assert fv.target == target_before
    assert "SET_POSITION_TARGET_GLOBAL_INT" not in fv.commands_seen[len(before):]
