# Testing strategy

## Stage 0 — repository and static checks
- Validate project metadata and syntax.
- Run formatting, linting and unit tests when code exists.
- Record exact commands, tool versions and exit codes.

## Stage 1 — simulation
- Exercise intent parsing and command-gateway policy with fake telemetry.
- Inject stale, duplicated, delayed and malformed requests.
- Trigger pilot takeover during pending arm, takeoff, movement, orbit and spray requests.
- Verify queued work and late completions cannot regain authority.
- Test fault transitions and recovery behavior.

## Stage 2 — read-only telemetry
- Inventory exact host OS, Cube model, firmware build, RC map, parameters and pinned software versions.
- Compare reported battery, GPS, mode, attitude and a known parameter with QGroundControl.
- Verify available tools cannot actuate or write parameters.

## Stage 3 — bench safety
- Props off and ESC power disconnected.
- Use an LED/dummy load before connecting a pump.
- Test pump inhibit, hard timeout, duplicate rejection, stale permission, target loss, mode change, authority revocation, range dropout and reset/power sequencing.
- Record expected and observed outcomes and response timing.

## Stage 4 — controlled field progression
Only after earlier gates pass: manual controlled hover, height stability tests, target-servo tests over a marker, then model-selected markers. Use water before considering chemicals and comply with applicable operating and chemical-use requirements.

## Reporting
For each test, record ID, setup, firmware/software versions, preconditions, exact command, expected result, observed result, logs, timing and pass/fail. Never infer a pass from absence of an error message.
