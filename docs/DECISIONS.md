# Decisions Log

Running record of project decisions. Add new entries at the top of the relevant section.
Entry format: **YYYY-MM-DD — Title** · Decision · Notes / follow-ups.

---

## Autonomy & Control

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

**2026-10-23 — Modular stack**
- Every module must be removable without halting anything else.
- Modules talk to each other only through narrow, well-defined interfaces.

**2026-10-18 — Consolidated stack report**
- The stack report is saved at `docs/STACK-REPORT-2026-10-18.md` (assessment dated 18 Oct 2026, saved with corrections on 23 Oct 2026).

## Vision & Computer Vision

_No entries yet._

## Ground Station (RIDGE)

_No entries yet._

## Open Questions

**2026-10-23 — Full safety review**
- A fine-tooth-comb review of safety and failsafes is still to come. Until then, the autonomy position above stands.
