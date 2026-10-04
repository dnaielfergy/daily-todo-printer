# Security Policy

This project is intended for personal/local use and does not provide a production security SLA.

## Reporting a security issue

Please do not publish credentials, bot tokens, API keys, personal task data, or sensitive exploit details in a public issue.

For a sensitive finding, contact the maintainer privately through the GitHub profile associated with this repository before disclosing details publicly. If GitHub private vulnerability reporting is enabled for the repository, that is also an appropriate channel.

For non-sensitive bugs or hardening suggestions, a normal GitHub issue is fine.

## Local secrets

Runtime credentials should be supplied through environment variables or other local configuration and must never be committed. The repository ignores `.env` files and local SQLite task databases by default.
