"""Configuration for the 'LLM behind the sticks' stack.

One small, typed config object shared by the MCP server, the hub, the safety
gate and the monitor. Values come from (lowest to highest priority):

    1. The dataclass defaults below (conservative: actuation OFF).
    2. A TOML file  (drone_stack/config/llm_stack.sitl.toml or
                     drone_stack/config/llm_stack.pixhawk.toml).
    3. Environment variables (handy for docker / systemd / one-off tests):
         DRONE_STACK_CONFIG   path to the TOML file
         MAVLINK_URL          e.g. udpin:127.0.0.1:14560 or tcp:127.0.0.1:5760
         DRONE_DRY_RUN        1/0  -> safety.dry_run
         DRONE_ACTUATION      1/0  -> safety.actuation_enabled
         DRONE_CONFIRM_MODE   echo|operator -> safety.confirm_mode
         MCP_HOST / MCP_PORT  streamable-HTTP bind address

Why a dataclass and not a dict: every limit the LLM can bump into is spelled
out here with a comment, so reviewing "what can the model do?" is one file.
Python 3.11+ ships `tomllib`, so no extra dependency is needed.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MavlinkConfig:
    # Where the MCP server listens for MAVLink. MAVProxy's `--out
    # udp:127.0.0.1:14560` sends to this port, so we bind with `udpin:`.
    # (Port 14560 is the "conversation model" endpoint in
    #  config/mavlink-router.conf and the stack report, kept for continuity.)
    url: str = "udpin:127.0.0.1:14560"
    baud: int = 57600                 # only used for serial URLs
    # Our own MAVLink identity. 250 (not 255) on purpose: ArduPilot's GCS
    # failsafe only counts heartbeats from SYSID_MYGCS (default 255), so this
    # server can never keep the GCS failsafe alive by accident (stack report,
    # Trap 1). It also makes our packets easy to spot in .tlog files.
    source_system: int = 250
    source_component: int = 191       # MAV_COMP_ID_ONBOARD_COMPUTER
    send_heartbeat: bool = True       # 1 Hz GCS-type heartbeat (identifies us)
    # MAVProxy already requests streams from the FC. Only enable this when the
    # server talks to the FC/SITL directly (no MAVProxy in between).
    request_streams: bool = False
    stream_rate_hz: int = 4
    heartbeat_timeout_s: float = 3.0  # telemetry older than this = link stale
    recent_buffer: int = 500          # ring buffer for get_recent_messages


@dataclass
class SafetyConfig:
    # Master switch. False = read-only server: every action tool refuses.
    # Same idea as mavlink-mcp's "read-only by default".
    actuation_enabled: bool = False
    # True = gate checks run and the tool reports what it WOULD send, but no
    # packet leaves the box. Use this for the first sessions on the Pixhawk.
    dry_run: bool = False

    # Envelope (checked by FlightGate before anything is sent).
    max_alt_m: float = 30.0           # relative to home; takeoff + goto
    min_goto_alt_m: float = 2.0       # never ask Guided to go below this
    geofence_radius_m: float = 100.0  # max horizontal distance from home
    max_speed_ms: float = 5.0         # set_speed ceiling
    max_mission_items: int = 100

    # Modes the LLM may request. Manual/acro-type modes are deliberately absent:
    # those belong to the pilot's sticks.
    allowed_modes: List[str] = field(default_factory=lambda: [
        "GUIDED", "LOITER", "BRAKE", "POSHOLD", "ALT_HOLD", "AUTO",
        "LAND", "RTL", "SMART_RTL",
    ])
    # "Stop" actions: always allowed (no confirmation, no rate limit, not
    # blocked by pilot override) because they only ever make things calmer.
    safe_modes: List[str] = field(default_factory=lambda: [
        "LOITER", "BRAKE", "POSHOLD", "LAND", "RTL", "SMART_RTL",
    ])

    # Actions that need a read-back + confirmation token (two-step call).
    confirm_actions: List[str] = field(default_factory=lambda: [
        "arm", "takeoff", "set_param", "upload_mission", "start_mission",
        "clear_pilot_override",
    ])
    # echo     = the token is returned to the caller; the LLM must call again
    #            with it. Catches hallucinated/duplicated calls; fine for SITL.
    # operator = the token is NOT returned to the LLM. It is printed on the
    #            MCP server console + logs/confirmations.jsonl (and shown by the
    #            monitor). A human has to read it out / type it in chat.
    #            Recommended on the real Pixhawk.
    confirm_mode: str = "operator"
    # How long a confirmation code stays valid, in seconds (gate's monotonic
    # clock). PLACEHOLDER: 45 s is a guess, not a measurement - long enough
    # for a person to read the readback, read the code off the console and
    # type it, short enough that an approval does not outlive the situation
    # it was given for. Hard ceiling: safety_gate.MAX_CONFIRM_TTL_S (120 s);
    # anything larger (or <= 0, NaN) stops the server at start-up.
    confirm_ttl_s: float = 45.0
    # How many gate decisions / state changes to keep in memory for the
    # read-only get_gate_audit tool (oldest dropped first).
    audit_max_entries: int = 500

    # Rate limits on non-safe actions.
    min_action_interval_s: float = 1.0
    max_actions_per_minute: int = 20

    # Pre-arm checks (the FC runs its own pre-arm checks too; these are extra).
    require_gps_fix: int = 3          # 3 = 3D fix
    min_battery_pct: int = 30         # ignored when the FC reports -1 (unknown)
    require_rc_link: bool = True      # pilot must have the radio up to arm

    # Pilot authority. If the flight mode changes and the hub did not ask for
    # it, the pilot (RC switch / QGC) took over -> latch "pilot_override" and
    # refuse every non-safe action until cleared (or the hold time expires).
    pilot_override_detection: bool = True
    pilot_override_hold_s: float = 0.0   # 0 = latched until cleared
    # Optional radio "LLM inhibit" switch: if RC channel N PWM > threshold, the
    # LLM is locked out (safe actions still pass). 0 disables.
    llm_inhibit_rc_channel: int = 0
    llm_inhibit_pwm_above: int = 1800

    # Parameters the LLM may write, with [min, max]. Everything else is
    # refused - in particular FS_*, BATT_FS_*, FENCE_*, ARMING_*: failsafes are
    # set by a person in QGC (stack report Trap 2 / DECISIONS.md).
    # ArduCopter 4.7 renamed many params to SI units (WPNAV_SPEED cm/s ->
    # WP_SPD m/s, RTL_ALT cm -> RTL_ALT_M m, ...). Both spellings are listed;
    # whichever the firmware does not have simply fails the read-back.
    param_allowlist: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        # --- ArduCopter <= 4.6 (centimetres) ---
        "WPNAV_SPEED": (50.0, 500.0),      # cm/s horizontal in Auto/Guided
        "WPNAV_SPEED_UP": (50.0, 250.0),   # cm/s
        "WPNAV_SPEED_DN": (30.0, 150.0),   # cm/s
        "LOIT_SPEED": (50.0, 500.0),       # cm/s
        "RTL_ALT": (500.0, 3000.0),        # cm
        "LAND_SPEED": (30.0, 100.0),       # cm/s
        # --- ArduCopter >= 4.7 (metres) ---
        "WP_SPD": (0.5, 5.0),              # m/s
        "WP_SPD_UP": (0.5, 2.5),           # m/s
        "WP_SPD_DN": (0.3, 1.5),           # m/s
        "LOIT_SPEED_MS": (0.5, 5.0),       # m/s
        "RTL_ALT_M": (5.0, 30.0),          # m
        "LAND_SPD_MS": (0.3, 1.0),         # m/s
    })


@dataclass
class ServerConfig:
    transport: str = "stdio"          # stdio | streamable-http | sse
    host: str = "127.0.0.1"
    port: int = 8765                  # streamable-HTTP endpoint = /mcp
    log_dir: str = "logs"             # command + confirmation JSONL logs


@dataclass
class StackConfig:
    profile: str = "default"
    mavlink: MavlinkConfig = field(default_factory=MavlinkConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def _merge(obj: Any, data: Dict[str, Any]) -> None:
    """Copy TOML keys onto a dataclass instance, recursing into sub-sections.

    Unknown keys raise, so a typo in the TOML ("max_alt" vs "max_alt_m") fails
    loudly instead of silently leaving the default in place."""
    names = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in names:
            raise KeyError(f"unknown config key '{key}' for {type(obj).__name__}")
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        elif key == "param_allowlist":
            setattr(obj, key, {k.upper(): (float(v[0]), float(v[1])) for k, v in value.items()})
        else:
            setattr(obj, key, value)


def _env_bool(name: str) -> Optional[bool]:
    v = os.environ.get(name)
    if v is None or v == "":
        return None
    return v.strip().lower() in ("1", "true", "yes", "on")


def load_config(path: Optional[str] = None) -> StackConfig:
    """Build a StackConfig from defaults + TOML file + environment overrides."""
    cfg = StackConfig()
    path = path or os.environ.get("DRONE_STACK_CONFIG")
    if path:
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    # Environment overrides (last word).
    if os.environ.get("MAVLINK_URL"):
        cfg.mavlink.url = os.environ["MAVLINK_URL"]
    for env, attr in (("DRONE_DRY_RUN", "dry_run"), ("DRONE_ACTUATION", "actuation_enabled")):
        b = _env_bool(env)
        if b is not None:
            setattr(cfg.safety, attr, b)
    if os.environ.get("DRONE_CONFIRM_MODE"):
        cfg.safety.confirm_mode = os.environ["DRONE_CONFIRM_MODE"]
    if os.environ.get("MCP_HOST"):
        cfg.server.host = os.environ["MCP_HOST"]
    if os.environ.get("MCP_PORT"):
        cfg.server.port = int(os.environ["MCP_PORT"])
    if cfg.safety.confirm_mode not in ("echo", "operator"):
        raise ValueError("safety.confirm_mode must be 'echo' or 'operator'")
    # Fail at start-up, not mid-flight, if the confirmation lifetime is silly.
    from safety_gate import validate_confirm_ttl
    cfg.safety.confirm_ttl_s = validate_confirm_ttl(cfg.safety.confirm_ttl_s)
    # Normalise mode names once so comparisons elsewhere are simple.
    cfg.safety.allowed_modes = [m.upper() for m in cfg.safety.allowed_modes]
    cfg.safety.safe_modes = [m.upper() for m in cfg.safety.safe_modes]
    return cfg
