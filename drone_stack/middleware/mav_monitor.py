#!/usr/bin/env python3
"""mav_monitor.py - watch the MAVLink data flow coming off the Pixhawk.

PASSIVE: listens on MAVProxy's monitor output (udpin:127.0.0.1:14565 by
default) and never transmits - no heartbeat, no stream requests, no
commands. Safe to start and stop at any time, in flight or not.

What it shows (refreshed every --interval seconds):

  * vehicle summary   - mode, armed, altitude, battery, GPS fix, EKF, RC link
  * link health       - heartbeat age, packets/s, bytes/s, packet LOSS per
                        source (from MAVLink sequence numbers), bad data
  * per-message rates - Hz for every message type seen in the last 5 s
  * STATUSTEXT        - the last few autopilot messages ("PreArm: ..." etc.)
  * pending LLM confirmations - read from logs/llm-stack/confirmations.jsonl,
                        so the operator can see (and in operator confirm mode,
                        read out) the token the LLM is waiting for

What it records (in --log-dir, default logs/monitor/):

  * monitor_<stamp>.tlog        - raw telemetry log; open with MAVExplorer,
                                  QGC log replay or `mavlogdump.py`
  * monitor_<stamp>_health.csv  - one row per interval: rates, loss, hb age,
                                  mode, altitude, battery
  * monitor_<stamp>_rates.csv   - long format: timestamp,msg_type,rate_hz

Optional --web PORT serves the same snapshot as a tiny auto-refreshing page
(http://127.0.0.1:PORT/) and JSON (/api/snapshot). Stdlib only, no extra deps.

Examples:
  python mav_monitor.py                                  # terminal dashboard
  python mav_monitor.py --web 8766                       # + browser view
  python mav_monitor.py --url tcp:127.0.0.1:5762         # SITL 2nd serial port
  python mav_monitor.py --once 5 --json                  # 5 s sample, print JSON
  python mav_monitor.py --headless --web 8766            # as a service (systemd)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import struct
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mav_node import MavNode  # noqa: E402


# ---------------------------------------------------------------- snapshot
def pending_confirmations(path: Optional[str], ttl_s: float = 60.0) -> list:
    """Confirmation requests from the MCP server still inside their TTL.
    (The server may already have consumed them; this is a convenience view.)"""
    if not path or not os.path.exists(path):
        return []
    now, out = time.time(), []
    try:
        with open(path) as fh:
            lines = fh.readlines()[-50:]
    except OSError:
        return []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if now - e.get("t", 0) <= ttl_s:
            out.append({"age_s": round(now - e["t"], 1), "action": e.get("action"),
                        "readback": e.get("readback"), "token": e.get("token")})
    return out


def build_snapshot(node: MavNode, confirm_log: Optional[str]) -> dict:
    """Everything the dashboard shows, as one JSON-able dict."""
    st = node.get_status()
    tel = node.get_telemetry()
    flow = node.stats.snapshot()
    vehicle_src = (f"{node.vehicle_sysid}:{node.vehicle_compid}"
                   if node.vehicle_sysid is not None else None)
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "connection": node.connection,
        "vehicle": {
            "src": vehicle_src, "mode": st.get("mode"), "armed": st.get("armed"),
            "rel_alt_m": st.get("rel_alt"), "battery_pct": st.get("battery"),
            "voltage_v": st.get("voltage"), "gps_fix": st.get("fix_type"),
            "sats": (tel.get("gps") or {}).get("satellites"),
            "ekf_position_ok": (tel.get("ekf") or {}).get("position_ok"),
            "rc_link_ok": st.get("rc_link_ok"),
            "dist_from_home_m": st.get("dist_from_home_m"),
        },
        "link": {
            "link_ok": node.link_ok(),
            "heartbeat_age_s": node.heartbeat_age(),
            "total_rate_hz": flow.get("total_rate_hz"),
            "bytes_per_s": flow.get("bytes_per_s"),
            "bad_data": flow.get("bad_data"),
            "sources": flow.get("sources", {}),
            "uptime_s": flow.get("uptime_s"),
        },
        "rates_hz": flow.get("rates_hz", {}),
        "statustext": list(node.statustext)[-6:],
        "pending_confirmations": pending_confirmations(confirm_log),
    }


# ---------------------------------------------------------------- terminal
def render(snap: dict) -> str:
    """Plain-text dashboard (ANSI clear-screen added by the caller)."""
    v, link = snap["vehicle"], snap["link"]
    ok = "OK " if link["link_ok"] else "DOWN"
    hb = link["heartbeat_age_s"]
    lines = [
        f"MAVLink monitor  {snap['time']}   {snap['connection']}",
        f"LINK {ok}  heartbeat age {hb if hb is None else round(hb, 1)} s   "
        f"{link['total_rate_hz']} msg/s   {link['bytes_per_s']} B/s   "
        f"bad {link['bad_data']}",
        f"VEHICLE {v['src']}  mode {v['mode']}  armed {v['armed']}  "
        f"alt {v['rel_alt_m']} m  batt {v['battery_pct']}% {v['voltage_v']} V  "
        f"gps fix {v['gps_fix']} sats {v['sats']}  ekf_pos {v['ekf_position_ok']}  "
        f"rc {v['rc_link_ok']}  home {v['dist_from_home_m']} m",
        "",
        "SOURCES (sysid:compid)   received   lost   loss%   hb age",
    ]
    for src, s in sorted(link["sources"].items()):
        lines.append(f"  {src:<22} {s.get('received', 0):>8} {s.get('lost', 0):>6} "
                     f"{s.get('loss_pct', 0):>7} {s.get('heartbeat_age_s')}")
    lines += ["", "MESSAGE RATES (Hz, last 5 s)"]
    rates = sorted(snap["rates_hz"].items(), key=lambda kv: -kv[1])
    for i in range(0, len(rates), 3):     # three columns
        lines.append("  " + "".join(f"{k[:24]:<25}{r:>6.1f}   " for k, r in rates[i:i + 3]))
    if snap["statustext"]:
        lines += ["", "STATUSTEXT"]
        lines += [f"  [{s['severity']}] {s['text']}" for s in snap["statustext"]]
    if snap["pending_confirmations"]:
        lines += ["", "PENDING LLM CONFIRMATIONS (read the token to the operator only "
                      "if they approve)"]
        lines += [f"  {c['action']:<16} token {c['token']}  ({c['age_s']} s)  "
                  f"{c['readback']}" for c in snap["pending_confirmations"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- CSV logs
class CsvLogger:
    """Health row + per-type rate rows each interval."""

    HEALTH_COLS = ["timestamp", "link_ok", "heartbeat_age_s", "total_rate_hz",
                   "bytes_per_s", "vehicle_loss_pct", "bad_data", "mode", "armed",
                   "rel_alt_m", "battery_pct", "gps_fix"]

    def __init__(self, log_dir: str, stamp: str):
        self.health_f = open(os.path.join(log_dir, f"monitor_{stamp}_health.csv"), "w",
                             newline="")
        self.rates_f = open(os.path.join(log_dir, f"monitor_{stamp}_rates.csv"), "w",
                            newline="")
        self.health = csv.writer(self.health_f)
        self.rates = csv.writer(self.rates_f)
        self.health.writerow(self.HEALTH_COLS)
        self.rates.writerow(["timestamp", "msg_type", "rate_hz"])

    def write(self, snap: dict):
        t = time.time()
        v, link = snap["vehicle"], snap["link"]
        loss = (link["sources"].get(v["src"]) or {}).get("loss_pct") if v["src"] else None
        self.health.writerow([round(t, 2), link["link_ok"], link["heartbeat_age_s"],
                              link["total_rate_hz"], link["bytes_per_s"], loss,
                              link["bad_data"], v["mode"], v["armed"], v["rel_alt_m"],
                              v["battery_pct"], v["gps_fix"]])
        for k, r in snap["rates_hz"].items():
            self.rates.writerow([round(t, 2), k, r])
        self.health_f.flush()
        self.rates_f.flush()

    def close(self):
        self.health_f.close()
        self.rates_f.close()


# ---------------------------------------------------------------- web view
_PAGE = """<!doctype html><meta charset=utf-8><title>MAVLink monitor</title>
<style>body{font:14px monospace;background:#111;color:#ddd;margin:1em}
pre{white-space:pre-wrap}</style>
<h3>MAVLink monitor (read-only)</h3><pre id=o>loading...</pre>
<script>
async function t(){try{const r=await fetch('/api/snapshot');
document.getElementById('o').textContent=JSON.stringify(await r.json(),null,1)}
catch(e){document.getElementById('o').textContent='monitor not reachable: '+e}}
t();setInterval(t,1000);</script>"""


def serve_web(port: int, get_snapshot, host: str = "127.0.0.1"):
    """Background HTTP server. Binds to localhost by default - put it behind
    SSH port-forwarding rather than opening it on the field network."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/snapshot"):
                body, ctype = json.dumps(get_snapshot(), default=str).encode(), \
                    "application/json"
            elif self.path in ("/", "/index.html"):
                body, ctype = _PAGE.encode(), "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):   # keep the terminal dashboard clean
            pass

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="monitor-web").start()
    return srv


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default=os.environ.get("MONITOR_URL", "udpin:127.0.0.1:14565"),
                    help="pymavlink URL to listen on (default MAVProxy monitor output)")
    ap.add_argument("--log-dir", default="logs/monitor")
    ap.add_argument("--no-tlog", action="store_true", help="do not write a .tlog")
    ap.add_argument("--no-csv", action="store_true", help="do not write CSV files")
    ap.add_argument("--interval", type=float, default=1.0, help="refresh seconds")
    ap.add_argument("--web", type=int, default=0, metavar="PORT",
                    help="also serve http://127.0.0.1:PORT/ (e.g. 8766)")
    ap.add_argument("--web-host", default="127.0.0.1")
    ap.add_argument("--confirm-log", default="logs/llm-stack/confirmations.jsonl",
                    help="MCP server confirmation log to show pending tokens")
    ap.add_argument("--once", type=float, default=0, metavar="SECONDS",
                    help="listen this long, print one snapshot, exit")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the dashboard")
    ap.add_argument("--no-clear", action="store_true", help="do not clear the screen")
    ap.add_argument("--headless", action="store_true",
                    help="no terminal output (service mode: tlog/CSV/web only)")
    args = ap.parse_args(argv)

    os.makedirs(args.log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Passive: no heartbeat, no stream requests, no CSV from MavNode itself.
    node = MavNode(args.url, record_dir=None, source_system=252, source_component=191,
                   send_heartbeat=False, request_streams=False)
    tlog = None
    if not args.no_tlog:
        # .tlog = 8-byte big-endian microsecond timestamp + raw MAVLink packet.
        # Written from a pymavlink message hook because the UDP transport
        # bypasses mavfile.setup_logfile().
        tlog = open(os.path.join(args.log_dir, f"monitor_{stamp}.tlog"), "wb")

        def _tlog_hook(_conn, msg):
            if msg.get_type() != "BAD_DATA":
                tlog.write(struct.pack(">Q", int(time.time() * 1e6) & ~3) + msg.get_msgbuf())
        node.conn.message_hooks.append(_tlog_hook)
    node.start(wait_heartbeat=False)
    csvlog = None if args.no_csv else CsvLogger(args.log_dir, stamp)

    def snap():
        return build_snapshot(node, args.confirm_log)

    if args.web:
        serve_web(args.web, snap, args.web_host)
        print(f"web view on http://{args.web_host}:{args.web}/", file=sys.stderr)

    try:
        if args.once:
            time.sleep(args.once)
            s = snap()
            print(json.dumps(s, indent=1, default=str) if args.json else render(s))
            if csvlog:
                csvlog.write(s)
            return 0 if s["link"]["link_ok"] else 1
        while True:
            time.sleep(args.interval)
            s = snap()
            if csvlog:
                csvlog.write(s)
            if args.headless:
                continue
            if args.json:
                print(json.dumps(s, default=str), flush=True)
            else:
                out = render(s)
                print(out if args.no_clear else "\033[2J\033[H" + out, flush=True)
    except KeyboardInterrupt:
        return 0
    finally:
        node.stop()
        if tlog:
            tlog.close()
        if csvlog:
            csvlog.close()


if __name__ == "__main__":
    sys.exit(main())
