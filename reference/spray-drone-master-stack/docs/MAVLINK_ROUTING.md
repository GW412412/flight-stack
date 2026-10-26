# MAVLink routing and diagnostic tools

**Status:** configuration guide for localhost simulation only. No real vehicle
endpoint, serial device, or actuator is configured here.

## Roles: do not run competing owners on the same link

- **MAVLink Router** is the preferred planned router when one telemetry source
  must be distributed to QGroundControl and a separately controlled gateway.
  One process should own a physical serial connection.
- **MAVProxy** is an alternative router/CLI and useful for SITL diagnostics.
  It is not required in the primary architecture. Use it instead of MAVLink
  Router for a test, or attach it to a deliberately configured simulation
  endpoint; do not let both processes open the same serial device.
- **QGroundControl** is the human-facing GCS. Its telemetry view is not proof
  that every command path is gated.
- **The command gateway** must be the only route for AI-originated vehicle
  commands. Never expose an unrestricted MAVLink output to the model/tool
  process.

The safe initial arrangement is localhost-only SITL, read-only telemetry, and
the repository's software dry-run gate. These examples do not transmit vehicle
commands.

## Install on Ubuntu (when you choose to)

Package names and versions vary by Ubuntu release. Check availability before
installing:

```bash
apt-cache policy mavlink-router mavproxy
```

Prefer distribution packages or pinned upstream releases after reviewing their
source and version. Do not run install commands as part of this dry-run.

## MAVLink Router example

See [sitl-local.conf.example](../config/mavlink-router/sitl-local.conf.example).
It binds only to loopback and is a starting template, not a validated complete
topology. Confirm the actual SITL output endpoint, endpoint direction, and
router version's configuration syntax before starting it. Do not substitute a
serial path or a vehicle IP until the read-only stage is approved.

## MAVProxy alternative for local SITL

After SITL is running and its UDP endpoint is known, a typical diagnostic
pattern is:

```bash
mavproxy.py --master=udp:127.0.0.1:14550 --out=udp:127.0.0.1:14551
```

This example assumes the SITL instance sends MAVLink to UDP port 14550 and the
receiving GCS is configured for port 14551. Confirm endpoint direction for the
chosen SITL launch command; UDP source/destination conventions differ between
launch setups. Keep this to localhost and do not add a real serial master.
Do not add AI command tools to this diagnostic route.

For read-only diagnostics, inspect MAVProxy's available commands and plugins
before enabling any module that can change mode, arm, write parameters, or
send commands. A CLI's presence does not make it safe by default.

## Port and authority rules

1. Write down each process, bind address, port, and direction before launch.
2. Use loopback addresses for software-only tests. Avoid `0.0.0.0` and LAN
   exposure during early integration.
3. Give each UDP port a single intended receiver where practical; avoid
   ambiguous duplicate consumers.
4. Keep QGroundControl and diagnostics read-only until the command gateway's
   enforcement and all alternate paths have been reviewed.
5. Do not filter or suppress GCS heartbeats to simulate a failsafe until the
   exact firmware behavior and effects on the human GCS are understood.
6. Never infer that a command is blocked because it is absent from the GCS map;
   inspect gateway decisions and the flight-controller's observed state.
7. No spray/relay/servo output is part of this routing guide.

## Acceptance checks before adding a real endpoint

- Confirm the software-only demo and unit tests pass.
- Confirm the chosen router starts with the local config and no serial device.
- Confirm telemetry can be received by the intended local consumer.
- Confirm no command-producing process has a route around the gateway.
- Inject stale, duplicate, delayed, mode-change, and pilot-takeover cases.
- Capture endpoint configuration and logs as test evidence.

Do not proceed to real-controller or actuation tests based on these checks alone.
