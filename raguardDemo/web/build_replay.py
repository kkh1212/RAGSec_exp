"""replay/*.json 을 replay.js 하나로 묶는다. index.html 을 서버 없이(file://) 열어도 리플레이가 돌게 하기 위함.

  python build_replay.py            # replay/ → replay.js

리플레이 JSON은 booth_cli --json 기본 출력(원문 제외)이어야 한다. 원문 필드가 있으면 거부한다.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEXT_KEYS = {"text", "before", "after", "removed"}


def has_text(node) -> bool:
    if isinstance(node, dict):
        return any(key in TEXT_KEYS or has_text(value) for key, value in node.items())
    if isinstance(node, list):
        return any(has_text(item) for item in node)
    return False


def main() -> int:
    data = {}
    for path in sorted((ROOT / "replay").glob("*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if has_text(item):
            print(f"{path.name}: 문서 원문 필드가 있어 제외합니다 (--show-original-text 없이 다시 만드세요).", file=sys.stderr)
            continue
        data[path.stem] = item
    js = "window.RAGUARD_REPLAY = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    (ROOT / "replay.js").write_text(js, encoding="utf-8")
    print(f"replay.js: {len(data)}건 ({', '.join(data)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
