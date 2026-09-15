#!/usr/bin/env bash
# Optional dependency installation only. Never invoked by doctor/demo/tests.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$RAG_DEFENSE_ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Prepare this project .venv first.' >&2
  exit 2
fi
.venv/bin/python -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.2,<3'
.venv/bin/python -c 'import torch; from pathlib import Path; assert torch.version.cuda is None, "CPU-only torch required"; Path(".tmp/observer-cpu-constraints.txt").write_text("torch==" + torch.__version__ + "\n")'
.venv/bin/python -m pip install -c .tmp/observer-cpu-constraints.txt -e '.[ml]'
.venv/bin/python -c 'import torch; assert torch.version.cuda is None, "CPU-only torch required"; print("CPU dependencies ready; no encoder downloaded")'
