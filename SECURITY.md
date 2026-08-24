# Security policy

## Supported scope

This repository is an educational, under-development paper-trading project. It
does not support live brokerage orders. Security fixes are applied to the latest
version on the default branch.

## Reporting a vulnerability

Do not post credentials, account data, or an exploitable security issue in a
public issue. Use the repository's private GitHub security-advisory feature so
the report can be reviewed before disclosure.

If a credential is ever exposed, revoke it with the provider immediately. Git
history cleanup is not a substitute for credential rotation.

## Local-data boundary

`.env`, `logs/`, `output/`, `private_data/`, databases, JSONL event files, and
generated trade records are intentionally excluded from Git. They may contain
sensitive local data. The application creates new runtime artifacts with
user-only permissions where the operating system supports POSIX modes.

The Streamlit dashboard binds to `127.0.0.1` by default. Exposing it to a LAN or
the internet is unsupported and may reveal local logs or trading data.
