# RAGuard 시연 웹

`booth_cli.py --profile transfer --json` 결과를 브라우저 한 화면에서 **왼쪽 방어 OFF / 오른쪽 RAGuard(방어 ON)**로 나란히 보여주는 부스용 화면입니다. 디자인은 발표 PPT 테마(미색 배경, 먹색·벽돌 빨강, Noto Sans KR)에 맞췄습니다.

| 파일 | 역할 |
|---|---|
| `index.html` | 화면 전체 (외부 라이브러리 없음, 글꼴만 Google Fonts) |
| `replay.js` | 저장된 실행 결과 묶음. `python build_replay.py`로 `replay/*.json`에서 다시 만듦 |
| `translations.js` | LLM 영어 답변 → 한국어 번역 (원문과 정확히 같을 때만 표시) |
| `replay/*.json` | 런팟(Llama-3-8B-Instruct, vLLM)으로 실행한 결과. 문서 원문은 포함하지 않음 |
| `server.py` | 서버용 API. `~/booth/raguard_booth` 코드를 그대로 import (표준 라이브러리만 사용) |
| `start_web.sh` | 서버에서 시작/중지 |

## 시연 질문

| 버튼 | qid | 정답 |
|---|---|---|
| 01 미국 최고봉 | test328 | Denali |
| 02 타이타닉 침몰 | test243 | 북대서양 |
| 03 합스부르크 수도 | test57 | Vienna |
| 04 로마의 강 | test327 | Tiber |
| 05 네덜란드 국기 | test337 | 빨강·하양·파랑 |
| 06 히로시마 원자폭탄 | test16 | Little Boy |
| 07 톰 브래디 (예비) | test281 | since 2000 |

키보드 숫자 `1`~`7`로도 고를 수 있고, `?q=test328`처럼 주소에 붙이면 바로 재생합니다. 오른쪽 위 입력칸에 질문 ID(`test16`, `16`)를 넣으면 저장된 결과는 재생하고, 없는 질문은 서버가 live 모드일 때 실시간으로 실행합니다.

## 실행 방법

1. **오프라인 리플레이**: `index.html`을 더블클릭. 서버 없이 저장된 7개 사례를 재생합니다.
2. **서버 연결 (리플레이 + 실제 검색)**: 서버 `~/booth_web`에서 `bash start_web.sh`, 노트북에서
   ```powershell
   ssh -N -L 8765:127.0.0.1:8765 ubuntu@<서버>
   ```
   그다음 http://localhost:8765 를 열고 `F11`.
3. **실시간 비교**: 서버에서 `bash start_web.sh live`. `~/.runpod_env`의 런팟 엔드포인트로 LLM을 호출합니다 (요청 시 과금, 콜드 스타트면 첫 응답까지 2~4분). 화면에서 `실시간`을 누르거나 입력칸에 질문 ID를 넣습니다.

서버 배치: `~/booth_web/`(이 폴더) 옆에 `~/booth/raguard_booth`, `~/booth/rag_transfer/data`, `~/booth/.venv`가 있어야 합니다 (`BOOTH_DIR`로 변경 가능).

## API

- `GET /api/health`, `GET /api/queries`, `GET /api/replay/<qid>`
- `GET /api/search?qid=test230&mode=expanded|baseline` — 검색만 (LLM 없음)
- `POST /api/compare?qid=test230&mode=expanded` — OFF/ON 전체 비교 (live 모드에서만)

공격 문서 원문은 어떤 경로로도 내보내지 않습니다 (`booth_cli`의 `_without_transfer_text`와 같음). 시연 목록 밖 질의의 OFF 거부 답변은 화면에서 가려 보여줍니다.

## 리플레이 추가

```bash
curl -s -X POST "127.0.0.1:8765/api/compare?qid=test230&mode=expanded" > replay/test230.json
python3 build_replay.py
```
`index.html`의 `DEMOS`에 `{ qid, tab, ko }`를 추가하면 버튼이 생기고, `translations.js`에 영어 원문 → 번역을 넣으면 한국어 답변이 표시됩니다.
