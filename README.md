# Receipt Printer Todo

A local-first todo system that turns a small Windows computer and an 80mm receipt printer into a physical daily planning ritual.

Target printer: **NETUM NS8360 / NT-8360 family**.

## Product loop

1. Tasks are captured throughout the day.
2. SQLite is the source of truth.
3. Incoming tasks from other people can print immediately as small physical tickets.
4. Each morning, a planner selects and orders today's work.
5. The daily plan prints on the receipt printer.
6. Stable printed task IDs make explicit completion reliable: `done 42`.
7. An eventual AI planner can prioritize the day, but never owns task state.

## Why Windows-native

The always-on machine is Windows. The first printer path therefore uses the normal Windows print spooler with the official NETUM driver rather than taking ownership of the USB device directly. The app sends raw ESC/POS bytes to the installed printer.

Linux is not required. We should only revisit Linux if physical printer testing proves the Windows spooler path unreliable.

## Roadmap

### Phase 1 — local task engine + physical printer

- [x] SQLite task store
- [x] Stable numeric task IDs
- [x] Add/list/complete CLI
- [x] Plain 80mm receipt renderer
- [x] Windows raw-spooler printer adapter
- [ ] Install the NETUM 8360 Windows driver on the mini-server
- [ ] Confirm exact Windows printer name
- [ ] Print, feed, and cut a real test receipt
- [ ] Tune line width / cut command against the actual NS8360

### Phase 2 — messaging

Start with Telegram because it is inexpensive, easy to run by long polling, and does not require a public web server.

- [ ] Private bot / chat allowlist
- [ ] Plain messages create tasks
- [ ] `done 42` completes a task
- [ ] `add ...`, `cancel ...`, `list`, `print`
- [ ] Wife-created tasks can print immediately
- [ ] Run the listener at Windows startup

### Phase 3 — daily ritual

- [ ] Windows Task Scheduler invokes morning planning/printing
- [ ] Carry unfinished work forward automatically
- [ ] Record daily plan selections separately from task truth
- [ ] Refine the physical receipt hierarchy and density

### Phase 4 — AI prioritization

The planner receives factual state (open tasks, age, deadlines, explicit must-do flags, creator, recent plan history, and optionally calendar context) and returns an ordered daily plan.

Important constraints:

- The planner does **not** complete, delete, or rewrite tasks.
- Explicit user overrides beat model judgment.
- A failed AI call must never prevent task capture or state updates.
- We should evaluate prioritization quality from real usage before adding more task metadata.
- Prefer one small model call per day over continuous agentic inference.

## Local setup

Requires Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Add and inspect tasks:

```powershell
receipt-todo add "Call dentist"
receipt-todo add "Send proposal" --must-do --due 2026-10-05
receipt-todo list
receipt-todo done 1
receipt-todo preview
```

### Printer setup on Windows

1. Install the NETUM 8360-series Windows driver.
2. Connect the printer and confirm a Windows test page / vendor test works.
3. Open **Settings -> Bluetooth & devices -> Printers & scanners** and copy the exact printer name.
4. Set it for the current shell:

```powershell
$env:RECEIPT_PRINTER_NAME = "YOUR EXACT PRINTER NAME"
```

5. Print the current task receipt:

```powershell
receipt-todo print
```

You can also pass the name directly:

```powershell
receipt-todo print --printer-name "YOUR EXACT PRINTER NAME"
```

## Data

The default database is `data/tasks.db` and is ignored by git. Backing up that single file backs up the core todo state.

Current task fields intentionally stay small:

- text
- status
- created/completed timestamps
- optional due date
- creator
- source
- optional `must_do` override

Do not add a large taxonomy until real use demonstrates the need.
