# Hardware-free CI: workflow waiting to be moved into place

Added by Maya (AI agent, Sesame), 2026-10-27, at G's request.

`github-actions/ci.yml` in this folder is a GitHub Actions workflow. It should live at `.github/workflows/ci.yml`. It is parked here because GitHub refused the push. The maya-flightstack GitHub App does not have the `workflows` permission, and GitHub won't let an app create a workflow file without it. The exact refusal was:

> refusing to allow a GitHub App to create or update workflow `.github/workflows/ci.yml` without `workflows` permission

## What the workflow does

It runs on every push to `main` and every pull request, and you can also start it by hand. It runs on GitHub's Ubuntu runners with Python 3.12, and it does three things:

1. installs `drone_stack/middleware/requirements.txt`
2. runs `python -m compileall -q drone_stack/middleware scripts`
3. runs the offline suite: `python -m pytest -q -p no:cacheprovider drone_stack/middleware/tests`

Ruff also runs, but only to report. It checks syntax errors and pyflakes, and it never fails the build. The run saves a `pip freeze` file, so each green run records the exact package versions it used.

The workflow doesn't touch an aircraft, camera, radio, pump, SITL binary or network service. It changes no code, config or runtime behaviour. A green run only means the offline tests passed. It isn't a flight approval.

## How to move it into place (one step, from your browser)

1. On GitHub, switch to the branch `maya/ci-hardware-free` and open `ci/github-actions/ci.yml`.
2. Click the pencil (edit). In the file-name box at the top, change the path to `.github/workflows/ci.yml`. Typing `../` moves up one folder, so delete back to the repo root and type `.github/workflows/ci.yml`.
3. Commit straight to `maya/ci-hardware-free`.

Your own account has workflow rights, so GitHub accepts the move from your session. The PR then runs the workflow on itself, and you'll see the result in the PR's Checks tab. Delete this README in the same PR, or leave it. It's harmless.

## What this deliberately does not do

- It doesn't turn on branch protection or "required status checks". That's a settings change, and it's your call. Once a few runs have come back green, the suggested setting is Settings, then Branches, then the rule for `main`, then "Require status checks to pass", choosing `compile + offline pytest (Python 3.12)`.
- It doesn't add Dependabot, a lockfile, pyproject, mypy, bandit or a SITL job. Those are possible later steps, listed in the review-record discussion.
