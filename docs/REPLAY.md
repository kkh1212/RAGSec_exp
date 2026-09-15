# JSONL replay 형식

replay는 기록된 **정확한 입력**에 대응하는 결과만 반환한다. 원본 응답으로 수정 후 결과를 추측하지 않는다. 현재 포함된 [synthetic_success.jsonl](../tests/fixtures/synthetic_success.jsonl)은 합성 성공 시나리오를 기록한 작은 예제이며 실제 공격 실험 로그가 아니다.

## 실행

```bash
# RAGSec_exp 저장소 루트에서 실행
source scripts/env.sh
.venv/bin/python -m rag_defense.cli replay --input tests/fixtures/synthetic_success.jsonl
```

기본 설정에서 `mode=replay`, `status=PATCH_APPLIED`, 추가 생성 시도 10회를 출력한다. 이 횟수는 기록 조회를 위한 adapter 호출 수이며 실제 API 요청 수가 아니다. CLI는 별도 메타데이터 보고서를 `runs/`에 저장한다.

replay CLI의 기본은 기록 당시의 `configs/defense.mock.yaml`(v1)이다. 일반 `DefenseConfig()`/demo의 새 기본 v2와 구분한다. 기존 export script도 v1을 명시하며, `--output .tmp/new-synthetic-replay.jsonl`로 원래 fixture를 덮어쓰지 않고 검사할 수 있다.

다른 기록은 사용자가 먼저 이 독립 프로젝트 안으로 복사한 완결된 JSONL 파일이어야 한다. CLI와 loader는 프로젝트 밖의 경로 및 밖으로 이어지는 심볼릭 링크를 거부한다. 원래 RAG 프로젝트의 로그를 탐색하거나 직접 읽는 기능은 없다.

## 파일 구성

UTF-8 텍스트에서 한 줄은 JSON 객체 하나다. 빈 줄은 건너뛴다. 첫 행은 `request` manifest이며 이후에는 generation, observation, verification 행을 둔다. 파일 하나에 요청 manifest는 한 개만 허용한다. 현재 `schema_version`은 `rag-defense-contract-v1`이다.

| kind | 필요한 필드와 내용 |
| --- | --- |
| `request` | `schema_version`, `request`, `request_fingerprint`, `generator_version`, `observer_version`, `verifier_version` |
| `generation` | `input`, `request_fingerprint`, `result` |
| `observation` | `query`, `response`, `key`, `result` |
| `verification` | `query`, `response`, `evidence`, `key`, `result` |

`request` 값은 DefenseRequest의 JSON 표현이다. `documents`에는 Document의 모든 필드, `initial_response`에는 최초 GenerationResult를 담는다. 정답·공격 라벨 등 알 수 없는 요청·문서 필드는 허용하지 않는다. 별도의 평가 자료를 adapter 입력에 추가하지 않는다.

`generation.input`의 필드 집합은 정확히 다음과 같다.

```text
query, documents, generation_fingerprint, generator_version,
contract_version, fresh, replicate_id
```

documents는 해당 시도에 **실제로 전달했던 수정 후 문서들**이다. 문서 순서를 보존하며 원본 문서 ID만 기록해서는 재생할 수 없다. result는 `text`, `execution_status`, `generation_fingerprint`, `usage`, `latency_ms`를 갖는 GenerationResult다. usage의 `provider_attempts`는 0 또는 1이어야 한다.

observation.result는 `state`, `confidence`, `observer_version`을 갖는다. verification.evidence는 `RemainingSpan(doc_id, field, start_char, end_char, text)`의 목록이며, result에는 `status`, `addresses_query`, `supported`, `citations`, `verifier_version`, `reason`을 담는다. Citation에는 `doc_id`, `field`, `start_char`, `end_char`, `quote`가 필요하다. 좌표는 수정본 위치가 아니라 남아 있는 원문의 Python 문자 위치다.

전체 직렬화 예시는 fixture에서 확인할 수 있다. [make_synthetic_replay.py](../scripts/make_synthetic_replay.py)는 같은 계약으로 mock callback 결과를 수집하는 코드 예시다.

## fingerprint와 반복 기록

