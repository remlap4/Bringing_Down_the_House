#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.browsers"

if [[ ! -x .venv/bin/python ]]; then
    echo 'Project environment missing. Run ./setup.sh first.' >&2
    exit 1
fi
if ! .venv/bin/python -c 'import sys, bs4, playwright; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo 'Python environment or dependencies unavailable. Run ./setup.sh first.' >&2
    exit 1
fi

# exec preserves the collector exit code and forwards interrupt signals.
exec .venv/bin/python scraper.py capture --method browser --headed --via-home "$@"
