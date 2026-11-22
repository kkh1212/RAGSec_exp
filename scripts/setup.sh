#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$RAG_DEFENSE_ROOT"
# Explicit path: an inherited VIRTUAL_ENV is never the installation target.
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv --without-pip .venv
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  if ! .venv/bin/python -m ensurepip >/dev/null 2>&1; then
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o .tmp/get-pip.py
    .venv/bin/python .tmp/get-pip.py
  fi
fi
.venv/bin/python -m pip install -e '.[test]'
