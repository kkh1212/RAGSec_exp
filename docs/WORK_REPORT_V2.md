# 독립 rag_defense 보완 작업 보고

작업 범위: 독립 rag_defense 개발 작업공간만 사용했다. 다른 RAG 프로젝트를 찾거나 읽거나 연결하지 않았다. 이 구현 단계에서는 Git 상태, 공용 설정, 시스템 Python, 기존 프로세스를 변경하지 않았다. agent/병렬 실행/서버/GPU 설치/모델 다운로드/live API 호출은 하지 않았다.

이 문서는 구현 당시의 기록이다. `runs/` 원시 결과와 로컬 환경은 GitHub 배포에 포함하지 않는다. 새 서버에서의 재현 명령은 [SERVER_INTEGRATION.md](SERVER_INTEGRATION.md)를 따른다.

## 완료 상태

| 구분 | 결과 |
| --- | --- |
| 코드·설정·데이터 검수 도구·CLI·문서 구현 | 완료 |
| 합성 회귀 검사 | 완료: 191 passed, 0 failed |
| 독립 mock 데모 | 완료: v2 17개 예상 결과 검사, v1 11개 실행 |
| 실제 데이터 학습 | 미완료: 복사된 실제 로그·human_verified 라벨 없음 (NEEDS_LABELS) |
| 실제 관측기 검증 | 미완료: 실제 encoder/classifier 없음 (UNCONFIGURED) |
| 실제 RAG 연결 | 미완료: Generator/Verifier 미연결 |
| Jamming Instruction Injection / Jamming Oracle Generated / Jamming BBO / MutedRAG / TabooRAG 실측 | 미실행: REAL_ATTACK_EVALUATION_NOT_RUN |

## 핵심 변경

문서 슬롯을 유지하는 마스킹과 실제 슬롯 삭제를 독립 후보로 시험한다. 전체 문서 시험과 독립적으로 제목·본문의 연속 구간을 시험하고, 단일 경로로 검증 가능한 후보가 없으면 두 문서 조합을 시험한다. 원본을 불변으로 보관하고 모든 시험은 원본에서 시작한다.

후보의 성공 operation과 원문 위치·hash·응답 상태·생성 설정 fingerprint를 보존한다. DROP_DOCUMENT를 구간 삭제로 바꾸는 경우 실제 입력을 다시 시험한다. 근거 검증 실패 후 남은 경로를 계속 탐색하며, 검증 예산이 없으면 최초 거부를 유지한다. 전체 예산 48, 부분 12, 조합 20, 복원 8, 검증 최대 2, 최종 생성 예약 4회를 적용한다. 미탐색 단계·범위·개수와 종료 사유를 남긴다.

응답 관측기는 query/response만 받는다. import/review/validate/train/evaluate를 연결했고, 실제 human_verified 개발 라벨만 정상 학습에 사용한다. 고정 encoder + LR 특징, 그룹 분할, 근접 중복 점검, validation 임계값, 봉인 test, 긴 입력 UNCERTAIN, 클래스별 지표와 분모, 준비 상태를 구현했다. 기본값으로 합성 모델을 등록하지 않는다.

CallableVerifier를 유지하고 FinalLLMVerifier를 추가했다. 명시적 설정·기본 live 비활성·최종 후보 최대 2회·엄격 JSON·남은 한 연속 원문 구간의 인용 검사를 적용한다. 실제 검증한 fresh 응답을 그대로 반환한다. 공급자 구조화 차단과 일반 API 오류를 구분하며 차단/인증/한도 오류 뒤에 자동 재제출하거나 모델·키·endpoint를 교체하지 않는다.

## 실제 실행 결과

- 수정 전: `runs/baseline-v2.txt` / XML — 기존 **102 passed, 0 failed**.
- 수정 전 실패 재현: `runs/v2-reproduction.txt` / XML — 새 핵심 3개 테스트가 예상대로 실패.
- 최종 전체: `runs/v2-tests.txt` / XML — **191 passed, 0 failed**, 2.73초.
- v2 mock: `runs/demo-v2.jsonl`, `runs/demo-v2-validation.json` — **17개 예상 결과 모두 일치**. 복구 성공뿐 아니라 오답 탈락/원본 불안정/정상 무변경을 포함한다.
- v1 mock: `runs/demo-v1.jsonl` — 기존 11개 순차 실행.
- replay: `runs/replay-v1.jsonl` — 합성 기록의 PATCH_APPLIED, 추가 조회 10회.
- 예제: `runs/example-mock-v2.txt`, `runs/example-integration-v2.txt` — 두 mock 예제 실행.
- 준비 상태: `runs/doctor-v2.json`, `runs/labels-readiness.json`, `runs/real-evaluation-readiness.json`.
- 설치 이력: `runs/cpu-test-dependencies.txt`, `runs/development-requirements-v2.txt`. 프로젝트 venv에 CPU NumPy/scikit-learn만 추가했다. torch/sentence-transformers/가중치는 없다.

