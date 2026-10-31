# P1 safety fixes: operator notes

Short. Code comments and tests carry the detail. Hardware profile = any profile
other than `sitl`/`default` (today `config/llm_stack.pixhawk.toml`; the file name
is historical, the flight controller is an Omnibus F4 Pro, docs not updated here).

## What changed for the operator

| Area | Now |
|---|---|
| AUTO | Not in `allowed_modes`. `set_mode("AUTO")` is refused. Use `start_mission`: it re-reads the mission stored on the FC, checks fence/altitude/commands, and the confirmation is bound to that exact mission. |
| Telemetry | Position, attitude and sys_status each need a fresh message (`telemetry_max_age_s`, default 3 s), not just heartbeats. `get_status` shows `msg_age_s`. |
| Disarm | Refused when altitude is unknown or stale, unless the FC reports ON_GROUND (EXTENDED_SYS_STATE) and it is fresh. |
| goto / takeoff / mission | Need 3D fix, EKF healthy and fresh. |
| Battery | Unknown is `None` and blocks arm/commanding. Never 101. |
| Link | Telemetry is adopted only from the pinned autopilot sysid/compid (`vehicle_sysid` in `[mavlink]`, 0 = pin the first autopilot heartbeat). |
| Hub lock | Never held across a COMMAND_ACK wait. LAND/RTL/hold/pump-off do not take it. |
| Env overrides | On hardware they can only tighten: `DRONE_ACTUATION` 1->0, `DRONE_DRY_RUN` 0->1, `DRONE_CONFIRM_MODE` echo->operator. Loosening is ignored and logged at startup. Enable actuation by editing the TOML. |

## MCP auth

* HTTP binds `127.0.0.1` by default.
* A non-loopback `--host` / `MCP_HOST` needs a token or the server will not start.
* If a token is set it is required on every HTTP request: `Authorization: Bearer <token>`.
* Token source (never the repo, never the TOML): `MCP_AUTH_TOKEN_FILE` (chmod 600, preferred) or `MCP_AUTH_TOKEN`. Min 16 chars.
* `mcp_agent.py` sends it automatically from the same variables.
* mcpo (:8001): `bringup.sh --webui` passes `MCPO_API_KEY[_FILE]` to `--api-key --strict-auth` and `MCP_AUTH_TOKEN[_FILE]` as the upstream header. A non-loopback `MCPO_HOST` is refused without `MCPO_API_KEY[_FILE]`. Note mcpo takes both on its command line, so they show in `ps` on that host.

```bash
umask 077; python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > ~/.mcp-token
MCP_AUTH_TOKEN_FILE=~/.mcp-token python drone_stack/middleware/mcp_server.py --transport streamable-http --host 0.0.0.0
```

## Spraying (weeds / spray points)

* Every sprayer command the stack sends is one line in `logs/**/mavlink-commands.jsonl`; each automated pulse request also logs a `SPRAY_DECISION` line (allowed, or blocked with reasons).
* The pilot's manual fire button is never gated: FlightGate has no spray action, and `SafetyGate.can_spray` applies only to the automated pulse path. Tests pin this.
* **Not done:** manual fire goes radio -> FC and never touches the hub, so it is not in this audit log. Logging it needs a telemetry observer on whatever the pump uses (relay/servo output or a STATUSTEXT). Open question for G.

## CI

`.github/workflows/` could not be created by the bot (token lacks `workflow` scope), so the workflow is parked at `ci/github-actions/p1-ci.yml`. Move it to `.github/workflows/ci.yml` to enable. It runs ruff (E9,F), shellcheck, pytest.
