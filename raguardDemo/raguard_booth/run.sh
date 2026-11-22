#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"

mode="${1:-live}"
case "$mode" in
  live|preview) ;;
  *) echo "사용법  bash run.sh [live|preview] [CLI 옵션]" >&2; exit 2 ;;
esac
if [[ $# -gt 0 ]]; then shift; fi
export PYTHONUTF8=1
python="${BOOTH_VENV_DIR:-.venv}/bin/python"
if [[ "$mode" == "live" && ! -x "$python" ]]; then
  echo "먼저 bash setup.sh live 를 실행하세요." >&2
  exit 1
fi
if [[ "$mode" == "preview" ]]; then python="${BOOTH_PYTHON:-python3}"; fi
exec "$python" booth_cli.py --mode "$mode" "$@"
