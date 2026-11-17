#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
PROJECT_DIR="$PWD"
export PLAYWRIGHT_BROWSERS_PATH="$PROJECT_DIR/.browsers"
export UV_CACHE_DIR="$PROJECT_DIR/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PROJECT_DIR/.python"

if [[ ! -x .venv/bin/python ]]; then
    if [[ -e .venv ]]; then
        echo 'Existing .venv is not a Unix environment. Move it aside and rerun setup.' >&2
        exit 1
    fi
    if [[ -n "${PYTHON:-}" ]]; then
        "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
            echo 'PYTHON must point to Python 3.10 or newer.' >&2
            exit 1
        }
        "$PYTHON" -m venv .venv
    elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        python3 -m venv .venv
    elif command -v uv >/dev/null 2>&1; then
        echo 'Creating a project-local Python 3.12 environment using uv.'
        uv venv --python 3.12 --seed .venv
    else
        echo 'Python 3.10+ is required. Install it (or uv), then rerun setup.' >&2
        echo 'To select an installed interpreter: PYTHON=/path/to/python3 ./setup.sh' >&2
        exit 1
    fi
fi

.venv/bin/python -c 'import sys; print(sys.version); sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
    echo 'Existing .venv needs Python 3.10+. Move it aside and rerun setup.' >&2
    exit 1
}
.venv/bin/python -m pip install --no-cache-dir -r requirements-lock.txt
.venv/bin/python -m playwright install chromium
echo 'Setup complete. Run ./run.sh to collect NBA odds in Chromium.'
