# Receipt Printer Todo

A local-first todo system that turns a small Windows computer and an 80mm receipt printer into a physical daily planning ritual.

Target printer: **NETUM NS8360 / NT-8360 family**.

## Product loop

1. Tasks are captured throughout the day.
2. SQLite is the source of truth.
3. Incoming tasks from other people can print immediately as small physical tickets.
4. Each morning, a planner selects and orders today's work.
5. The daily plan prints on the receipt printer.
6. Stable printed task IDs make explicit completion reliable: `/done 42`.
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
- [x] Install the NETUM 8360 Windows driver on the mini-server
- [x] Confirm exact Windows printer name
- [x] Print, feed, and cut a real test receipt
- [ ] Tune line width / cut command against the actual NS8360

### Phase 2 — messaging

Start with Telegram because it is inexpensive, easy to run by long polling, and does not require a public web server.

- [x] Private bot with an allowlist of Telegram user IDs
- [x] Map each allowed user to a local alias used as task `created_by`
- [x] Plain messages create tasks
- [x] `/done 42` completes a task
- [x] `/add ...`, `/cancel ...`, `/list`, `/print`
- [x] New tasks from configured allowed users can print immediately
- [x] Run the listener at Windows startup

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
receipt-todo add "Send proposal" --priority high --due 2026-10-05
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


## Telegram setup

Telegram is the first remote task-capture interface. It uses long polling, so the Windows server does not need a public webhook or inbound internet port.

### Commands

Anything that is not a slash command becomes a new todo:

```text
Buy dog food
```

Supported commands:

```text
/add Buy dog food
/done 12
/done 12 13 14
/cancel 12
/cancel 12 13
/priority 12 high
/due 12 2026-10-10
/due 12 clear
/list
/print
/help
/whoami
```

Telegram numeric user IDs are allowlisted and mapped to local aliases. The alias is saved as the task's `created_by`.

### Configure the bot

1. Create a bot with Telegram's BotFather and copy the bot token.
2. Copy the example config:

```powershell
Copy-Item config.example.toml config.local.toml
```

3. Edit `config.local.toml` with the bot token and the exact Windows printer name. This file is ignored by git.
4. To discover your Telegram numeric user ID, start the listener before adding your real ID to the allowlist:

```powershell
receipt-todo telegram
```

5. Send the bot any message. It will refuse access but reply with your numeric Telegram user ID.
6. Add that ID to `config.local.toml` with an alias:

```toml
[telegram.users."123456789"]
alias = "sam"
print_on_create = false
```

Add more allowed users by adding more `telegram.users` tables. Set `print_on_create = true` for any user whose new tasks should immediately print a small incoming-task ticket.

The bot token may alternatively be supplied through `TELEGRAM_BOT_TOKEN`, which overrides the token in the config file.

### Run continuously on Windows

Test the listener manually first:

```powershell
.\.venv\Scripts\receipt-todo.exe telegram --config config.local.toml
```

After the real Telegram flow is validated, open PowerShell as Administrator and install the included startup task:

```powershell
.\scripts\install-telegram-task.ps1
```

The scheduled task runs at Windows startup as `SYSTEM`, restarts after failures, and invokes `scripts\run-telegram.ps1` from the repository so the SQLite database stays at `data/tasks.db`.

## Daily morning receipt

Task priority is explicit task truth:

- `low`
- `medium` (default)
- `high`

Due dates are optional and use `YYYY-MM-DD`.

Set them from the CLI:

```powershell
receipt-todo priority 42 high
receipt-todo due 42 2026-10-10
receipt-todo due 42 clear
```

Configure how many tasks appear on the daily receipt in `config.local.toml`:

```toml
[daily]
max_items = 10
```

The deterministic planner ranks every open task before applying `max_items`. It combines explicit priority with deadline urgency, then uses deadline state, due date, creation time, and stable task ID as tie breakers.

Preview today's persisted plan:

```powershell
receipt-todo daily --preview
```

Print it once:

```powershell
receipt-todo daily
```

A second normal invocation on the same day does not print a duplicate. To deliberately reprint:

```powershell
receipt-todo daily --force
```

Install the native Windows daily task from an elevated PowerShell session, passing the desired 24-hour local time:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-daily-task.ps1 -At "08:00"
```

The deterministic rank is persisted separately from task truth. Daily plan items also reserve nullable `ai_rank` and `final_rank` fields so a later local AI model can replace the ordering while deterministic ranking remains the fallback.

## Data

The default database is `data/tasks.db` and is ignored by git. Backing up that single file backs up the core todo state.

Current task fields intentionally stay small:

- text
- status
- created/completed timestamps
- explicit priority (`low`, `medium`, `high`)
- optional due date
- creator
- source

Do not add a large taxonomy until real use demonstrates the need.


## Contributing

Small, focused contributions are welcome. See `CONTRIBUTING.md` for development and pull request guidance.

Please do not include credentials, bot tokens, personal task data, or local SQLite databases in issues or commits. See `SECURITY.md` for reporting sensitive problems.

## License

MIT. See `LICENSE`.
