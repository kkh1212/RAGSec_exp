# RAGSec_exp · rag_defense

검색 컨텍스트 때문에 정상 질문의 응답이 거부된 경우, **이번 요청에서 거부 해소에 도움이 되는 수정 후보**를 제한된 호출로 찾습니다. 원본 재현, 패치 응답의 fresh 재현, 최종 근거 검증을 통과한 **바로 그 응답**만 반환합니다. 문서가 악성이라는 판정이나 정답 보장이 아닙니다.

기존 패키지·callback·replay 계약을 유지하면서 v2 탐색과 관측기 데이터 도구를 추가했습니다. 현재 검증 범위는 **합성 소프트웨어 검사**입니다. 실제 관측기 학습, 실제 RAG 연결, 다섯 공격의 실측은 수행하지 않았습니다.

## 바로 실행

새 서버에서는 이 저장소를 독립 디렉터리에 clone하고 프로젝트 전용 가상환경을 준비합니다. PR을 검토하는 동안에는 작업 브랜치를 사용합니다.

```bash
git clone --branch feat/rag-defense-v2 https://github.com/kkh1212/RAGSec_exp.git
cd RAGSec_exp
bash scripts/setup.sh
```

설치가 끝난 뒤에는 저장소 루트에서 실행합니다.

```bash
source scripts/env.sh
.venv/bin/python -m rag_defense.cli doctor
.venv/bin/python -m rag_defense.cli demo --config configs/defense.v2.mock.yaml --output-dir runs/demo-v2
.venv/bin/python -m rag_defense.cli replay --input tests/fixtures/synthetic_success.jsonl --config configs/defense.mock.yaml
.venv/bin/python -m rag_defense.cli evaluate-records
.venv/bin/python -m pytest -q --junitxml=runs/v2-tests.xml
```

모든 명령은 순차 실행합니다. 기본 API 호출은 없으며 환경의 API 키도 읽지 않습니다. `env.sh`는 현재 shell에서 프로젝트 `.venv` 사용에 필요한 격리 설정, `.cache/`, `.tmp/`, 수치 스레드 1개, Hugging Face 오프라인 모드를 설정합니다. 다른 RAG 프로젝트·Git 상태·기존 프로세스·공용 설정은 건드리지 않습니다.

이미 구현된 실험 코드에 연결하는 절차는 [다른 서버 적용 안내](docs/SERVER_INTEGRATION.md)에 있습니다. 실제 RAG 코드와 데이터는 이 저장소에 포함하지 않습니다. 개발 작업에서 생성한 `runs/` 결과는 배포하지 않으며, 문서의 과거 결과 경로는 개발 기록을 가리킵니다. 새 서버에서는 위 명령으로 결과를 새로 생성합니다.

설정 파일 `.yaml`은 JSON 형식의 YAML 1.2 부분집합입니다. 들여쓰기형 YAML은 지원하지 않습니다. CLI는 live 설정으로 demo/replay/evaluate-records를 실행하지 않습니다.

## 현재 상태

| 항목 | 상태 |
| --- | --- |
| v2 탐색 엔진 | 구현, 합성 회귀 검사 |
| Generator | mock/replay 실행 가능, 실제 callback 미연결 |
| 응답 관측기 | 기본 `UNCONFIGURED`; 데이터는 `NEEDS_LABELS` |
| 관측기 학습·검증 도구 | import/review/validate/train/evaluate 구현, 실제 학습 산출물 없음 |
| 최종 검증기 | CallableVerifier, 선택적 FinalLLMVerifier 구현, 실제 연결 없음 |
| 실제 공격 평가 | `REAL_ATTACK_EVALUATION_NOT_RUN`, 미측정 값은 null |

`doctor`는 이 항목들과 live 허용 여부, 입력 파일 존재 여부, 실행 가능한 모드를 각각 출력합니다. callback 연결 상태와 모델의 검증 상태를 혼동하지 않습니다. 현재 실제 로그나 MiniLM 가중치는 없습니다. 테스트용 CPU NumPy/scikit-learn만 추가했고 torch/sentence-transformers와 모델은 설치·다운로드하지 않았습니다.

## v2 처리

1. `query_policy=ALLOW`, 최초 실행 `OK`, 관측된 거부인 요청만 탐색합니다. 초기 `UNCERTAIN`은 원본 fresh 재시도에서 거부가 확인되어야 합니다.
2. 원본 재시도만으로 답변이 나오면 근거 검증 후 `RETRY_RECOVERY`로 반환합니다.
3. 문서별 `MASK_DOCUMENT`와 `DROP_DOCUMENT`를 독립적으로 시험합니다. 삭제는 실제 컨텍스트 항목 제거입니다.
4. 검증 가능한 후보가 없으면 모든 문서의 제목·본문 구간을 원문 순서와 round-robin 일정으로 시험합니다. 실패한 큰 구간의 작은 하위 구간도 시험 대상입니다.
5. 필요하면 두 문서의 동시 마스킹·동시 삭제를 고정 순서로 시험합니다.
6. 성공한 변환을 유지하면서 원문을 복원해 수정량을 줄입니다. 문서 삭제를 슬롯 유지 구간 삭제로 바꿀 때에는 그 입력을 먼저 시험합니다.
7. 최종 원본 거부·패치 답변은 fresh 호출로 재현하고, 검증했던 패치 응답을 그대로 반환합니다. 검증 실패 후에는 남은 예산으로 부분/조합 탐색을 재개합니다.

