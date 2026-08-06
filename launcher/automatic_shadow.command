#!/bin/zsh

set -euo pipefail

SCRIPT_DIRECTORY="${0:A:h}"
PROJECT_DIRECTORY="${SCRIPT_DIRECTORY:h}"

cd "$PROJECT_DIRECTORY"
exec "$PROJECT_DIRECTORY/.venv/bin/python" -m bot shadow-schedule start
