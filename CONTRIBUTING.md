# Contributing

This is a small, local-first project. Contributions are welcome, but please keep the implementation as simple as the problem allows.

## Development

Requires Python 3.11+.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m unittest discover -s tests
```

Windows is the primary runtime because printer output currently uses the Windows print spooler. Core task logic should remain portable where practical.

## Pull requests

- Keep changes focused and easy to review.
- Add or update tests for non-trivial behavior.
- Do not commit local databases, credentials, bot tokens, API keys, or `.env` files.
- Avoid new dependencies unless they remove meaningful complexity.
- Preserve SQLite as the source of truth; messaging and AI integrations should stay adapters around it.

For larger behavior changes, opening an issue first is helpful but not required.
