# Imported reference: Spray-Drone-master-stack

**Reference only. Not authoritative.** [docs/DECISIONS.md](../../docs/DECISIONS.md) is the master record for this project. Where anything in this folder disagrees with it, DECISIONS.md wins. Nothing in this folder is a flight-safety instruction, a tested configuration, or a decision the group has agreed.

## Where this came from

- Source repository: https://github.com/GW412412/Spray-Drone-master-stack
- Source commit: [`873dadd6e94d33168a664bcc04636c96d3b31c3a`](https://github.com/GW412412/Spray-Drone-master-stack/commit/873dadd6e94d33168a664bcc04636c96d3b31c3a) (branch `main`)
- Written by: commit author "Spray Drone" (spray-drone@users.noreply.github.com), 61 commits on 23 October 2026, 08:30 to 08:57 AEDT. Spray Drone is the name DECISIONS.md uses for the conversation model.
- Imported by: Maya (AI agent, Sesame), 2026-10-26, at G's request, so the group works in one repository.

Every file from that commit is copied here byte for byte, with no edits. The full commit history, with each original commit, stays in the source repository, which is left in place so existing links keep working.

## What this means in practice

- The files here keep their own wording, including "private repository" and "keep the repository private". Both repositories are public; that wording is historical.
- `.github/` inside this folder is inert. GitHub only reads workflows and templates from the repository root, so the copied CI workflow, pull request template and bug report template do nothing here.
- The `spray_drone` Python package is not wired into `drone_stack/`. It has no MAVLink link and no actuator output. Its `CommandGate` is a separate design from `drone_stack/middleware/safety_gate.py` (FlightGate), which is the gate this project uses.
- `data/`, `logs/`, `reports/` and `simulation/results/` are placeholder folders from the source; the root `.gitignore` would normally skip `data/` and `logs/`, so their placeholder files were added deliberately.

## Running its tests in place

```bash
cd reference/spray-drone-master-stack
python -m pip install pytest ruff
python -m pytest -q        # 19 passed on 2026-10-26 (Python 3, software only)
ruff check src tests       # all checks passed on 2026-10-26
python simulation/dry_run_demo.py   # needs PYTHONPATH=src unless installed with pip install -e .
```

These runs are software only. No SITL, bench or flight test was run.

What moved, what overlaps, and where this material conflicts with the rest of flight-stack: [docs/MIGRATION-SPRAY-DRONE-MASTER-STACK.md](../../docs/MIGRATION-SPRAY-DRONE-MASTER-STACK.md).
