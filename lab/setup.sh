#!/usr/bin/env bash
# The lab's Python environment, .venv-lab at the checkout's root: requirements.txt and
# requirements-lab.txt. Run once, and again after either changes.
#
#   lab/setup.sh                 (PYTHON=python3.12 lab/setup.sh to choose the interpreter)
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
[ -d .venv-lab ] || "$PYTHON" -m venv .venv-lab
.venv-lab/bin/python -m pip install --quiet --upgrade pip
.venv-lab/bin/python -m pip install --quiet -r requirements.txt -r requirements-lab.txt
echo "Ready: .venv-lab/bin/python -m lab status"
