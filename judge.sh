#!/usr/bin/env sh
# Judge mode: the guided demo in one command. Needs Python 3 and Docker.
cd "$(dirname "$0")" || exit 1
exec "$(command -v python3 || command -v python)" scripts/judge.py "$@"
