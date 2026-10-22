# Decisions Log

Running record of project decisions. Add new entries at the top of the relevant section.
Entry format: **YYYY-MM-DD — Title** · Decision · Notes / follow-ups.

---

## Autonomy & Control

**2026-10-23 — The model on the sticks**
- For now, the model flying the drone is a local model, which keeps it focused on the task at hand.
- Its value is how much raw data it can process and how quickly it can decide in the time available. We'll fine-tune that as we go.
- Spray drone (this assistant) could take that role later.

**2026-10-23 — Command hierarchy**
- The flight controller has master authority until it's overridden.
- G's radio/failsafe switch overrides the flight controller.
- The failsafe fires on whichever trigger comes first, human or system. Neither has priority over the other.
- See the draft hierarchy under Architecture.

**2026-10-23 — Autonomy position: Spray drone flies the drone**
- Spray drone (the AI) controls the drone itself, with safeties off during development.
- Crashes and damage are expected and accepted as part of development.
- The master/failsafe switch stays in G's hand on the radio at all times.
- Failsafe ladder: straight-and-level, then land, then come home.
- Always flown within line of sight.
- Safety is to be reviewed with a fine-tooth comb later (see Open Questions).

## Safety & Failsafe

**2026-10-23 — Rely on ArduPilot's built-in failsafes, and G's action is Position Hold**
- Link loss, low battery and similar triggers use the failsafes already built into the flight controller, set the way ArduPilot documents them. We aren't writing new rules.
- G watches the drone's orientation. If it starts tilting, running off, or heading where it shouldn't, G switches to position hold and then decides what happens next (land or something else).
- To check: whether "position hold" means ArduPilot's PosHold mode or Loiter mode on G's mode switch.

**2026-10-23 — Failsafe ladder and manual override**
- See Autonomy & Control, 2026-10-23. In short, G's radio switch overrides everything, the failsafe ladder is straight-and-level, then land, then come home, and every flight stays within line of sight.

## Hardware

_No entries yet._

## Software Stack

**2026-10-23 — Live notes and debrief**
- While the drone is in a safe state (for example position hold), G and the assistant can talk, and notes get logged on the fly with exact timestamps.
- Each note is tied to a snapshot of the flight data at that moment.
- In the evening we review the day's flying, swap feedback both ways, and fine-tune the next session.

**2026-10-23 — Reference links**
- Links to the ArduPilot repositories and docs are kept in `docs/REFERENCES.md`.

**2026-10-23 — Speed principle**
- The end of the pipeline is a decision process, not a chat.
- For real-time control, latency matters more than eloquence.

**2026-10-23 — Small decision models (under evaluation)**
- G is evaluating small decision models on Ollama (for example Nimble and Clef, names as G gave them) for fast go/no-go answers such as "weeds left/right", instead of slow English responses from a large LLM.
- Their value depends on their structure and framework. Not adopted yet, see Open Questions.

**2026-10-23 — Modular stack**
- Every module must be removable without halting anything else.
- Modules talk to each other only through narrow, well-defined interfaces.

**2026-10-18 — Consolidated stack report**
- The stack report is saved at `docs/STACK-REPORT-2026-10-18.md` (assessment dated 18 Oct 2026, saved with corrections on 23 Oct 2026).

## Vision & Computer Vision

_No entries yet._

## Ground Station (RIDGE)

_No entries yet._

## Architecture

**2026-10-23 — Command hierarchy (DRAFT, not yet agreed)**

Highest priority first:

1. **G's radio**: the failsafe and manual override, highest priority.
2. **Decision model**: fast go/no-go calls such as "weeds left/right".
3. **Large LLM**: planning and reasoning, not time-critical.
4. **Flight controller**: master authority until overridden.

```
G's radio (failsafe, highest priority)
  → Decision model (fast go/no-go, weeds left/right)
    → Large LLM (planning, reasoning, non-time-critical)
      → Flight controller (master authority until overridden)
```

_Draft. Review it alongside the failsafe-trigger question and the full safety review._

## Open Questions

**2026-10-23 — What triggers the failsafe ladder? (mostly answered 23 Oct)**
- Answer: the flight controller's built-in failsafes handle the automatic triggers, and G switches to position hold by watching orientation (see Safety & Failsafe).
- Still to do: set each built-in failsafe's action (RC, GCS, battery, EKF) to match the ladder, and record the chosen values.

**2026-10-23 — Do decision models fit the modular stack?**
- Assess the small decision models (Nimble, Clef, others) by their structure and framework.
- Check that they can be added without becoming load-bearing. Removing one must not halt anything else (see Modular stack).

**2026-10-23 — Full safety review**
- A fine-tooth-comb review of safety and failsafes is still to come. Until then, the autonomy position above stands.
