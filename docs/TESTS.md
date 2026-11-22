# 실행한 검사 (2026-09-15)

모든 실행은 이 프로젝트 안에서 `.venv/bin/python`, `scripts/env.sh`, 수치 스레드 1개, 순차 실행으로 수행했다. pytest는 socket 연결을 막는 autouse fixture를 사용한다. API 호출·모델 다운로드·GPU 설치·다른 RAG 프로젝트 접근은 없다.

## 결과

| 단계 | 실제 명령 | 결과 파일 | 결과 |
| --- | --- | --- | --- |
| 수정 전 기존 테스트 | `python -m pytest -q --junitxml=runs/baseline-v2.xml` | `runs/baseline-v2.txt`, XML | 102 passed, 0 failed |
| 탐색 공백 재현 | `python -m pytest -q tests/test_search_v2.py --junitxml=runs/v2-reproduction.xml` | `runs/v2-reproduction.txt`, XML | 예상한 3 failed: 삭제 전용/부분 근거/두 문서 |
| 최종 전체 회귀 | `python -m pytest -q --junitxml=runs/v2-tests.xml` | `runs/v2-tests.txt`, XML | **191 passed, 0 failed**, 2.73초 |
| doctor | `python -m rag_defense.cli doctor` | `runs/doctor-v2.json` | v2 준비, 실제 adapter 미연결, NEEDS_LABELS, live=false |
| v2 demo | `python -m rag_defense.cli demo --config configs/defense.v2.mock.yaml --output-dir runs/demo-v2` | `runs/demo-v2.jsonl`, 개별 JSON | 17개 합성 시나리오 실행 |
| v1 demo | `python -m rag_defense.cli demo --config configs/defense.mock.yaml --output-dir runs/demo-v1` | `runs/demo-v1.jsonl`, 개별 JSON | 기존 11개 합성 시나리오 실행 |
| 기존 replay | `python -m rag_defense.cli replay --input tests/fixtures/synthetic_success.jsonl --config configs/defense.mock.yaml` | `runs/replay-v1.jsonl` | PATCH_APPLIED, 기록된 추가 생성 10회 |
| 외부 평가 준비 | `python -m rag_defense.cli evaluate-records` | `runs/real-evaluation-readiness.json` | REAL_ATTACK_EVALUATION_NOT_RUN, 지표 null |
| 예제 | `python examples/run_mock.py`, `python examples/integration_example.py` | `runs/example-*-v2.txt` | 두 예제 PATCH_APPLIED, mock |

표의 python은 실제로 `.venv/bin/python`으로 실행했다. 데이터 CLI import/review/validate/train/evaluate와 실패 상태는 pytest에서도 직접 호출한다. CPU LR fitting/export/봉인 test-report 경로는 합성 encoder·합성 라벨만으로 검사했다. 실제 관측기 학습 성능 검증이 아니다.

## 필수 회귀 대응

| 번호 | 요구 조건 | 테스트 파일 / 주요 검사 |
| --- | --- | --- |
| 1, 15 | 삭제만 성공, 마스킹 필수 성공 금지 | test_search_v2: drop_only_recovery, drop_only_refinement |
| 2, 16 | 전체 실패 뒤 부분 복구 | test_search_v2: partial_search_survives_failed_whole_document_probes |
| 3 | 두 문서 조합 | test_search_v2: pair_search, two_document_drop_only |
| 4 | 명시적 지시 없는 원인 | test_search_v2의 opaque 숫자 원인 fixture |
| 5 | 문장부호 없는 문자열 | test_search_v2: unpunctuated_cause |
| 6 | DENY/UNKNOWN 복구 금지 | test_search_v2: legitimate_disallowed_query; 기존 test_engine |
| 7 | 근거 부족 거부 유지 | test_search_v2: insufficient_evidence_refusal |
| 8 | 오답 ANSWER 검증 탈락 | test_search_v2: wrong_nonrefusal_answer; test_evaluation_v2 |
| 9 | 정상 정책·주의 문구 무변경 | test_search_v2: policy_note |
| 10 | 단순 재시도 별도 상태 | test_search_v2: retry_recovery; 기존 test_engine |
| 11 | 공급자 차단/일반 오류 | test_verifier_execution_v2: structured_status_mapping, operational_stop |
| 12 | 미학습/저확신/장문 | test_optional_ml, test_observer_data_v2: long_input, low_confidence, incompatible_artifact |
| 13 | 그룹/중복 데이터 누수 | test_observer_data_v2: three_way_group_split, duplicate_questions, explicit_separate_final_evaluation_file |
| 14 | 문서/질문 간 누적 금지 | test_search_v2: every_probe_starts_from_original; test_patch |
| 17 | 검증 실패 후 다음 탐색 | test_search_v2: failed_grounding_resumes_independent_partial_search |
| 18 | 호출 상한/미탐색 범위 | test_search_v2: v2_budget, zero_budget, large_pair_scope; test_budget_cache |
| 19 | fresh 캐시 대체 금지 | test_search_v2: four_generation_calls_reserved; test_budget_cache, test_replay_cli |
| 20 | 정답·공격 라벨 입력 격리 | test_engine: request/callback 계약; test_evaluation_v2 |
| 21 | fixture 숨은 원인 접근 금지 | test_search_v2: fixture_hidden_cause_is_never_read_by_engine |
| 22 | 검증한 응답 그대로 반환 | test_search_v2, test_verifier_execution_v2: final_llm_adapter_is_connected_at_final_stage; 기존 객체 동일성 검사 |

추가로 실제 슬롯 제거·빈 문서 제거·원문 인용의 연속성·JSON 중복 키/타입·검증 호출 최대 2회·인증/한도 오류 무재시도·명시적 로컬 경로·합성 학습 거부·UNCERTAIN 분모·작은 표본 provisional·test 임계값 불변·토큰/시간 보고를 검사한다.

기존 assertion은 삭제하지 않았다. 이전 v1의 정확한 호출 수, phase, 삭제 대조, replay 순서를 검증하는 테스트에는 v1 설정을 명시했다. v2는 별도 새 회귀로 검사한다. 중간 실패 기록(`runs/intermediate-v2.*`)도 보존했다.

## 해석

v2 demo의 PATCH_APPLIED는 합성 조건에서 승인 경로가 동작했다는 뜻이다. wrong_answer는 NO_VERIFIED_REPAIR, 근거 검증 실패/불확실은 예산 종료, 불안정 원본은 UNSTABLE_BASELINE, 정상 응답은 무변경이라는 **예상 결과 자체**를 검사한다. 모든 시나리오에서 답변 복구가 성공해야 테스트가 통과하는 것은 아니다.

Jamming Instruction Injection/Jamming Oracle Generated/Jamming BBO/MutedRAG/TabooRAG 실제 방어율은 측정하지 않았다. mock에 공격명을 붙여 다섯 공격을 검증했다고 보고하지 않는다.
