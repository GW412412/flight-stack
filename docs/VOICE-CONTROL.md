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
| Safety | **Read-back** (“I heard: … — yes or no?”) then firm **yes**/**confirm**; **~2 s** hard timeout cancels; reject unknown audio |
| Wind / mishears | High confidence threshold, confirm loop, fail closed (no command) |
| Maturity | **SITL only** until scripted acceptance tests pass |

---

## Architecture

```
Mic (headset / GS) → Speech-to-text (local preferred)
                  → Phrase normaliser (lowercase, strip fillers)
                  → Whitelist matcher (exact / regex)
                  → Read-back (“I heard: … — yes or no?”)
                  → Policy gate (firm yes within 2 s? armed? RC override?)
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

## Read-back, then spoken confirmation

**Every** whitelist match — including spray-job `stop` / `hold` / `position hold` /
`loiter` — goes through a **read-back** before any MAVLink is sent. This catches
STT mishears before execution.

**Critical commands** (same gate, no exceptions): `arm`, `disarm`, `land`, `auto`,
`rtl` / `return to launch`, and any mode change while the **spray pump is on**.

**Flow:**

1. Matcher accepts a whitelist phrase (STT confidence ≥ threshold).
2. **Read-back (mandatory, before confirm):** the system speaks the command it
   heard, inviting yes/no, e.g.  
   `I heard: position hold — yes or no?`  
   Other examples: `I heard: arm — yes or no?`, `I heard: return to launch — yes or no?`  
   Do **not** send MAVLink at this step.
3. **Hard timeout: ~2 seconds** from the end of the read-back prompt. If no valid
   positive is accepted within **2.0 s**, **automatically cancel** and **fail closed**
   (no MAVLink). Speak `Timed out — cancelled.`
4. Reply handling:
   - **Firm positive only executes:** `yes`, `confirm` (optional: `affirmative`).
   - **Negative or ambiguous cancels:** `no`, `cancel`, `stop`, `wait`, `what`,
     wrong command name, partial words, low-confidence STT, or noise. Speak
     `Cancelled.` Do **not** treat hesitation or “okay?” as yes.
5. Only after a firm positive within the 2 s window send `MAV_CMD_DO_SET_MODE` /
   arm/disarm; speak `Position hold acknowledged` (or the mapped mode) / ACK error.

There is **no silent fast path**. Read-back + firm yes is required for Loiter
aliases too; the loop is still short (~2 s) so hands-free “hold” stays usable.

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
| Feedback | Mandatory TTS **read-back** of the matched phrase before yes/no; then execute only on firm positive |
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
| 2. SITL read-back + confirm | Every phrase gets “I heard: … — yes or no?”; Arm/Land/Auto/Loiter abort without firm `yes`/`confirm`; **2 s** timeout and `no`/ambiguous cancel; **zero** executes without read-back |
| 3. Noise injection | Play wind/prop noise under mic; false execute rate **0** over N≥50 distractors |
| 4. RC conflict | Flip ELRS mode switch during voice Loiter — vehicle follows RC; voice does not re-assert |
| 5. Failsafe | Kill SITL RC → voice refuses commands |
| 6. Live bench (props off) | Disarmed vehicle on bench only; never armed spray tank outdoors until 1–5 signed off |

**No live armed flight with voice enabled until SITL checklist is recorded in the repo or ops log.**

---

## One-paragraph summary

Voice control for this project is a **GS/companion STT whitelist** that maps
spoken **stop / hold / position hold / loiter** to Copter **Loiter** via
`MAV_CMD_DO_SET_MODE`, with a mandatory **read-back** (“I heard: … — yes or no?”) then firm
**confirm**/**yes** (≈**2 s** timeout, else cancel) for all whitelist actions including Loiter, **ELRS retained as hard authority**, wind mishears failing
closed, and **SITL validation required** before any live vehicle — built as a
tight scripted layer inspired by MAVProxy chat, not as unconstrained LLM control.
