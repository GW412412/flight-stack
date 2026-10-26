# Bringing Spray-Drone-master-stack into flight-stack

flight-stack is the one place to work. Everything in the separate [Spray-Drone-master-stack](https://github.com/GW412412/Spray-Drone-master-stack) repository is now copied, unedited, into [reference/spray-drone-master-stack/](../reference/spray-drone-master-stack/IMPORTED.md) as clearly labelled reference material. Nothing was deleted, renamed or archived in either repository, and no decision was changed.

Prepared by Maya (AI agent, Sesame), 2026-10-26 12:10 AEDT, at G's request. Source commit [`873dadd`](https://github.com/GW412412/Spray-Drone-master-stack/commit/873dadd6e94d33168a664bcc04636c96d3b31c3a).

## The two names, for anyone confused

- **flight-stack** (this repository) was renamed from `precision-drone-spraying`. Old `precision-drone-spraying` links redirect here. Same repository, one history.
- **Spray-Drone-master-stack** is a separate repository, created 22 October 2026 and last pushed 23 October 2026. It is the one imported here.

## Status of each part

Authoritative means flight-stack's own file stays in charge; the imported file is kept for reference.

| Imported file | What it is | Status against flight-stack |
|---|---|---|
| `docs/MASTER_PLAN.md` | Master reference v2, revised 18 Oct 2026 | Overlaps [STACK-REPORT-2026-10-18.md](STACK-REPORT-2026-10-18.md): both revise the same consolidated report. Unique parts: the latched pilot-takeover section, configuration register and bench acceptance sequence. Reference. |
| `docs/REQUIREMENTS.md` | REQ-AUTH, REQ-SPRAY, REQ-TEST register | Unique. flight-stack has no requirements register. Proposals only. |
| `docs/SAFETY_CASE.md` | Hazard register | Unique. Proposals only. |
| `docs/THREAT_MODEL.md` | Threats and trust boundaries | Unique. Proposals only. |
| `docs/INTERFACES.md` | Request envelope (`request_id`, expiry, `authority_generation`) | Unique, but see conflict 4. |
| `docs/TESTING_STRATEGY.md`, `docs/DEPLOYMENT.md` | Staged test and deployment gates | Overlap [SIMULATION.md](SIMULATION.md) and the STACK-REPORT test sequence. Reference. |
| `docs/ARCHITECTURE.md` | Proposed chain and authority boundaries | Superseded by [ARCHITECTURE.md](ARCHITECTURE.md). See conflicts 3 and 5. |
| `docs/DECISIONS.md` | D-001 to D-003 and open decisions | Superseded by [DECISIONS.md](DECISIONS.md). See conflict 1. |
| `docs/MAVLINK_ROUTING.md`, `config/mavlink-router/sitl-local.conf.example` | Router and MAVProxy roles, loopback-only template | Overlap [LLM-STACK.md](LLM-STACK.md) and [../config/mavlink-router.conf](../config/mavlink-router.conf). See conflict 5. |
| `AGENTS.md` | Rules for human and AI agents | Overlaps [../CONTRIBUTING.md](../CONTRIBUTING.md). Reference. |
| `README.md`, `CONTRIBUTING.md` | Source repo front page and workflow | Superseded by flight-stack's own. |
| `docs/development/` | Setup, coding standards, troubleshooting for the package | Applies only to the imported package. |
| `src/spray_drone/` | `CommandGate`, dry-run adapter, config guard, empty placeholder packages | Unique code, not wired in. 19 tests pass in place. See conflict 4. |
| `simulation/dry_run_demo.py` | Software-only demo of takeover rejecting a late request | Unique. Opens no vehicle link. |
| `.github/`, `pyproject.toml`, `Makefile`, `scripts/`, `tests/` | Tooling for the package | Kept as files. The `.github` copy is inert in a subfolder. |
| `data/`, `logs/`, `reports/`, `simulation/results/`, `tools/`, `deployment/` | Placeholder folders and READMEs | Kept as is. |

## Not included: Claude's patches for the old gate

**Claude's patches for the old repository's gate were NOT included in this import.** The imported test-only safety gate (`CommandGate` in `reference/spray-drone-master-stack/src/spray_drone/`) does not have them. Don't read the 19 passing tests as covering them.

- The folder is source commit `873dadd` exactly as it stood. None of the commits in Spray-Drone-master-stack is credited to Claude.
- [Draft PR #10](https://github.com/GW412412/flight-stack/pull/10) says Claude's patches were written for the `src/spray_drone/command_gate` layout and were not applied there either. PR #10 re-implemented some of their ideas in FlightGate and lists the ones it did not adopt. Those changes live in PR #10, not in this folder.
- Whether to apply Claude's patches to the imported gate is a separate question. This import doesn't settle it.

Added by Maya (AI agent, Sesame), 2026-10-26 12:52 AEDT, after Claude (AI agent, Anthropic) reviewed this pull request and asked for the note.

## Where the imported material disagrees with flight-stack

These are recorded, not resolved. Each is a call for G and the group, through [DISCUSSION.md](DISCUSSION.md) and then [DECISIONS.md](DECISIONS.md).

1. **Development posture.** Imported D-002 says simulation and read-only first, with no real actuation. flight-stack's DECISIONS.md (2026-10-23, "Autonomy position: Spray drone flies the drone") says safeties are off during development, crashes are accepted, and G keeps the master switch. DECISIONS.md stands.
2. **Boot and restart.** Imported REQ-AUTH-001 says AI authority is locked at boot and after restart. In flight-stack, FlightGate takes `actuation_enabled` from config (off by default), and Claude's review of PR #10 notes that a server restart clears the pilot-override latch. That is an open item, not yet decided.
3. **What a takeover blocks.** Imported AGENTS.md and REQ-AUTH-002 say pilot takeover latches all AI authority off. PR #10 deliberately still lets hold, brake, land and RTL through after a takeover; Claude flagged this as a decision for G.
4. **Gate design.** The imported `CommandGate` uses an authority-generation number on every request. PR #10 chose not to adopt Claude's authority-generation envelope for flight-stack's layout. The imported gate stays a reference design; FlightGate is the gate in use.
5. **Who owns the flight controller link.** Imported docs make MAVLink Router the preferred owner, with MAVProxy optional. flight-stack's LLM-STACK.md makes MAVProxy the only program that opens the link, with mavlink-router as the alternative.
6. **Flight controller.** The imported master plan names a Cube on USB. flight-stack's STACK-REPORT and LLM-STACK.md name a Pixhawk 1, and AIRFRAME.md recommends a Pixhawk 4 or Cube Orange and says not an original Pixhawk 1. Also unresolved inside flight-stack itself.
7. **Failsafe action.** Imported docs leave the GCS failsafe action unselected. flight-stack's STACK-REPORT (23 Oct corrections) names 7 (Brake or Land) or 5 (Land) as closest, and DECISIONS.md still lists setting each failsafe action as to do. Broadly consistent; both still open.

## Issues in the old repository

Both were opened by GW412412 on 25 October 2026, after the main work had moved to flight-stack. Neither has comments. They were not moved, closed or edited. Their full text is below so nothing is lost if the old repository is archived.

### [#1 Design-review note: spray subsystem safety and validation](https://github.com/GW412412/Spray-Drone-master-stack/issues/1)

> A constructive review note for the team — and credit to Maya for the work on the project and the message-board setup. Having a public place to compare notes should make the review much easier.
>
> One area worth making explicit before moving beyond simulation is the spray subsystem's safety boundary. Suggested questions for the next review:
>
> - What exact conditions permit the pump/valve to activate, and what independent conditions force it off?
> - On lost link, controller restart, stale telemetry, or an invalid mission state, is spraying guaranteed to stop?
> - How will flow rate and dose be calibrated against speed, swath width, and target application rate?
> - What tests demonstrate that spray remains disabled during startup, arming, failsafe, and software faults?
> - How will tests record commanded spray state versus measured pump/valve/flow state?
>
> I'd keep these as requirements and simulation/test cases first, with no assumption that software-only checks alone prove hardware safety. This is a discussion prompt, not a claim that the current implementation is unsafe or that these behaviours are already implemented.
>
> Thanks to everyone contributing — please add evidence, corrections, and competing ideas so we can turn the open questions into testable requirements.

Suggested home: the flight-stack message board (Proposals) or DISCUSSION.md. The imported REQ-SPRAY items and SAFETY_CASE.md already sketch answers to several of these questions.

Update, 2026-10-26 12:52 AEDT: reposted word for word to the message board as [discussion #22](https://github.com/GW412412/flight-stack/discussions/22) (Proposals), crediting the original and linking back. The original issue is still open and unchanged.

### [#2 Project notes and general discussion](https://github.com/GW412412/Spray-Drone-master-stack/issues/2)

> ## A place for general project notes
>
> Testing, testing — 1, 2, 3. This is a first check that the repository's discussion workflow is usable, and a simple acknowledgement that we'll use it to keep project thoughts in one place.
>
> This thread is for general group observations, ideas, useful links, small progress notes, and questions that do not yet need a formal design review or bug report. No need to have a fully worked-out answer before contributing; short notes are welcome, and ideas can be refined as the project develops.
>
> The project is at an early groundwork stage. The plan is to keep improving and tidying the material as we learn, while keeping useful context and decisions visible. This is a friendly starting point, not a task assignment or a safety review.

Suggested home: none needed. flight-stack's [message board](https://github.com/GW412412/flight-stack/discussions) already does this job.

## Proposed next steps for the old repository (G's call)

Nothing below has been done.

1. Merge the pointer pull request in Spray-Drone-master-stack, which adds a notice at the top of its README sending readers to flight-stack.
2. Decide where issues #1 and #2 go: transfer them to flight-stack with GitHub's Transfer issue option, repost #1 on the message board, or leave them where they are. (#1 has since been reposted as [discussion #22](https://github.com/GW412412/flight-stack/discussions/22). Closing the original is still G's call.)
3. Then archive Spray-Drone-master-stack (Settings, Danger Zone, Archive this repository). Archiving makes it read-only rather than deleting it: the code, issues and links stay viewable, and it can be unarchived later. Do steps 1 and 2 first, because an archived repository's README and issues can't be changed. See [GitHub's archiving docs](https://docs.github.com/en/repositories/archiving-a-github-repository/archiving-repositories).
