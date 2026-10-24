# Contributing

Everyone's ideas are welcome, from humans and AIs alike. This page sets out one shared habit so that everyone stays in the loop: talk about the project in the open, in the repo, and settle questions as a group.

_Protocol drafted by Maya (AI agent, Sesame) at G's request, 25 Oct 2026. Like everything here, it is open to change by the group._

## The two files

- **[docs/DISCUSSION.md](docs/DISCUSSION.md)** is the running log: updates, questions, proposals, and everyone's input on them.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** stays the master record of what has been agreed. Where anything else disagrees with it, DECISIONS.md wins.

## How it works

1. **Log your work and your conversations.** When you finish a piece of work, or a conversation about the project produces something others should know (a result, a finding, a request, an idea), add a dated entry to the top of the log in DISCUSSION.md. Say who you are, and if you are an AI, which one and from where. Keep personal conversation out: only the project goes in.
2. **Raise questions and proposals as open items.** Add them to the Open items list with the next number (O-1, O-2, ...) and the status **Open: input welcome**. Anyone can raise one.
3. **Add your input under the item.** Add a line that starts with the date and your name, then your view: support, objection, a question, evidence, or a better idea. Don't edit or delete someone else's input. Disagreement stays visible.
4. **Propose an outcome.** When the input points somewhere, anyone can add a **Proposed outcome** line under the item and change its status to **Proposed**.
5. **Record what was agreed.** An outcome counts as agreed when at least one contributor other than the proposer supports it and nobody has an objection left unanswered. Then any contributor:
   - changes the item's status to **Agreed YYYY-MM-DD**, with one line naming who agreed
   - adds a dated entry to DECISIONS.md in its usual format, linking back to the item

   **Flight safety needs the owner's explicit OK.** Anything that touches flight safety is not agreed, and does not go into DECISIONS.md, until G, the owner, has given his explicit OK on the item. That covers FlightGate and MavlinkHub, the safety limits (geofence, altitude, speed, rate limits), anything that sends commands or actuates (flight, pump or sprayer), confirm codes, and the hardware. Support from other contributors is not enough on its own: two AI agents can never settle such an item between them. When in doubt whether something touches flight safety, treat it as if it does.

   If the group can't converge, the item stays open and that is written down too. To change a past decision, open a new item. Don't rewrite the old one: follow the supersede rule in DECISIONS.md.

## On GitHub

Do the work on a branch and open a draft pull request for review. Never push straight to main, and never force-push. G owns the repository, so merging on GitHub goes through him. Merging lands work that has been reviewed. It is not a way around the group process above.

If you commit as an AI, use a name that says who you are, for example `Maya (AI agent, Sesame)`. Credits live in CONTRIBUTORS.md.

## Why this shape

The repo already keeps decisions in one file, DECISIONS.md, with dated entries and a supersede rule. One log beside it, read top to bottom, works for anyone, human or model, without new tools. One file per decision (ADR style) would add structure this project doesn't need yet.
