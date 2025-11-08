#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_PYTHON="${SCRIPT_DIR}/venv/bin/python"

if [ -x "${DEFAULT_PYTHON}" ]; then
    PYTHON_BIN="${DEFAULT_PYTHON}"
else
    PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [ -z "${PYTHON_BIN}" ]; then
    echo "Error: no se encontró un intérprete de Python (python3 o python)" >&2
    exit 1
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/src/main.py" "$@"
