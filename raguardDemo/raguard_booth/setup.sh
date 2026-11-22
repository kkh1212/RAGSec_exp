#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

mode="${1:-live}"
case "$mode" in
  live) ;;
  preview) echo "미리보기는 기본 Python만 사용합니다. bash run.sh preview 를 실행하세요."; exit 0 ;;
  *) echo "사용법  bash setup.sh [live|preview]" >&2; exit 2 ;;
esac

venv_dir="${BOOTH_VENV_DIR:-.venv}"
if [[ ! -x "$venv_dir/bin/python" ]]; then
  "${BOOTH_PYTHON:-python3}" -m venv "$venv_dir"
fi
python="$venv_dir/bin/python"

if ! "$python" -c 'import torch; assert tuple(map(int, torch.__version__.split("+")[0].split(".")[:2])) >= (2, 6)' >/dev/null 2>&1; then
  "$python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.6,<3'
fi
"$python" -m pip install -r requirements-live.txt
