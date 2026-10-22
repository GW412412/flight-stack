# Decisions Log

Running record of project decisions. Add new entries at the top of the relevant section.
Entry format: **YYYY-MM-DD — Title** · Decision · Notes / follow-ups.

---

## Autonomy & Control

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

**2026-10-23 — Failsafe ladder and manual override**
- See Autonomy & Control, 2026-10-23. In short, G's radio switch overrides everything, the failsafe ladder is straight-and-level, then land, then come home, and every flight stays within line of sight.

## Hardware

_No entries yet._

## Software Stack

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

**2026-10-23 — What triggers the failsafe ladder?**
- Which triggers start the ladder (link loss, low battery, G's switch, anything else), and what each one does (straight-and-level, land or come home).

**2026-10-23 — Do decision models fit the modular stack?**
- Assess the small decision models (Nimble, Clef, others) by their structure and framework.
- Check that they can be added without becoming load-bearing. Removing one must not halt anything else (see Modular stack).

**2026-10-23 — Full safety review**
- A fine-tooth-comb review of safety and failsafes is still to come. Until then, the autonomy position above stands.
