"""Link / data-flow statistics for a MAVLink stream. Pure Python, no I/O.

Used by both the MCP server (tool `get_data_flow`) and the standalone
monitor (`mav_monitor.py`), so "message rate" means the same thing in both.

What it measures, per stream:
  * message rate per type (Hz) over a sliding window (default 5 s)
  * packets received / lost, from the MAVLink sequence number, which every
    sender increments by 1 per packet (per sysid/compid). A jump of N means
    N-1 packets were lost somewhere between the FC and us.
  * heartbeat age per source (sysid, compid) - the link-health signal
  * bytes/s (approximate, from the packed message length)

Feed it with `observe(msg)` for every message, read it with `snapshot()`.
Thread-safe: the reader thread writes, tool calls read.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple


class LinkStats:
    def __init__(self, window_s: float = 5.0, clock=time.monotonic):
        self.window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        # msg type -> deque of arrival times inside the window
        self._arrivals: Dict[str, Deque[float]] = defaultdict(deque)
        self._counts: Dict[str, int] = defaultdict(int)      # lifetime count per type
        self._last_seq: Dict[Tuple[int, int], int] = {}      # (sysid, compid) -> seq
        self._received: Dict[Tuple[int, int], int] = defaultdict(int)
        self._lost: Dict[Tuple[int, int], int] = defaultdict(int)
        self._last_hb: Dict[Tuple[int, int], float] = {}
        self._bytes: Deque[Tuple[float, int]] = deque()
        self._bad_data = 0
        self._started = clock()

    # ------------------------------------------------------------------ write
    def observe(self, msg) -> None:
        """Record one pymavlink message (call from the reader thread)."""
        now = self._clock()
        mtype = msg.get_type()
        with self._lock:
            if mtype == "BAD_DATA":
                self._bad_data += 1
                return
            dq = self._arrivals[mtype]
            dq.append(now)
            self._counts[mtype] += 1
            try:
                src = (msg.get_srcSystem(), msg.get_srcComponent())
                seq = msg.get_seq()
            except Exception:       # synthetic messages in tests may lack these
                src, seq = None, None
            if src is not None and seq is not None:
                self._received[src] += 1
                prev = self._last_seq.get(src)
                if prev is not None:
                    gap = (seq - prev - 1) % 256    # seq wraps at 255
                    # A huge "gap" is almost always a sender reboot, not loss.
                    if 0 < gap < 128:
                        self._lost[src] += gap
                self._last_seq[src] = seq
                if mtype == "HEARTBEAT":
                    self._last_hb[src] = now
            try:
                self._bytes.append((now, len(msg.get_msgbuf())))
            except Exception:
                pass
            self._trim(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_s
        for dq in self._arrivals.values():
            while dq and dq[0] < cutoff:
                dq.popleft()
        while self._bytes and self._bytes[0][0] < cutoff:
            self._bytes.popleft()

    # ------------------------------------------------------------------- read
    def heartbeat_age(self, sysid: Optional[int] = None) -> Optional[float]:
        """Seconds since the last HEARTBEAT (from `sysid`, or from anyone)."""
        with self._lock:
            ages = [self._clock() - t for (s, _c), t in self._last_hb.items()
                    if sysid is None or s == sysid]
        return min(ages) if ages else None

    def snapshot(self) -> dict:
        """JSON-friendly summary: rates, loss, heartbeat ages, throughput."""
        now = self._clock()
        with self._lock:
            self._trim(now)
            span = min(self.window_s, max(now - self._started, 1e-6))
            rates = {t: round(len(dq) / span, 2) for t, dq in self._arrivals.items() if dq}
            sources = {}
            for src, rx in self._received.items():
                lost = self._lost.get(src, 0)
                hb = self._last_hb.get(src)
                sources[f"{src[0]}:{src[1]}"] = {
                    "received": rx,
                    "lost": lost,
                    "loss_pct": round(100.0 * lost / (rx + lost), 2) if rx + lost else 0.0,
                    "heartbeat_age_s": round(now - hb, 2) if hb is not None else None,
                }
            bps = sum(n for _t, n in self._bytes) / span
            return {
                "window_s": self.window_s,
                "total_rate_hz": round(sum(rates.values()), 2),
                "bytes_per_s": round(bps, 1),
                "rates_hz": dict(sorted(rates.items(), key=lambda kv: -kv[1])),
                "lifetime_counts": dict(self._counts),
                "sources": sources,
                "bad_data": self._bad_data,
                "uptime_s": round(now - self._started, 1),
            }
