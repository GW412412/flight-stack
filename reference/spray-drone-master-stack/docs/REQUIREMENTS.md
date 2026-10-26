# Requirements register

**Status:** Initial register derived from the existing master reference. Requirements need verification evidence before being marked complete.

## Authority and command safety
- REQ-AUTH-001: AI authority is locked at boot and after restart.
- REQ-AUTH-002: Explicit pilot takeover latches AI authority off.
- REQ-AUTH-003: Reconnection, heartbeat recovery, model restart, queued work or delayed tool completion cannot restore authority.
- REQ-AUTH-004: Every AI vehicle command path passes through the command gateway.
- REQ-AUTH-005: Authority is rechecked immediately before each vehicle command is sent.
- REQ-AUTH-006: Takeover invalidates pending requests and rejects late completions.
- REQ-AUTH-007: Re-enabling AI requires deliberate pilot action, valid current telemetry, suitable mode, healthy link and cleared faults.

## Spray safety
- REQ-SPRAY-001: Pump permission is off at boot, reset, takeover, mode change, fault, stale permission, target loss and shot timeout.
- REQ-SPRAY-002: A shot requires fresh, uniquely identified, unexpired authorization and valid flight, authority, height and sensor conditions.
- REQ-SPRAY-003: Duplicate or replayed requests cannot retrigger spraying.
- REQ-SPRAY-004: A hardware-safe default keeps the driver off through controller startup, reset and loss of control signal.
- REQ-SPRAY-005: Every cutoff reason and shot event is logged.

## Observability and verification
- REQ-TEST-001: Tests distinguish passed, failed, skipped and unrun checks.
- REQ-TEST-002: Firmware versions, parameters, dependency versions and source commit SHAs are recorded before hardware tests.
- REQ-TEST-003: Fault tests separately cover model hang, servo stop, stale telemetry, gateway failure, link interruption and process restart.
- REQ-TEST-004: No requirement is marked verified without recorded evidence.

## Open questions
Firmware-specific failsafe action, height-control configuration, rangefinder thresholds, exact joystick mapping, command semantics, tool permissions, and timing limits remain unresolved pending inventory and tests.