부분 구간을 실제로 모두 시험한 것은 아닐 수 있습니다. `search_complete`, `unexplored`의 단계·좌표·개수, 실제 호출 수, `termination_reason`을 확인하세요. 문서 3개 이상의 동시 변경과 다중 문서 부분 구간 조합은 지원 범위 밖입니다. 다섯 공격이 언제나 두 문서 이하라는 가정은 하지 않습니다.

## 예산과 v1 비교

| 설정 | v2 기본 |
| --- | ---: |
| 요청 전체 추가 생성 시도 | 48 |
| 직접 부분 탐색 | 12 |
| 두 문서 조합 탐색 | 20 |
| 복원·표현 변환 시험 | 8 |
| 최종 근거 검증 | 최대 2 |
| 동시 변경 문서 | 최대 2 |
| 실행 동시성 | 1 |
| live | false |

단계 예산은 상한이며 합산 호출을 보장하지 않습니다. 전체 48회가 우선합니다. 탐색·복원·보조 변환 시험은 최종 생성 호출 **4회**를 남깁니다. 실패 요청과 설정된 재시도도 실제 시도에 포함합니다. 캐시 적중은 생성 호출이 아닙니다. 최종 fresh 호출은 캐시를 쓰지 않습니다.

`DefenseConfig()`와 기본 demo는 v2입니다. 기존 `configs/defense.mock.yaml`은 v1의 단일 문서 마스킹 발견·문장 복원·삭제 대조와 20회 예산을 보존합니다. 같은 예산 비교에는 `configs/defense.v1.equal-budget.yaml`을 사용합니다. v1의 엄격 삭제 대조 옵션은 v2에서 사용하지 않습니다. 삭제 후보 승인에 마스킹 성공을 요구하지 않습니다.

## 실제 관측기 준비 순서

1. 원래 RAG 작업이 끝난 뒤, 사용자가 개발 로그의 완결된 복사본을 `data/imported/`에 준비합니다.
2. 검수 파일을 생성합니다.
3. 질문과 실제 응답을 보고 응답 상태를 검수합니다. 공격 적용 여부로 라벨을 정하지 않습니다.
4. CPU 라이브러리와 프로젝트 안의 명시적 로컬 encoder를 준비합니다. 자동 모델 다운로드는 없습니다.
5. 그룹을 나누어 학습하고 validation에서 임계값을 선택합니다.
6. 봉인된 test 분할을 평가합니다. 기준과 표본 수를 충족하지 못하면 `TRAINED_PROVISIONAL`을 유지합니다.
7. Generator/Verifier를 명시적으로 연결한 뒤, 별도 최종 평가 데이터로 소규모 평가를 수행합니다.

```bash
.venv/bin/python -m rag_defense.cli import-observer-data --input data/imported/observer-dev.jsonl --output data/review/observer.csv
.venv/bin/python -m rag_defense.cli review-labels --input data/review/observer.csv --output data/review/observer-reviewed.jsonl
.venv/bin/python -m rag_defense.cli validate-labels --input data/review/observer-reviewed.jsonl
.venv/bin/python -m rag_defense.cli train-observer --input data/review/observer-reviewed.jsonl --embedding-model-dir models/all-MiniLM-L6-v2 --embedding-revision IMMUTABLE_REVISION --output-dir artifacts/observer-v2
.venv/bin/python -m rag_defense.cli evaluate-observer --input data/review/observer-reviewed.jsonl --artifact-dir artifacts/observer-v2 --embedding-model-dir models/all-MiniLM-L6-v2
.venv/bin/python -m rag_defense.cli doctor --labels data/review/observer-reviewed.jsonl --artifact-dir artifacts/observer-v2 --embedding-model-dir models/all-MiniLM-L6-v2
```

이 명령의 실제 입력 파일은 아직 없습니다. 빈 schema/CSV만 제공하며 가짜 실제 로그를 만들지 않습니다. 관측기는 query/response만 표현에 사용합니다. `UNCERTAIN`을 제외해 REFUSAL recall 분모를 줄이지 않습니다. LR score는 보정된 확률로 표시하지 않습니다.

## 문서

- [수정 전 코드·함수 점검](docs/IMPLEMENTATION_AUDIT.md)
- [현재 준비 상태와 미완료 항목](docs/READINESS.md)
- [라벨링 가이드](docs/LABELING.md), [학습·평가 절차](docs/OBSERVER_WORKFLOW.md)
- [v1 → v2 탐색](docs/SEARCH_V2.md), [계약](docs/CONTRACT.md)
- [실제 RAG 연결](docs/INTEGRATION.md), [replay](docs/REPLAY.md)
- [다른 서버 설치·연결·실험 기록](docs/SERVER_INTEGRATION.md)
- [평가 해석·지원 범위](docs/LIMITATIONS.md), [테스트 실행 기록](docs/TESTS.md)
