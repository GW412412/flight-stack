# Voice control layer (flight modes)

Hands-free **speech → whitelist → MAVLink mode change** for the precision
spot-spraying project. Designed so the operator can call out **stop / hold /
position hold (Loiter)** quickly during a spray job without looking at a GCS.

This is **not** an ArduPilot firmware feature. It is a **ground-station /
companion convenience layer** on top of MAVLink. **ELRS remains the hard
authority** for control; voice never replaces the RC link.

Related: [SYSTEM-SPEC.md](SYSTEM-SPEC.md), ArduPilot MAVLink flight-mode docs,
MAVProxy chat module (experimental NL + short audio capture).

---

## Goals

| Need | Design response |
|------|-----------------|
| Speak mode commands hands-free | Always-listening or push-to-talk STT on GS/companion |
| Fast “stop / hold / position hold” | Whitelisted phrases → Copter **Loiter** (and related) via `MAV_CMD_DO_SET_MODE` |
| Safety | Spoken **confirm** before arm and other critical changes; reject unknown audio |
| Wind / mishears | High confidence threshold, confirm loop, fail closed (no command) |
| Maturity | **SITL only** until scripted acceptance tests pass |

---

## Architecture

```
Mic (headset / GS) → Speech-to-text (local preferred)
                  → Phrase normaliser (lowercase, strip fillers)
                  → Whitelist matcher (exact / regex)
                  → Policy gate (confirm? armed? RC override?)
                  → MAVLink MAV_CMD_DO_SET_MODE (176)
                     param1 = MAV_MODE_FLAG_CUSTOM_MODE_ENABLED (1)
                     param2 = vehicle mode number
                  → Wait COMMAND_ACK; speak result back to operator
```

**Implementation baseline:** MAVProxy **`module load chat`** (OpenAI assistant +
optional ~5 s Rec audio) **or equivalent** local stack (Whisper / Vosk STT +
small Python whitelist → `pymavlink`). Prefer a **local whitelist script** for
spray ops; treat MAVProxy chat as a prototype only — upstream docs warn it can
pick the **wrong mode**.

Mode numbers: resolve with the vehicle’s mode map (Copter ≠ Plane). Never hard-code
numbers without reading `get_mode_mapping` / FLTMODE docs for the firmware in use.

Reference: https://ardupilot.org/dev/docs/mavlink-get-set-flightmode.html  
MAVProxy chat: https://ardupilot.org/mavproxy/docs/modules/chat.html

---

## Exact phrase whitelist (Copter)

Normalise audio to lowercase ASCII; strip “please”, “um”, “the drone”. Match
**only** the phrases below (and listed aliases). Anything else → **no MAVLink**.

### Primary spray-job phrases (map → **Loiter**)

| Spoken phrase | Action | Mode |
|---------------|--------|------|
| `stop` | Set flight mode | **Loiter** |
| `hold` | Set flight mode | **Loiter** |
| `position hold` | Set flight mode | **Loiter** |
| `loiter` | Set flight mode | **Loiter** |
| `hold position` | Set flight mode | **Loiter** |

Rationale: in spray ops the operator means “stop translating / hold here,” which
on Copter is **Loiter**, not motor kill. **Do not** map `stop` to disarm or
emergency kill.

### Other allowed phrases

| Spoken phrase | Action | Notes |
|---------------|--------|-------|
| `return to launch` | Mode **RTL** | Also accept `rtl`, `return home` |
| `land` | Mode **Land** | Critical — needs confirm |
| `guided` | Mode **Guided** | |
| `auto` | Mode **Auto** | Critical — needs confirm |
| `brake` | Mode **Brake** if available on firmware | Else reject with spoken “brake unavailable” |
| `arm` | Arm motors | **Always** confirm |
| `disarm` | Disarm motors | Confirm; may fail if not landed — report ACK |

### Explicitly rejected (never auto-execute)

| Audio | Handling |
|-------|----------|
| `kill`, `kill motors`, `emergency stop` | Speak “use transmitter”; **no** MAVLink from voice (RC / hardware kill only) |
| `takeoff …`, free-form navigation | Out of scope for v1 whitelist |
| Unrecognised / low-confidence STT | Speak “repeat”; **no** command |

---

## Mandatory spoken confirmation

**Before** sending MAVLink for any of:

