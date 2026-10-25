# Security Policy

This project is intended for personal/local use and does not provide a production security SLA.

## Reporting a security issue

Please do not publish credentials, bot tokens, API keys, personal task data, or sensitive exploit details in a public issue.

For a sensitive finding, contact the maintainer privately through the GitHub profile associated with this repository before disclosing details publicly. If GitHub private vulnerability reporting is enabled for the repository, that is also an appropriate channel.

For non-sensitive bugs or hardening suggestions, a normal GitHub issue is fine.

## Local secrets

Runtime credentials should be supplied through environment variables or other local configuration and must never be committed. The repository ignores `.env` files and local SQLite task databases by default.

## Private recipe inbox

- Keep the `household-print-inbox` repository **private**, with household recipes and approved print snapshots only there.
- The Windows printer uses a fine-grained GitHub token with **Contents: Read-only** for only that private repository. Never grant the printer write privileges.
- Keep `recipe_inbox.token` in ignored `config.local.toml` with restrictive filesystem permissions or use `RECIPE_INBOX_TOKEN` under the scheduled task account. Never log or share tokens.
- Published JSON is untrusted input. The importer limits file size and paths, validates all jobs, pins the source to a single commit, and never executes downloaded content.
- No inbound listener is required, and the local SQLite database should never be committed or directly exposed on the internet.
