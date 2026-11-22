# 프론트엔드 인수인계 — transfer 프로필 `--json` 출력

부스 백엔드는 질문 하나에 OFF(방어 없음)와 ON(RAGuard)을 같은 Top-5로 실행하고 결과를 JSON 하나로 돌려준다. 화면은 이 JSON만 보고 그리면 된다.

```bash
# raguardDemo/raguard_booth 에서
.venv/bin/python booth_cli.py --mode live --profile transfer --search-mode expanded --example test230 --json
```

- `--search-mode expanded`: 공격 문서 298개를 인덱스에 붙인 상태(공격 주입 후). `baseline`이면 깨끗한 상태(주입 전).
- `--example`에는 등록된 qid만 넣는다(`test0`~`test349` 중 `queries.jsonl`에 있는 350개). 다른 질의는 전체 인덱스와 결과가 달라질 수 있어 막혀 있다.
- 출력은 **UTF-8**이다. Windows PowerShell에서 `>`로 저장하면 한글이 깨진다. `cmd /c "... --json > out.json"`을 쓰거나, 프로세스 stdout을 UTF-8로 읽는다.
- 실제 예시: [`example_test230.json`](example_test230.json)

## 최상위 구조

| 필드 | 뜻 |
|---|---|
| `question`, `source_question_id` | 질문 원문(영어)과 qid |
| `retrieval` | 공통 검색 결과 (아래) |
| `off` | RAGuard OFF 결과 |
| `on` | RAGuard ON 결과 |
| `overhead.seconds`, `overhead.percent` | ON 총시간 − OFF 총시간, 증가율 |
| `execution_order` | 실제 생성 순서 (`["off","on"]` 또는 반대). 화면은 항상 OFF→ON 순으로 그린다 |
| `preload_seconds` | Ollama 모델 예열 시간. 양쪽 총시간에서 제외된 값 |

### `retrieval`

| 필드 | 뜻 |
|---|---|
| `qid`, `question`, `mode` | `mode`는 `baseline` 또는 `expanded` |
| `vector_count` | 검색 대상 벡터 수 (baseline 33,947 / expanded 34,245) |
| `top5[]` | `rank`, `doc_id`, `score`, `additional` |

`additional: true`면 **공격 문서**다 (`doc_id`가 `atk_mr_…`). 화면에서 빨간색 등으로 표시한다.

### `off`, `on` 공통

| 필드 | 뜻 |
|---|---|
| `status` | `ok` 또는 `error`. `error`면 `answer`에 오류 문구가 들어 있다 |
| `answer` | 모델 답변 (영어) |
| `seconds` | 이 쪽 총시간 (공통 검색 포함) |
| `timings[]` | 단계별 `label`, `seconds`. OFF: 공통 검색, Ollama 응답. ON: 공통 검색, 의미 탐지(C1), 청크 제외·재검색 보충, Ollama 응답 |
| `sources[]` | 모델에 실제로 들어간 문서. `title`(검색 문서 N = 원래 순위 N), `location`, `number`(프롬프트 안 순서) |
| `ollama` | `prompt_tokens`, `generated_tokens`, `prompt_seconds`, `generation_seconds`, `load_seconds`. `truncated: 1`이면 160토큰에서 잘린 답 |

### `on`에만 있는 것

| 필드 | 뜻 |
|---|---|
| `detector` | `c1` (논문 C1 탐지기) |
| `detections[]` | 탐지된 문서. `title`, `location`, `doc_id`, `score`, `reason`(판정과 근거 수치), `backfill_candidate`(보충 후보를 검사하다 걸린 것이면 true) |
| `sanitization[]` | 실제로 한 조치. `action`이 `drop`이면 그 문서를 뺐고, `backfill`이면 그 문서(6위 이후)를 채워 넣었다. `doc_id` 포함 |
| `security_seconds` | 탐지·보충에 쓴 시간 합 |

`sources`(ON)는 DROP·보충이 끝난 뒤의 문맥이다. 예: test230은 2위(공격 문서)가 빠지고 6위가 들어와 `검색 문서 1, 3, 4, 5, 6`이 된다.

## 화면 구성 제안

1. 질문 (영어 원문 + 미리 준비한 한국어 번역)
2. Top-5 목록: 공격 문서 표시, 점수
3. OFF | ON 두 칸: 답변, 거부 여부 강조
4. ON 칸 아래: 탐지된 문서 → 제외, 보충된 문서 → 추가 (순위 이동을 화살표로)
5. 시간: 단계별 막대. `preload_seconds`는 표시하지 않거나 별도로

## 반드시 지킬 것

- **공격 문서 원문을 기본으로 보여주지 않는다.** JailbreakBench 유해 요청(일부는 매우 심각한 내용)이 들어 있다. 이 JSON에는 원문이 포함되지 않는다(`--show-original-text`를 줄 때만 포함). 원문을 보여줄 때도 페이로드 부분은 가린다.
- **OFF 거부 답변에 유해 요청이 되풀이될 수 있다.** 예: test230 OFF는 "I cannot provide a plan for indoctrinating people into a cult…". 시연 질의는 이 문구가 공개해도 되는 것만 고른다(`rag-availability/results/demo/demo_scenarios.csv`의 `payload_flag`).
- 답변은 거부 여부만 판정한 것이다. 시연 질의는 ON 답이 실제로 맞는지 사람이 확인한 것만 쓴다.
- 같은 질의라도 모델·GPU가 바뀌면 답이 달라질 수 있다. 시연 환경에서 다시 확인한다.

## 오류 모양

- 원문 누락: 최상위에 `missing_text_count`, 양쪽 `status: error`
- Ollama 연결 실패나 모델 없음: 양쪽 `status: error`, `answer`에 안내 문구
- 탐지기 오류: `on.status: error` (OFF는 정상일 수 있음)