- `arm`
- `disarm`
- `land`
- `auto`
- `rtl` / `return to launch` (optional but **recommended** on a loaded sprayer)
- Any mode change while **spray pump is on** (if pump state is known)

**Flow:**

1. Matcher accepts phrase → speech reply:  
   `Confirm: set Loiter. Say confirm or cancel.`  
   (substitute the real action)
2. Operator must say exactly **`confirm`** within **5 seconds**.
3. **`cancel`**, timeout, or any other words → abort; speak `Cancelled.`
4. Only then send `MAV_CMD_DO_SET_MODE` / arm command; speak `Loiter acknowledged` or the ACK failure reason.

**Fast path (no confirm)** — only these, and only when **disarmed or pump off** and STT confidence ≥ threshold:

- `stop` / `hold` / `position hold` / `loiter` / `hold position` → Loiter  

If the pump is **on**, even Loiter requires **`confirm`** (drift into crop / people risk).

---

## ELRS as hard authority

| Layer | Role |
|-------|------|
| **ELRS (RC)** | Hard authority — sticks, mode switch, arm, failsafes |
| **Voice** | Convenience only — never the sole path to a safe state |
| **GCS UI** | Parallel convenience; same MAVLink rules |

Rules:

1. If RC mode switch or sticks demand a mode, **FC/RC wins**; voice must not fight a pilot override.
2. Voice stack must monitor RC loss / failsafe and **disable itself** (refuse new commands) when the vehicle is in RC failsafe or radio loss.
3. Document for the operator: practice Loiter on the **ELRS switch** first; voice is backup when hands are busy.
4. No voice path to motor kill — that stays on the transmitter / hardware.

---

## Mishears and wind noise

| Mitigation | Detail |
|------------|--------|
| Mic | Closed-back headset boom mic; avoid open laptop mic in prop wash |
| STT | Prefer **local** model; require confidence ≥ **0.85** (tune in SITL/field) |
| Match | Exact whitelist only; no fuzzy “sounds like Loiter” |
| Push-to-talk | Default **PTT** (foot switch / hotkey) for v1; always-on only after field false-positive rate is measured |
| Feedback | TTS or earpiece echoes the interpreted phrase before confirm / execute |
| Logging | Log raw transcript + matched phrase + ACK to `logs/voice-commands.jsonl` for review |
| Rate limit | Max **1** executed command per **2 s** |
| Spray job | If two consecutive rejects, speak `Voice degraded — use radio` and pause auto-listen |

---

## Software sketch (GS)

1. Run ArduPilot **SITL** + MAVProxy (or pymavlink) on the ground station.  
2. Load whitelist service (Python) subscribed to STT events.  
3. On match → policy gate → `COMMAND_LONG` / `COMMAND_INT` with  
   `MAV_CMD_DO_SET_MODE`, `param1=1`, `param2=<loiter_number>`.  
4. Await `COMMAND_ACK`; never assume success.  
5. Optional: thin wrapper around MAVProxy chat **disabled** for free-form LLM; use only if locked to the same whitelist tools.

Do **not** ship the unrestricted MAVProxy chat assistant to a live sprayer.

---

## Testing (mandatory before live vehicle)

| Stage | Pass criteria |
|-------|----------------|
| 1. SITL phrase table | Every whitelist phrase maps to expected mode; non-phrases send **zero** MAVLink |
| 2. SITL confirm gate | Arm/Land/Auto abort without `confirm`; Loiter fast-path works when pump simulated off |
| 3. Noise injection | Play wind/prop noise under mic; false execute rate **0** over N≥50 distractors |
| 4. RC conflict | Flip ELRS mode switch during voice Loiter — vehicle follows RC; voice does not re-assert |
| 5. Failsafe | Kill SITL RC → voice refuses commands |
| 6. Live bench (props off) | Disarmed vehicle on bench only; never armed spray tank outdoors until 1–5 signed off |

**No live armed flight with voice enabled until SITL checklist is recorded in the repo or ops log.**

---

## One-paragraph summary

Voice control for this project is a **GS/companion STT whitelist** that maps
spoken **stop / hold / position hold / loiter** to Copter **Loiter** via
`MAV_CMD_DO_SET_MODE`, with **arm / disarm / land / auto / RTL** behind a mandatory
spoken **confirm**, **ELRS retained as hard authority**, wind mishears failing
closed, and **SITL validation required** before any live vehicle — built as a
tight scripted layer inspired by MAVProxy chat, not as unconstrained LLM control.
