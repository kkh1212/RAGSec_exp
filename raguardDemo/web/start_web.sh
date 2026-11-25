#!/usr/bin/env bash
# RAGuard 웹 서버 (재)시작.  서버의 ~/booth_web 에서:
#   bash start_web.sh          # 리플레이 + 검색만 (LLM 호출 없음)
#   bash start_web.sh live     # 실시간 비교 켬. ~/.runpod_env 를 읽어 런팟 서버리스 LLM 사용 (과금)
#   bash start_web.sh stop
set -euo pipefail
cd -- "$(dirname -- "$0")"
mode="${1:-replay}"

if [[ -f server.pid ]] && kill -0 "$(cat server.pid)" 2>/dev/null; then
  kill "$(cat server.pid)"; sleep 1
fi
rm -f server.pid
[[ "$mode" == "stop" ]] && { echo "중지했습니다."; exit 0; }

export BOOTH_DIR="${BOOTH_DIR:-$HOME/booth}"
export PYTHONUTF8=1
if [[ "$mode" == "live" ]]; then
  [[ -f "$HOME/.runpod_env" ]] && { set -a; . "$HOME/.runpod_env"; set +a; }
  export BOOTH_WEB_LIVE=1
else
  export BOOTH_WEB_LIVE=0
fi

setsid nohup "$BOOTH_DIR/.venv/bin/python" server.py > server.log 2>&1 < /dev/null &
echo $! > server.pid
sleep 3
tail -n 3 server.log
echo "노트북에서: ssh -N -L 8765:127.0.0.1:8765 ubuntu@<서버>  →  http://localhost:8765"
