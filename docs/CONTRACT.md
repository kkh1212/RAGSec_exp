# 실행·반환 계약

DefenseRequest는 request_id, query, documents, initial_response, generation_fingerprint, query_policy만 갖는다. Document는 doc_id, title, text, rank, retrieval_score만 갖는다. 공격명·원인 위치·정답·clean 정답·qrels·fixture 숨은 원인은 입력 계약에 없다.

문서는 불변이며 각 호출은 원본에서 patch를 적용한다. title/text 위치는 Python Unicode 문자 기준이며 byte/token 위치가 아니다. DROP_DOCUMENT의 field=document 범위는 제목+본문의 전체 변경 문자 수를 나타내는 구조적 표기다. 실제 근거 좌표는 title/text별로만 정의한다. field 원문 hash, request fingerprint, 생성 설정 fingerprint를 확인한다. 중첩 패치나 문서 삭제와 같은 문서 구간 변경의 중첩은 거부한다.

## 성공과 실패

- NO_REPAIR_NEEDED: 최초 ANSWER 유지.
- OUT_OF_SCOPE: DENY/UNKNOWN/OTHER 또는 명시적 외부 실행 비허용.
- OBSERVER_NOT_CONFIGURED: 미학습/미검증 관측기로 복구하지 않음.
- RETRY_RECOVERY: 원본 fresh 재시도 답변의 최종 근거 검증 통과. 문서 원인 attribution 없음.
- PATCH_APPLIED: 원본 거부·패치 답변의 fresh 재현과 근거 검증 통과. 바로 그 검증 응답을 반환.
- NO_REPAIR_FOUND / NO_VERIFIED_REPAIR: 후보 없음 또는 승인 실패. 최초 응답 유지.
- BUDGET_EXHAUSTED: 필요한 탐색/근거 검증을 예산 내 끝내지 못함. 최초 응답 유지.
- UNSTABLE_BASELINE: 원본 거부가 재현되지 않음.
- EXECUTION_ERROR: 관측/설정/공급자 오류 또는 차단. 최초 응답 유지.

성공하지 않으면 patches는 비어 있고 final_response는 최초 객체다. 관측된 후보는 candidate_patches/best_candidate_patches/candidate_details/trace에 따로 남는다. 최선의 작은 탐색 후보와 최종 승인 후보는 다를 수 있다.

`grounding_passed`는 runtime 검사 결과다. `gold_correctness`는 엔진에서 항상 None이고 외부 평가에서 별도로 측정해야 한다. REFUSAL을 attack=True로 바꾸는 필드나 기능은 없다. transformation_sensitive는 변환별 반응의 차이이며 악성 확신도가 아니다.

## 캐시·예산

생성 키는 query, 실제 수정 문서의 내용·순서·필드, generation fingerprint, generator version, fresh, replicate_id를 포함한다. 기본 캐시는 요청 단위다. fresh는 항상 캐시를 우회한다. 입력이 같아도 fresh replicate는 별도 요청이다.

v2는 전체 48회, partial 12, pair 20, refinement 8, grounding 최대 2, 동시 변경 문서 2, concurrency=1이다. 단계 상한보다 전체 상한이 우선이다. 탐색과 복원/변환 대조는 최종 생성 4회를 예약한다. 최종 후보당 원본/패치 fresh 호출을 사용한다. 오류·설정된 engine retry도 전체 실제 시도에 포함한다. 외부 adapter retry는 허용하지 않는다.

준비 상태와 미탐색 범위는 별도 결과다. search_complete=false는 실패의 악성 원인 부재를 뜻하지 않는다. 작은 하위 구간의 성공 가능성을 큰 구간 실패만으로 배제하지 않는다. 한 요청의 패치를 다음 질문이나 공유 문서에 누적하지 않는다.

## 보고서

`runs/` JSON에는 query/document/response/citation 원문 대신 해시를 저장한다. 학습 검수 파일은 의도적으로 원문을 포함하므로 run 보고서와 구분한다. 설정·operation·원문 좌표·사용 호출 수·미탐색 범위·종료 사유를 포함한다. 명시적 adapter 입력에 비밀정보가 있는 경우 문서 ID/버전에도 넣지 않는다.

추가 generation/grounding 호출, 토큰, callback 소요 시간을 분리한다. FinalLLMVerifier가 토큰 사용량을 주지 않으면 grounding 토큰은 None이다. mock의 외부 토큰은 0이며 실제 공격 성능 측정 값이 아니다. 실제 Generator는 TokenUsage를 정확하게 공급해야 한다.
