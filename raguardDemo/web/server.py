"""RAGuard 부스 웹 서버.

booth_cli.py --profile transfer --json 과 같은 결과를 HTTP로 돌려주고 index.html을 서빙한다.
표준 라이브러리만 쓴다(추가 설치 없음). 부스 코드는 BOOTH_DIR/raguard_booth 에서 그대로 import 한다.

  BOOTH_DIR=~/booth ~/booth/.venv/bin/python server.py            # 127.0.0.1:8765
  노트북에서:  ssh -L 8765:127.0.0.1:8765 ubuntu@<서버>  →  http://localhost:8765

실시간 비교(/api/compare)는 LLM을 호출한다. BOOTH_LLM_BACKEND=openai 면 런팟 서버리스로 가며 과금될 수 있다.
BOOTH_WEB_LIVE=0 이면 실시간 비교를 끄고 리플레이·검색만 제공한다.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from urllib.parse import parse_qs, urlparse

WEB_ROOT = Path(__file__).resolve().parent
BOOTH_DIR = Path(os.path.expanduser(os.getenv("BOOTH_DIR", "~/booth"))).resolve()
APP_DIR = BOOTH_DIR / "raguard_booth"
TRANSFER_DIR = Path(os.path.expanduser(os.getenv("BOOTH_TRANSFER_DIR", str(BOOTH_DIR / "rag_transfer")))).resolve()
REPLAY_DIR = Path(os.path.expanduser(os.getenv("BOOTH_REPLAY_DIR", str(WEB_ROOT / "replay")))).resolve()
LIVE_ENABLED = os.getenv("BOOTH_WEB_LIVE", "1").strip() != "0"
HOST = os.getenv("BOOTH_WEB_HOST", "127.0.0.1")
PORT = int(os.getenv("BOOTH_WEB_PORT", "8765"))
MODES = ("baseline", "expanded")

sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)  # booth 모듈이 상대 경로를 쓰는 경우 대비

from transfer_index import TransferIndex, load_questions  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("booth-web")


class Engine:
    """검색 인덱스와 런타임을 모드별로 한 번만 만든다. 비교는 한 번에 하나씩."""

    def __init__(self):
        self.questions = {q["qid"]: q["text"] for q in load_questions(TRANSFER_DIR)}
        self._indexes: dict[str, TransferIndex] = {}
        self._runtimes: dict[str, object] = {}
        self._index_lock = threading.Lock()
        self._run_lock = threading.Lock()

    def index(self, mode: str) -> TransferIndex:
        with self._index_lock:
            if mode not in self._indexes:
                start = perf_counter()
                self._indexes[mode] = TransferIndex(TRANSFER_DIR, expanded=mode == "expanded")
                log.info("index %s ready (%.1fs)", mode, perf_counter() - start)
            return self._indexes[mode]

    def search(self, qid: str, mode: str) -> dict:
        retrieval = self.index(mode).search(qid)
        retrieval["additional_count"] = sum(hit["additional"] for hit in retrieval["top5"])
        return retrieval

    def compare(self, qid: str, mode: str) -> dict:
        from booth_cli import _without_transfer_text
        from booth_runtime import build_transfer_runtime

        with self._run_lock:
            if mode not in self._runtimes:
                self._runtimes[mode] = build_transfer_runtime(self.index(mode))
            result = self._runtimes[mode].compare(qid)
        result = {
            **result,
            "mode": "live",
            "profile": "transfer",
            "question": self.questions[qid],
            "source_question_id": qid,
        }
        # 공격 문서 원문은 절대 내보내지 않는다 (booth_cli 기본 출력과 같음)
        result = _without_transfer_text(result)
        retrieval = result.get("retrieval")
        if retrieval:
            retrieval["additional_count"] = sum(hit["additional"] for hit in retrieval["top5"])
        return result

    def health(self) -> dict:
        backend = os.getenv("BOOTH_LLM_BACKEND", "ollama").strip().lower() or "ollama"
        return {
            "live": LIVE_ENABLED,
            "backend": backend,
            "model": os.getenv("BOOTH_MODEL", "").strip() or None,
            "questions": len(self.questions),
            "busy": self._run_lock.locked(),
        }


def replay_files() -> dict[str, Path]:
    if not REPLAY_DIR.is_dir():
        return {}
    return {path.stem: path for path in sorted(REPLAY_DIR.glob("*.json"))}


class Handler(SimpleHTTPRequestHandler):
    engine: Engine

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": message}, status)

    def _params(self):
        url = urlparse(self.path)
        return url.path, {key: values[0] for key, values in parse_qs(url.query).items()}

    def _qid_mode(self, params):
        qid = params.get("qid", "")
        mode = params.get("mode", "expanded")
        if qid not in self.engine.questions:
            raise ValueError("등록되지 않은 질문 ID입니다.")
        if mode not in MODES:
            raise ValueError("mode는 baseline 또는 expanded 여야 합니다.")
        return qid, mode

    def do_GET(self):
        path, params = self._params()
        if not path.startswith("/api/"):
            # 정적 파일은 index.html, replay.js, 이미지 정도만. 서버 코드·리플레이 원본 디렉터리 노출 방지.
            if path in ("/", "/index.html", "/replay.js", "/translations.js") or path.startswith("/assets/"):
                return super().do_GET()
            return self._error("not found", HTTPStatus.NOT_FOUND)
        try:
            if path == "/api/health":
                return self._json(self.engine.health())
            if path == "/api/queries":
                replays = replay_files()
                return self._json([
                    {"qid": qid, "question": text, "replay": qid in replays}
                    for qid, text in self.engine.questions.items()
                ])
            if path.startswith("/api/replay/"):
                qid = path.rsplit("/", 1)[-1]
                file = replay_files().get(qid)
                if file is None:
                    return self._error("리플레이가 없습니다.", HTTPStatus.NOT_FOUND)
                return self._json(json.loads(file.read_text(encoding="utf-8")))
            if path == "/api/search":
                qid, mode = self._qid_mode(params)
                return self._json(self.engine.search(qid, mode))
            return self._error("not found", HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("GET %s failed", path)
            return self._error(f"서버 오류 ({type(exc).__name__})", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        path, params = self._params()
        if path != "/api/compare":
            return self._error("not found", HTTPStatus.NOT_FOUND)
        if not LIVE_ENABLED:
            return self._error("실시간 비교가 꺼져 있습니다 (BOOTH_WEB_LIVE=0).", HTTPStatus.FORBIDDEN)
        try:
            qid, mode = self._qid_mode(params)
            if self.engine._run_lock.locked():
                return self._error("다른 비교가 실행 중입니다. 잠시 후 다시 시도하세요.", HTTPStatus.CONFLICT)
            start = perf_counter()
            result = self.engine.compare(qid, mode)
            log.info("compare %s %s %.1fs", qid, mode, perf_counter() - start)
            return self._json(result)
        except ValueError as exc:
            return self._error(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("compare failed")
            return self._error(f"실시간 비교 실패 ({type(exc).__name__})", HTTPStatus.INTERNAL_SERVER_ERROR)


def main():
    Handler.engine = Engine()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("RAGuard web on http://%s:%d  live=%s backend=%s data=%s",
             HOST, PORT, LIVE_ENABLED, os.getenv("BOOTH_LLM_BACKEND", "ollama"), TRANSFER_DIR)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
