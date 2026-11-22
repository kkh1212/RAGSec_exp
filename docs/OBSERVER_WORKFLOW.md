# 관측기 학습·검증

## 구성과 설치

기존 `optional_ml`을 확장했다. 권장 encoder는 `sentence-transformers/all-MiniLM-L6-v2`이며 CPU에서 고정한다. MiniLM은 거부 분류기가 아니다. 별도의 검수 라벨로 multinomial LogisticRegression을 학습한다.

실제 라벨과 로컬 모델이 준비된 뒤에만 선택 설치를 수행한다. `scripts/setup_observer_cpu.sh`는 프로젝트 `.venv`에 CPU torch를 먼저 설치한 뒤 optional ML 의존성을 설치한다. 이번 작업에서는 실행하지 않았다. 모델을 다운로드하는 명령은 제공하지 않는다. 사용자가 준비한 encoder 완결본을 프로젝트 `models/`에 복사하고 immutable revision을 명시해야 한다. HF offline/local_files_only/trust_remote_code=false를 유지한다.

질문과 응답을 각각 정규화 임베딩한 후 `[q, r, abs(q-r), q*r]`로 결합한다. 원문 텍스트 정규화는 하지 않는다. 특징 version·전처리·순서·차원·encoder ID/revision/tree hash를 저장하고 로딩 시 확인한다. 공격 문구 사전, 거부 템플릿 유사도, gold label 특징은 없다.

## CLI

README의 import → review → validate → train → evaluate → doctor 명령을 순서대로 사용한다. `train-observer`에는 입력, 프로젝트 로컬 모델 디렉터리, revision, 새 output 디렉터리가 필요하다. 기존 output을 덮어쓰지 않는다. 기본 목표는 `configs/observer.targets.json`이며 다른 사전 목표 파일을 `--targets`로 명시할 수 있다.

`python -m rag_defense.optional_ml.train ...`의 기존 module entry도 유지하지만 내부에서는 같은 train-observer CLI를 사용한다. 학습 입력은 새로운 검수 schema다. 이전 human_reviewed/data_kind 전용 레코드는 새 sample_id/execution_status/label_status/purpose 필드로 명시적으로 변환·검수한다. 기존 `load_labels`/`split_by_query` Python 유틸리티는 호환을 위해 유지한다.

## 분할과 표본 부족

고정 seed=13, 질문/근접중복 연결 그룹 단위 train/validation/test=60/20/20이다. 질문 ID가 둘 이상의 split에 나타나지 않는다. 사용자 지정 split도 검증하며 일부 행만 split을 지정하는 모호한 형식은 거부한다. 데이터 순서·hash와 각 split의 sample/query ID 목록을 저장한다.

전체 그룹이 3개 미만이거나 길이 지원 가능한 train에서 세 클래스가 없으면 학습을 중단한다. validation/test 클래스·분모가 작으면 결과를 출력하되 VALIDATED로 승격하지 않는다. 근접 중복 검사는 보수적인 문자열 휴리스틱이므로 모든 의미상 중복을 찾는 보장은 없다. 최종 방어 평가 자료의 파일·질문 그룹은 개발자도 별도로 보관해야 한다.

## 길이와 확신도

encoder tokenizer로 query/response 각각의 특수 토큰 포함 길이를 **encode 이전**에 확인한다. 설정 길이(기본 256)를 넘으면 조용히 자르지 않는다. 추론은 `UNCERTAIN`과 query/response_token_limit_exceeded를 반환한다. 학습에서는 초과 기록을 제외한 수를 저장하고, validation/test에서는 UNCERTAIN으로 남겨 초과율과 전체 분모를 보고한다. 별도의 긴 입력 분할 처리는 이번 범위에 없다.

validation에서 고정된 threshold grid를 시험한다. 미리 정한 semantic 목표를 만족하는 후보 중 coverage가 높은 것을 선택하고, 없으면 기준 미달 상태를 유지한다. `--threshold`는 validation grid에 후보를 추가하는 옵션이며 test를 이용한 override가 아니다. 점수는 **보정되지 않은 LR score**다. 복잡한 확률 보정은 구현 범위에 넣지 않았다.

## 산출물과 test 봉인

- `classifier.json`: class 순서, 계수, intercept. pickle/joblib 실행 파일은 로드하지 않는다.
- `manifest.json`: encoder ID/revision/tree hash, 특징·전처리, class mapping, threshold/grid/선택 split, seed, dataset/file hash, split ID, 라이브러리 버전, validation confusion matrix·클래스별 precision/recall/F1, UNCERTAIN/coverage, 길이 초과율, 표본 수, 목표 통과 여부.
- train 후 상태는 항상 TRAINED_PROVISIONAL. test 텍스트는 이 단계의 encoder 호출에도 들어가지 않는다.
- evaluate-observer가 정확히 같은 데이터 hash의 봉인된 test만 평가한다. threshold 조정·refit 옵션은 없다. 같은 artifact의 반복 평가 명령은 이미 저장한 test 보고서를 읽는다.
- 고정 validation/test 기준과 최소 표본 수를 모두 충족해야 VALIDATED다. `MLObserver(..., allow_provisional=True)`는 평가용이며, 엔진은 provisional 관측기로 repair하지 않는다.

REFUSAL recall에는 UNCERTAIN으로 판단된 실제 REFUSAL도 포함된다. 각 지표는 실제 numerator/denominator를 기록한다. 표본별 Wilson 95% 구간은 참고값이며 같은 질문 변형들의 상관관계를 반영한 보장 구간이 아니다. 독립 그룹 수와 최소 표본 게이트도 함께 제시한다.

현재 실제 라벨·encoder·classifier는 없다. CPU fitting/export/test-report 검사는 합성 테스트 encoder로만 수행했으며, 해당 artifact는 synthetic_test_only로 표시하고 실전 MLObserver가 거부한다.

별도의 최종 평가 파일과 개발 라벨의 교차 누수는 다음 명령으로 점검할 수 있다. final 파일의 공격/정답 metadata는 관측기 특징이나 threshold 선택에 들어가지 않는다.

```bash
.venv/bin/python -m rag_defense.cli validate-labels --input data/review/observer-reviewed.jsonl --final-evaluation-input data/imported/final-evaluation.jsonl
```

문자열 근접중복까지 묶어서 purpose 충돌을 보고한다. 교차 누수 검사가 최종 평가 내용을 이용한 학습/튜닝을 허용하는 것은 아니다.
