# Contributors

Who did what on this project. Git author names alone do not tell the full story, because some work was committed under generic identities. This file says who actually did that work.

## Maya, AI agent from Sesame

Maya is an AI agent made by Sesame. She works at the direction of the project owner. The work below is hers. Her earlier commits were made under generic git identities, not under her own name, so this file records them by hash. New commits by Maya use the author name "Maya (AI agent, Sesame)".

On `main`:

- `a145861`: added `docs/VISUAL_CENTERING.md`, the visual centering and precise flight commands design note. Committed under the generic identity "Precision Drone Spraying Research <research@precision-drone-spraying.local>".

From PR #1 (branch `prototype/visual-centering-review`, merged into `main` with a merge commit on 2026-10-25):

- `4eb8ead`: the visual centering prototype: `drone_stack/middleware/visual_centering.py`, its 30 tests in `drone_stack/middleware/tests/test_visual_centering.py`, the prototype guide and one line in `docs/INDEX.md`. Committed under the generic identity "Sesame <sesame@localhost>".
- `3a05e40`: renamed the prototype guide to `docs/VISUAL-CENTERING-PROTOTYPE.md`. Committed under the generic identity "Sprite <noreply@sprites.dev>".
- `2a9ff80`: attribution comments in the prototype code, tests and guide. Committed as "Maya (AI agent, Sesame)".

PR #1 was merged with a merge commit, so these hashes are unchanged. On 2026-10-25, after the merge, all four hashes above (`a145861`, `4eb8ead`, `3a05e40`, `2a9ff80`) were checked and are reachable from `main`.

Everything Maya has committed since, on PRs #1, #3, #4 and #5, is under her own author name, "Maya (AI agent, Sesame) <maya-ai-agent@noreply.invalid>", so `git log --author=Maya` lists it and it needs no entry here.

This file and the attribution line in `docs/VISUAL_CENTERING.md` were also added by Maya, at the owner's request. The owner has asked not to be listed on any credit list, so the owner is not named here.

## Other names in the history

The git history also has commits under other author names, such as "Eve of Grok" and "Grok Bot", and 40 more commits under the "Precision Drone Spraying Research" identity. This file makes no claim about who did that work. Only `a145861` under that identity is Maya's.

## Adding yourself

Everyone's ideas are welcome. If you contribute, human or AI, add a line here saying who you are and what you did. Use a git author name that identifies you.
