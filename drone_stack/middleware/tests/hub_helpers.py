"""Stub MavNode for MavlinkHub tests (no sockets)."""
import queue
import threading
from types import SimpleNamespace

ACK_WAIT_S = 4.0        # how long the stuck command waits for its ACK
FAST_S = 1.0            # LAND/RTL must return well inside this


class StubNode:
    """Just enough MavNode for the hub. ACKs only for commands in `acks`."""

    def __init__(self, acks=()):
        self.acks = set(acks)
        self.sent = []
        self.send_lock = threading.Lock()
        self.vehicle_sysid, self.vehicle_compid = 1, 1
        self._waiters = []
        mav = SimpleNamespace(
            command_long_send=self._command_long,
            set_position_target_global_int_send=lambda *a: self.sent.append(("goto", a)),
            mission_count_send=lambda *a: self.sent.append(("mission_count", a)),
            param_set_send=lambda *a: self.sent.append(("param_set", a)))
        self.conn = SimpleNamespace(mav=mav, target_system=1, target_component=1)

    status = {}

    def get_status(self):
        return dict(self.status)

    def mode_mapping(self):
        return {"LAND": 9, "RTL": 6, "LOITER": 5}

    def subscribe(self, types, pred=None):
        w = SimpleNamespace(q=queue.Queue(), types=set(types), pred=pred or (lambda m: True))
        self._waiters.append(w)
        return w

    def unsubscribe(self, w):
        self._waiters.remove(w)

    def wait(self, w, timeout):
        try:
            return w.q.get(timeout=min(timeout, ACK_WAIT_S))
        except queue.Empty:
            return None

    def _command_long(self, sysid, compid, command, conf, *params):
        self.sent.append((command, params))
        if command in self.acks:
            ack = SimpleNamespace(command=command, result=0)
            for w in list(self._waiters):
                if "COMMAND_ACK" in w.types and w.pred(ack):
                    w.q.put(ack)
