# 준비 상태 (2026-09-15)

## 구분

| 구분 | 현재 결과 | 미완료 이유 |
| --- | --- | --- |
| 구현 | v2 탐색, 데이터 검수, ML 학습·평가, 검증 adapter, CLI 구현 | 아래 실전 준비와 별도 |
| 합성 검사 | 기존 v1 회귀 + 신규 v2 검사 실행 | 실제 공격 자료가 아님 |
| 실제 데이터 학습 | 미실행 / NEEDS_LABELS | 명시적으로 복사한 실제 로그·검수 라벨 없음 |
| 관측기 검증 | 미실행 / UNCONFIGURED | 실제 classifier와 로컬 encoder 없음 |
| 실제 RAG 연결 | 미연결 | 실제 Generator·Verifier를 사용자가 아직 공급하지 않음 |
| 다섯 공격 실측 | REAL_ATTACK_EVALUATION_NOT_RUN | 실제 평가 레코드·실행 연결 없음 |

기본 Generator/Observer/Verifier의 mock/replay는 실제 준비 완료가 아니다. CallableVerifier는 연결 계약이며 내장 의미 검증 모델이 아니다. FinalLLMVerifier는 실제 API에 연결할 수 있는 선택 adapter이나 이번 작업에서 호출하지 않았다.

## Observer 상태

- `UNCONFIGURED`: artifact를 공급하지 않음.
- `NEEDS_LABELS`: 검수·학습에 필요한 실제 라벨이 없음/부족함. 파일이 있다는 것만으로 준비 완료가 되지 않는다.
- `TRAINED_PROVISIONAL`: 학습 산출물은 있으나 test 미평가, 표본 부족, 또는 고정 목표 미달. 실전 repair의 기본 관측기로 사용하지 않는다.
- `VALIDATED`: 고정 목표와 최소 표본/그룹 수를 validation과 봉인된 test에서 충족한 산출물. 실환경 전체 성능을 보장하는 상태는 아니다.
- `INCOMPATIBLE_ARTIFACT`: schema, hash, 특징 구성, 차원, 클래스, 분할, encoder 연결 등에 불일치가 있음.

`doctor --artifact-dir ...`는 파일의 상태만 검사한다. `--embedding-model-dir ...`까지 주어야 실제 로컬 encoder 연결을 검사한다. 연결하지 않은 artifact의 검증 상태와 실행 가능한 관측기 상태를 각각 출력한다.

`configs/observer.targets.json`의 초기 엔지니어링 목표: REFUSAL recall ≥ .90, ANSWER precision ≥ .95, UNCERTAIN rate ≤ .20. validation과 test 각각 최소 100행, 각 학습 클래스 20행, 독립 질문 그룹 20개를 요구한다. 연구로 입증된 기준이 아니며 미달 시 몰래 낮추지 않는다. 작은 표본의 우연한 통과를 VALIDATED로 표시하지 않는다.

## 설치 상태

기존 `.venv`에 CPU NumPy/scikit-learn과 그 수치 연산 의존성을 추가했다. 정확한 목록은 `runs/development-requirements-v2.txt`에 저장한다. torch, sentence-transformers, GPU/CUDA, 모델 가중치는 설치·다운로드하지 않았다. CPU LR fitting 검사는 **합성 encoder/합성 라벨의 소프트웨어 검사**이며 산출물은 pytest 임시 경로에서만 만들어진다. 실제 모델로 등록하지 않는다.
