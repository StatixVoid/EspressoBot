#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 scripts/launch.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python scripts/launch.py "$@"
else
    echo 'Install Python 3.11 or newer (3.12 recommended), then run again.' >&2
    exit 1
fi
