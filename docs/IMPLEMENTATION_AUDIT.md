# 수정 전 점검 (2026-09-15)

작업 경로와 AGENTS.md를 먼저 확인했다. 다른 프로젝트는 탐색하지 않았다.
기존 테스트: `runs/baseline-v2.txt`, `runs/baseline-v2.xml`: 102 passed.
`data/`와 `data/imported/`는 없었다. 실제 데이터 학습과 실제 공격 평가의 입력은 없다.

| 보고된 문제 | 파일·함수에서 확인한 상태 |
| --- | --- |
| 실제 응답 관측기 미설정 | `observer.UnconfiguredObserver`, `optional_ml.observer.MLObserver.__init__`: 기본 미구성. 명시적 로컬 모델과 산출물 필요 |
| 전체 문서 성공 후에만 부분 탐색 | `search.find_candidates`, `refine_candidate`, `engine._Session.search_and_validate`: 전체 마스킹 ANSWER 후보에만 복원 수행 |
| 삭제는 대조군으로만 사용 | `patch.deletion_patches`: 문자열 삭제일 뿐 슬롯 제거 아님. `engine` 최종 대조에서만 호출 |
| 두 문서 탐색 없음 | `config.DefenseConfig.__post_init__`, `probe.ProbeRunner.run`: 한 문서로 제한 |
| 공급자 차단 구분 부족 | `types.ExecutionStatus`, `probe.ProbeRunner.run`: API_ERROR/TIMEOUT 중심; 구조화 차단 adapter 없음 |
| 실제 준비 상태 불명확 | `cli.main` doctor는 관측기만 간략 표시. `verify.validate_citations`는 원문 연속 인용 검사는 구현됨. `CallableVerifier`는 사용자 callback이며 내장 의미 검증 모델이 아님 |
| ML 경로 일부 구현 | `optional_ml.common/train/observer`: 고정 encoder, query/response 특징, LR JSON 저장 있음. 2-way 그룹 분할만 존재. 검수 CLI, test holdout, validation 임계값 선택, tokenizer 초과 처리, 준비 상태 검증 필요 |

원본 불변성, label-free 입력, 고정 생성 fingerprint, fresh 대조, 연속 원문 인용 검사, 오프라인 mock/replay 계약은 유지한다.

## 보완 후 연결 위치

| 항목 | 실제 구현 |
| --- | --- |
| v2 독립 탐색과 재개 | `search.SearchPlan.candidates/_partial_schedule/refine_v2`, `engine._Session.search_v2/try_v2_candidate` |
| 실제 문서 제거 | `types.Operation`, `patch.full_document_patches/apply_patches/remaining_spans` |
| 예산·실패 운영 상태 | `budget.Budget`, `probe.ProbeRunner.run`, `adapters.execution.classify_execution` |
| 원문 근거 최종 adapter | 기존 `CallableVerifier`, 새 `adapters.final_llm.FinalLLMVerifier/parse_verdict`; 기존 `validate_citations` 재사용 |
| 데이터 준비·검수 | `optional_ml.data.import_data/review_labels/validate_records/split_records`, CLI |
| 관측기 학습·평가 | `optional_ml.train.train/evaluate`, `metrics.choose_threshold/assess`, `common.input_support`, `observer.inspect_artifact/MLObserver` |
| 외부 평가 격리 | `evaluation.load_evaluation_records/evaluate_records/evaluate_copied_file` |
| 준비 상태 | `cli.doctor`, `types.Readiness`, docs/READINESS.md |

기존 v1 함수는 비교 재현용으로 유지했다. 새 기본 설정은 v2이고, 기존 replay와 호출 수 검사는 명시적 v1 설정을 사용한다. 최초 102개 회귀와 신규 검사를 합한 최종 결과는 191 passed이다. 실제 입력 데이터가 없어 실제 학습·검증·RAG 연결·다섯 공격 실측은 미완료이며 그 상태를 doctor와 평가 보고서에 기록한다.
