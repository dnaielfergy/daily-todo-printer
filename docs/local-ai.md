# Local AI ranking

Issue #8 evaluates a local decision model as an optional ranking layer for the morning receipt.

The deterministic planner remains the permanent fallback. Local AI never owns task state.

## Architecture

The trusted Python application:

1. reads open tasks from SQLite,
2. computes deterministic rank,
3. passes only selected task fields to an isolated Kev subprocess,
4. evaluates a small canonical set of option permutations in one model load,
5. averages each task's probability across those permutations,
6. validates the complete consensus distribution,
7. converts the consensus probabilities to `ai_rank`,
8. falls back wholesale to deterministic rank on any failure,
9. applies `daily.max_items` after final ranking,
10. persists the daily plan and prints normally.

Kev never receives a SQLite connection, shell tool, filesystem tool, Telegram token, printer API, or scheduler API.

The child process receives a scrubbed environment. In normal app execution Hugging Face and Transformers are forced into offline mode, so the model must already be cached locally.

## Runtime

The application stays on its existing Python environment.

Kev uses a separate Python 3.13 environment under:

```text
.ai/kev/.venv/
```

Model/cache data stays under the ignored local `.ai/` directory.

Current evaluation target:

```text
jaredpalmer/kev-0.8b@v1.0
```

The installer pins the Kev 1.0 source revision used by this integration.

## Install the evaluation runtime

From an ordinary PowerShell session in the repository:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install-local-ai.ps1
```

The installer:

- installs `uv` through WinGet if needed,
- installs Python 3.13 through `uv`,
- creates the isolated Kev environment,
- installs the pinned Kev source,
- downloads/caches the pinned Kev-0.8B model,
- runs a local smoke decision.

The first model download requires internet access. Normal application inference is offline-only.

To force CPU evaluation:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install-local-ai.ps1 -Device cpu
```

## Synthetic benchmark

Run the checked-in sanitized benchmark:

```powershell
.\.venv\Scripts\receipt-todo.exe ai-benchmark
```

It reports:

- deterministic expectation pass/fail,
- Kev expectation pass/fail,
- top-choice stability across several deterministic option permutations,
- minimum pairwise rank agreement across those permutations,
- maximum probability movement across permutations,
- model load time,
- inference time.

The cases live at:

```text
eval/ai-ranking/cases.json
```

They contain no personal task data.

Do not enable production AI based only on a successful smoke test. Review the benchmark results first.

## Evaluation database

`ai-eval` defaults to a separate SQLite file:

```text
data/tasks.test.db
```

Seed it with ordinary task commands:

```powershell
.\.venv\Scripts\receipt-todo.exe --db data\tasks.test.db add "Submit reimbursement" --priority high --due 2026-10-10
.\.venv\Scripts\receipt-todo.exe --db data\tasks.test.db add "Organize downloads" --priority low
```

Then compare rankings without persisting a daily plan:

```powershell
.\.venv\Scripts\receipt-todo.exe ai-eval
```

To inspect the ranking against real task meaning on the local machine, opt in to showing task text:

```powershell
.\.venv\Scripts\receipt-todo.exe --db data\tasks.real-eval.db ai-eval --show-text
```

Task text is printed only to the local console; it is not persisted to evaluation output files or sent anywhere by the app.

The evaluation command is read-only with respect to task state and daily-plan persistence.

For a real-data evaluation, create a consistent SQLite snapshot and point the command at the copy. Using SQLite's backup API avoids relying on a raw file copy while Telegram may be running:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect(r'data/tasks.db'); d=sqlite3.connect(r'data/tasks.real-eval.db'); s.backup(d); d.close(); s.close(); print('Created data/tasks.real-eval.db')"
```

Then run the local consensus ranking plus option-order stability check:

```powershell
.\.venv\Scripts\receipt-todo.exe `
  --db data\tasks.real-eval.db `
  ai-eval --show-text --stability
```

If the copied database contains fewer tasks than the configured `daily.max_items`, use an evaluation-only cutoff such as top 3 so printed-set stability is meaningful:

```powershell
.\.venv\Scripts\receipt-todo.exe `
  --db data\tasks.real-eval.db `
  ai-eval --show-text --stability --max-items 3
```

The displayed AI order is the canonical permutation consensus that production uses. The stability report additionally shows:
- whether every underlying single-pass permutation chooses the same winner as the consensus,
- whether the same top-N tasks remain in the printed set,
- minimum pairwise full-rank agreement between the consensus and each raw pass,
- maximum probability movement around the consensus,
- each task's raw rank range across tested option permutations.

Do not use the production database as a disposable test database.

## Configuration split

Existing installations can continue using `config.local.toml`.

For least privilege, migrate to two local ignored files:

```text
config.telegram.toml
config.daily.toml
```

Start from:

```powershell
Copy-Item config.telegram.example.toml config.telegram.toml
Copy-Item config.daily.example.toml config.daily.toml
```

The Telegram runner prefers `config.telegram.toml` and falls back to `config.local.toml`.

The daily runner prefers `config.daily.toml` and falls back to `config.local.toml`.

The daily config must not contain the Telegram bot token.

## Enabling AI

Keep AI disabled during evaluation:

```toml
[ai]
enabled = false
model = "jaredpalmer/kev-0.8b@v1.0"
python_path = ".ai/kev/.venv/Scripts/python.exe"
bridge_path = "scripts/kev_rank.py"
device = "auto"
timeout_seconds = 180
```

Once the model has passed evaluation and the scheduled-task security change below is complete:

```toml
[ai]
enabled = true
```

A missing/broken model still falls back to deterministic ranking.

## Production scheduled-task security

Do not run local AI under `SYSTEM`.

The application actively refuses to launch Kev when it detects the Windows SYSTEM account. The daily receipt therefore falls back to deterministic ranking rather than launching third-party inference code with SYSTEM privileges.

For production AI, create a dedicated non-admin local Windows account for the daily planner and reinstall the morning task under that account:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\install-daily-task.ps1 `
  -At "07:00" `
  -User "$env:COMPUTERNAME\ReceiptTodo"
```

When `-User` is supplied, the installer uses:

- `LogonType S4U`,
- `RunLevel Limited`,
- no stored password,
- no network access for that scheduled logon.

This is intentional: production model files must already be cached locally.

Before switching the scheduled task, the dedicated account must have only the local permissions it needs:

- read/execute the application code and app virtual environment,
- read `.ai/` model/runtime files,
- read `config.daily.toml`,
- read/write `data/`,
- permission to print to the NETUM printer.

Do not grant it Administrator membership or access to `config.telegram.toml`.

Account creation and ACL changes should be performed only after the evaluation proves that Kev is worth enabling. They are the final production-hardening step, not required for synthetic/test-DB evaluation.

## Failure behavior

Any of the following results in deterministic fallback:

- Kev environment missing,
- model files unavailable,
- CUDA/runtime failure,
- timeout,
- malformed bridge output,
- missing/unknown task IDs,
- invalid probability values,
- incomplete ranking.

Task text, priority, due date, creator, source, and status are never modified by the AI path.

## Removal

Local AI is optional. To remove it:

1. set `[ai].enabled = false`,
2. remove the ignored `.ai/` directory if desired.

Telegram, SQLite, deterministic planning, scheduling, and printing continue to work without Kev.
