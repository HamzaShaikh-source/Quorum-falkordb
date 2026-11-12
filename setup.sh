#!/usr/bin/env sh
# One-command setup. Needs Python 3 and Docker.
cd "$(dirname "$0")" || exit 1
exec "$(command -v python3 || command -v python)" scripts/setup.py "$@"
