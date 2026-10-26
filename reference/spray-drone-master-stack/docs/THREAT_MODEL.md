# Threat model

## Status

Initial engineering register. This is not a completed security assessment or flight approval.

## Assets to protect

- Pilot authority and manual takeover latch.
- Flight-controller configuration and vehicle state.
- Command-gateway policy and spray-permission state.
- Local model, tools, logs, configuration and credentials.
- Integrity and traceability of test evidence.

## Trust boundaries

1. Human pilot and ground-control station.
2. MAVLink transport and router.
3. AI/model output and tool responses.
4. Command gateway and policy enforcement.
5. Flight controller and onboard spray interlock.
6. Filesystem, dependencies and deployment environment.

## Threats and required mitigations

| Threat | Potential consequence | Required mitigation / evidence |
|---|---|---|
| Prompt injection in imagery, telemetry labels or tool output | Model proposes an unsafe or out-of-scope action | Treat external content as untrusted data; model has no direct actuator tool; test adversarial inputs |
| Alternate command path bypasses gateway | AI command evades policy checks | Enumerate every command path; deny by default; integration tests for bypass attempts |
| Replayed, delayed or duplicated request | Stale mission or spray action is accepted | Unique request ID, expiry, state/version binding and replay tests |
| Pilot takeover lost on reconnect or restart | AI authority returns without pilot consent | Persistent latched lockout; explicit pilot re-enable; test reconnect, process restart and queued-command cases |
| Stale telemetry or link/process failure | Decision made from invalid state | Freshness limits; timeout to safe state; fault injection |
| Configuration or dependency tampering | Unreviewed behavior reaches deployment | Pin and record versions, review changes, maintain reproducible tests |
| Secrets or sensitive location data in logs | Credential or privacy exposure | No secrets in repository; minimize and redact logs; local access controls |
| Model confidently invents state or capabilities | Unsafe action based on unsupported assumptions | Validate against typed telemetry and allow-listed operations; reject unknown values |

## Non-negotiable constraints

- The model is not a safety controller and cannot directly command pump activation.
- All AI-originated commands must pass through a deterministic command gateway.
- Spray output must fail off on boot/reset, takeover, mode change, fault, stale permission, target loss or timeout.
- Pilot takeover remains latched until an explicit pilot re-enable action.
- Unknown states and validation failures are rejected, not guessed through.

## Validation still required

The threat list must be reviewed against the exact flight-controller firmware, MAVLink routing, tool servers, model runtime and electrical design. No hardware or flight behavior is verified by this document.