SHA-256 key를 직접 조합하지 말고 [fingerprint.py](../src/rag_defense/fingerprint.py)의 함수를 사용한다. 직렬화에는 `to_jsonable`을 사용하면 dataclass·tuple·enum을 JSON 값으로 변환할 수 있다.

| 대상 | key 생성 함수 |
| --- | --- |
| 전체 최초 요청 | `request_key(request)` |
| 생성 입력 | `generation_key(query, documents, fingerprint, generator_version, fresh=..., replicate_id=...)` |
| 생성 input 객체 | `generation_input(query, documents, fingerprint, generator_version, fresh=..., replicate_id=...)` |
| 관측 | `observation_key(query, response, observer_version)` |
| 검증 | `verification_key(query, response, evidence, verifier_version)` |

key에는 계약 버전이 포함된다. generation에는 모든 문서 필드와 순서, 생성 설정 fingerprint, 생성기 버전, fresh와 replicate_id도 포함된다. 최초 요청의 fingerprint에는 initial_response와 query_policy 등을 포함한 전체 요청이 반영된다.

fresh 기록에는 비어 있지 않은 replicate_id가 필요하며, 동일 ReplayGenerator 인스턴스에서 같은 fresh key는 한 번만 소비할 수 있다. 기본 엔진은 `original_retry:1`처럼 phase와 호출 순서로 ID를 만든다. 설정·탐색 순서가 달라지면 원문이 같아도 필요한 ID와 입력 key가 달라질 수 있다. 따라서 기록 수집 시의 엔진·설정·adapter 버전을 함께 보관하고 같은 조건으로 재생한다. replay 설정 자체는 manifest에 저장되지 않으므로 수집 시 사용한 설정을 별도로 보관해야 한다.

각 테이블에서 중복 key는 오류다. 같은 질문·응답을 관측한 결과가 반복되면 동일 버전의 관측 결과를 한 번만 기록한다. 관측·검증 key에는 반복 ID가 없으므로 동일 입력에 확률적으로 서로 다른 판정을 여러 개 기록하는 형식은 지원하지 않는다.

## 기록이 없거나 잘못된 경우

- 생성 기록이 없거나 같은 fresh 기록을 재소비하면 실행 상태 `REPLAY_MISS`와 빈 텍스트를 반환한다. 이는 의미상 REFUSAL이 아니다.
- 관측 기록이 없으면 UNCERTAIN, confidence 0을 반환한다.
- 검증 기록이 없으면 UNCERTAIN, reason `replay_miss`를 반환한다.
- 요청/input key 불일치, 버전 혼합, 중복 key, 알 수 없는 kind는 loader 오류다. CLI는 원문을 출력하지 않고 입력 오류로 종료한다.

`REPLAY_MISS`는 생성 실행 상태이며 DefenseStatus의 별도 종료 코드가 아니다. 최초 재시도 누락이면 EXECUTION_ERROR가 될 수 있고, 문서 시험 누락이면 NO_REPAIR_FOUND 등 탐색 단계에 맞는 상태가 될 수 있다. 원인 구분에는 trace의 execution_status를 확인한다. CLI 종료 코드 0은 처리를 끝냈다는 뜻이며 방어 성공 여부는 JSON의 status로 판단한다.

## 보고서와 기록의 차이

`save_run`의 JSON은 질문·문서·응답·인용 원문을 생략한 메타데이터 보고서다. 이것을 JSONL로 이름만 바꿔 replay 입력으로 사용할 수 없다. 완전한 replay에는 실제 원문과 수정 입력 및 그에 대한 생성·관측·검증 결과가 필요하다.

fingerprint 검사는 입력의 내부 일관성을 확인한다. 결과 텍스트의 진실성, 로그 출처나 무결성을 인증하는 전자서명이 아니다. 원문이나 결과와 함께 key까지 새로 만든 기록을 신뢰할 근거를 제공하지 않는다. 신뢰할 수 있는 기록을 사용하고, real replay라도 독립적인 새 모델 실행이나 일반화 성능 측정으로 보고하지 않는다.

합성 fixture를 명시적으로 갱신할 때만 다음을 실행한다. 기존 `tests/fixtures/synthetic_success.jsonl`을 덮어쓰며 외부 로그에는 접근하지 않는다.

```bash
.venv/bin/python scripts/make_synthetic_replay.py
```
