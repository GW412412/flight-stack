# Agent instructions

These instructions apply to every human and AI coding agent working in this repository.

## Authority and source of truth
- Read `docs/MASTER_PLAN.md` before planning implementation work.
- Treat it as the project's existing planning reference, not as proof that any behavior has been implemented or verified.
- Read relevant architecture, requirements, interfaces, and safety documents before changing affected code.
- If documents conflict or a requirement is unclear, stop and report the conflict; do not silently invent a decision.
- Distinguish verified facts, proposals, assumptions, and open questions.

## Change discipline
- Make small, reviewable changes limited to the requested task.
- Inspect existing files before editing. Do not overwrite unrelated work.
- Update documentation and tests whenever behavior or interfaces change.
- Do not delete files, change public interfaces, alter dependencies, or change flight parameters without explaining the reason and impact.
- Never claim a command or test ran unless it actually ran; report exact commands and results.
- Do not commit or push changes unless explicitly authorised for the task.

## Safety boundaries
- Default to simulation and read-only telemetry.
- Do not enable real motor, flight, or spray actuation by default.
- Never bypass an interlock or weaken a safety check to make a test pass.
- AI-generated commands must pass through the specified command gateway and authority checks; no alternate direct command path is acceptable.
- Pilot takeover must latch AI authority off. Reconnection, model restart, heartbeat recovery, queued work, or a new prompt must not clear that latch.
- Pump permission must default off and be independently enforced onboard. Ground-station logic alone is not proof of safe shutdown.
- Do not select unresolved firmware-specific failsafe settings or height-control parameters by guesswork.
- Props remain off and ESC power disconnected during bench work unless a separately approved test procedure says otherwise.
- No real-world flight or chemical application is authorised by this repository's existence.

## Security and data
- Never store passwords, access tokens, private keys, or secrets in the repository or logs.
- Keep the repository private unless the owner explicitly approves publication.
- Do not commit personal data, raw telemetry containing sensitive details, or large generated datasets without review.
- Use least privilege and ask before consequential external actions.

## Required completion report
For each task, state:
1. Goal and scope.
2. Files created or changed.
3. Requirements addressed.
4. Tests and exact commands run, with passed/failed/skipped/unrun clearly separated.
5. Remaining uncertainties, risks, and next steps.
