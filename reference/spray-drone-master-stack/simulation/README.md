# Simulation

The repository includes a software-only dry-run adapter and deterministic demo:

```bash
python simulation/dry_run_demo.py
```

The demo passes proposals through the same command gate used by the unit tests.
It shows one proposal accepted as a simulated event, then demonstrates that a
delayed request from the old authority generation is rejected after pilot
takeover. The JSON report explicitly records that no vehicle link was opened and
no actuator interface is present.

This is not SITL and does not connect to a flight controller. It does not prove
real vehicle safety, MAVLink integration, or hardware interlock behaviour.
Keep physical outputs disconnected during subsequent integration work.
