"""Build real pymavlink messages with a chosen source sysid/compid, and a MavNode
that has no socket, so tests can drive MavNode._handle() directly."""
import os
from types import SimpleNamespace

os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

M = mavutil.mavlink


def make_msg(name, *args, sysid=1, compid=1):
    enc = M.MAVLink(None, srcSystem=sysid, srcComponent=compid)
    buf = getattr(enc, f"{name}_encode")(*args).pack(enc)
    dec = M.MAVLink(None)
    return dec.parse_char(buf)


def heartbeat(sysid=1, compid=1, mtype=None, autopilot=None, mode=4, armed=False):
    return make_msg("heartbeat",
                    M.MAV_TYPE_QUADROTOR if mtype is None else mtype,
                    M.MAV_AUTOPILOT_ARDUPILOTMEGA if autopilot is None else autopilot,
                    mode | (M.MAV_MODE_FLAG_SAFETY_ARMED if armed else 0) | 1, mode,
                    M.MAV_STATE_ACTIVE, sysid=sysid, compid=compid)


def position(lat=-35.0, lon=149.0, rel_alt_m=10.0, **kw):
    return make_msg("global_position_int", 0, int(lat * 1e7), int(lon * 1e7), 0,
                    int(rel_alt_m * 1000), 0, 0, 0, 0, **kw)


def sys_status(battery=80, **kw):
    return make_msg("sys_status", 0, 0, 0, 0, 12000, 0, battery, 0, 0, 0, 0, 0, 0, **kw)


def attitude(**kw):
    return make_msg("attitude", 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, **kw)


def gps_raw(fix=3, **kw):
    return make_msg("gps_raw_int", 0, fix, 0, 0, 0, 100, 100, 0, 0, 10, **kw)


def ekf(flags=1 | 2 | 4 | 8 | 16 | 32, **kw):
    return make_msg("ekf_status_report", flags, 0.1, 0.1, 0.1, 0.1, 0.1, **kw)


def bare_node(**kw):
    from mav_node import MavNode
    conn = SimpleNamespace(target_system=0, target_component=0)
    return MavNode(conn=conn, record_dir=None, **kw)


def feed(node, msgs):
    """Run the node's real reader loop over `msgs` (synchronously), then stop."""
    queue = list(msgs)

    def recv_match(blocking=True, timeout=None):
        if queue:
            return queue.pop(0)
        node._stop.set()
        return None
    node.conn.recv_match = recv_match
    node._reader()
