# Agent instructions

## Product goal
Build a local-first tactile todo system that runs on a Windows mini-server and prints an intentional daily plan on an 80mm NETUM NS8360 receipt printer.

## Source of truth
SQLite is the source of truth for task state. Messaging systems and AI models are adapters, not databases.

## Engineering rules
- Windows is the primary runtime. Do not introduce Linux-only assumptions.
- Prefer the Windows print spooler and the official NETUM driver over direct USB access unless testing proves that path insufficient.
- Prefer Python standard library and local execution. Add dependencies only when they remove meaningful complexity.
- Do not require a paid cloud service for core task capture, completion, persistence, or printing.
- LLMs must not be required for correctness. The eventual planner may rank tasks, but it must return a plan without mutating task truth.
- Keep task IDs stable and printable so explicit commands such as `done 42` always work.
- Build milestones sequentially: local task engine -> physical printer -> messaging -> daily AI prioritization.
- Do not add projects, tags, energy levels, estimation systems, or other task taxonomy until real usage justifies them.

## Recipe print pipeline
- Working source: existing household Notion recipe library and editable meal plans (integration later).
- Published source: private GitHub `household-print-inbox` manifest and immutable print snapshots.
- Execution source: local SQLite print queue and history (never committed to GitHub).
- One-way only: working -> published -> executed. Explicit approval before publishing.
- The printer host receives only data, not commands or executable scripts from the inbox.
- GitHub inbox token must be read-only, narrowly scoped and kept outside the repository.
- Phase A imports approved snapshots. #17 reconciles revisions/withdrawals while preserving print history. #18 adds a single grocery list per week, revision-linked to the manifest. Grocery totals/quantities are agent-authored and human-approved; the printer never calculates amounts. Never print a withdrawn job, even via force/requeue. Both recipe and grocery printing stay manual until #20.