기존 의미 있는 assertion은 삭제하지 않았다. 정확한 v1 호출 순서·삭제 대조·replay를 검증하는 테스트에 v1 설정을 명시했고 v2는 새 회귀로 검사했다. 실패/중간 결과도 보존했다.

### 동일 예산 합성 비교

두 설정 모두 요청 전체 48회 상한이다. 아래는 **소프트웨어 조건**이며 실제 공격 방어율이 아니다. 상세: `runs/v1-v2-synthetic-comparison.json`.

| 합성 조건 | v1 상태 / 생성 시도 | v2 상태 / 생성 시도 |
| --- | --- | --- |
| 삭제만 복구 | NO_REPAIR_FOUND / 3 | PATCH_APPLIED / 8 |
| 근거를 남기는 부분 변경 | NO_REPAIR_FOUND / 2 | PATCH_APPLIED / 11 |
| 두 문서 동시 변경 | NO_REPAIR_FOUND / 4 | PATCH_APPLIED / 18 |
| 구두점 없는 원인 블록 | NO_REPAIR_FOUND / 3 | PATCH_APPLIED / 16 |
| 오답 ANSWER | NO_VERIFIED_REPAIR / 7 | NO_VERIFIED_REPAIR / 12 |
| 정상 주의 문구 | NO_REPAIR_NEEDED / 0 | NO_REPAIR_NEEDED / 0 |

## 변경 파일

### 엔진·계약·adapter

`src/rag_defense/{__init__,types,config,patch,budget,probe,observer,search,engine,verify,report,cli}.py`

`src/rag_defense/adapters/callable.py`

새 파일: `src/rag_defense/adapters/{execution,final_llm,synthetic_v2}.py`, `src/rag_defense/evaluation.py`.

### ML·데이터

`src/rag_defense/optional_ml/{common,train,observer}.py`

새 파일: `src/rag_defense/optional_ml/{data,metrics}.py`, `data/imported/README.md`, `data/templates/{observer-import.schema.json,observer-review.csv,evaluation-record.schema.json}`.

### 설정·설치·테스트

`pyproject.toml`, `configs/defense.mock.yaml`, 새 `configs/{defense.v2.mock.yaml,defense.v1.equal-budget.yaml,observer.targets.json}`.

`scripts/make_synthetic_replay.py`, 새 `scripts/setup_observer_cpu.sh` (미실행).

기존 테스트 수정: `tests/{conftest,test_engine,test_budget_cache,test_optional_ml,test_replay_cli}.py`.

새 테스트: `tests/{test_search_v2,test_verifier_execution_v2,test_observer_data_v2,test_evaluation_v2}.py`.

### 문서

`README.md`, `docs/{CONTRACT,INTEGRATION,LIMITATIONS,OPTIONAL_ML,REPLAY,TESTS}.md`.

새 문서: `docs/{IMPLEMENTATION_AUDIT,READINESS,LABELING,OBSERVER_WORKFLOW,SEARCH_V2,WORK_REPORT_V2}.md`.

## 남은 한계와 다음 실행

실제 분류 성능·원인 식별 성능·정답 복구율은 미측정이다. classifier의 초기 목표는 연구에서 입증된 기준이 아니다. 인용 존재와 semantic verifier의 PASS는 gold correctness와 별개다. 다섯 공격이 두 문서 이하에 한정된다는 가정도 하지 않는다. 두 문서 부분 구간 곱집합·세 문서 이상·긴 입력 분할·확률 보정은 현재 범위 밖이다.

우선 사용자 준비가 필요한 것은 완결된 실제 개발 로그 복사본과 로컬 encoder다. 다음 명령부터 시작한다.

```bash
source scripts/env.sh
.venv/bin/python -m rag_defense.cli doctor
.venv/bin/python -m rag_defense.cli import-observer-data --input data/imported/observer-dev.jsonl --output data/review/observer.csv
.venv/bin/python -m rag_defense.cli review-labels --input data/review/observer.csv --output data/review/observer-reviewed.jsonl
.venv/bin/python -m rag_defense.cli validate-labels --input data/review/observer-reviewed.jsonl
```

실제 입력 파일은 아직 없다. 이후 [OBSERVER_WORKFLOW.md](OBSERVER_WORKFLOW.md)의 train/evaluate를 수행하고 [INTEGRATION.md](INTEGRATION.md)의 Generator/Verifier 계약을 연결한다. 별도 최종 평가 파일이 준비되면 교차 누수를 검수하고 소규모 평가를 수행한다. live 실행에는 이번 요청과 별개의 명시적 사용자 지시가 필요하다.
