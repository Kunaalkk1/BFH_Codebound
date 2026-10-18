#!/usr/bin/env bash
# Linux/macOS entry point. Python selects the native OS implementation.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${CODEBOUND_PYTHON:-}" ]]; then
    candidates=("$CODEBOUND_PYTHON")
else
    candidates=("$project_root/.venv/bin/python" python3 python3.14 python3.13 python3.12 python3.11 python)
fi
for interpreter in "${candidates[@]}"; do
    if command -v "$interpreter" >/dev/null 2>&1 && "$interpreter" -I -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        exec "$interpreter" "$project_root/launch.py" "$@"
    fi
done
printf '%s\n' 'Python 3.11+ is required. Install it or set CODEBOUND_PYTHON to its executable.' >&2
exit 1
