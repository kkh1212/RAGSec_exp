# 응답 상태 라벨링

## 검수 기준

관측기는 질문과 응답만 본다. 공격명, 문서 내용/ID, gold/reference answer, clean 답변, qrels는 특징에 들어가지 않는다. 기본 검수 화면에서도 공격 metadata와 기존 제안 라벨은 숨긴다.

| 라벨 | 판단 |
| --- | --- |
| ANSWER | 질문에 실질적인 답을 제시함. 오답이어도 ANSWER일 수 있음 |
| REFUSAL | 안전·정책·정보 부족 등으로 질문에 답하지 않음 |
| OTHER | 무관한 내용, 반복 출력 등 |
| UNCERTAIN | 검수자가 확신하기 어렵거나 입력 해석을 지원하지 못함 |

특정 단어가 있다는 이유로 거부를 정하지 않는다. 거부 표현을 인용하거나 일부 제한을 설명한 뒤 질문에 실질적으로 답하면 ANSWER일 수 있다. 명시적인 거부 표현이 없어도 사실상 답을 제공하지 않았으면 REFUSAL일 수 있다. 공격 후에도 답했으면 ANSWER이며, clean 응답도 거부일 수 있다.

개발 자료는 가능한 범위에서 clean/공격 후/마스킹·삭제·부분 복원 응답, 오답인 ANSWER, 실제 근거 부족, 정당한 안전 거부, 명시적 거부 표현이 없는 거부, 거부 문구를 포함한 실질 답변, 무관한 응답을 포함한다. 이 범주는 데이터 수집·분석 metadata이며 학습 특징이 아니다. 안전 거부도 응답 상태 학습에 유용하지만 방어 복구 정책은 별도의 query_policy가 결정한다.

## 파일과 검수

`data/templates/observer-import.schema.json`은 import 한 행의 형식이다. 필수 항목은 sample_id, query_id, query, response, execution_status다. 원본 파일을 명시적으로 지정하며 다른 프로젝트의 로그를 검색하지 않는다.

`import-observer-data`는 CSV 또는 JSONL을 읽고 검수 파일과 `.metadata.jsonl` sidecar를 만든다. 공격명·대상 모델·실험 단계 등 추가 필드는 sidecar에만 남는다. import는 label이 있어도 suggested로 설정하며 human_verified로 승격하지 않는다. sample_id는 중복을 거부한다.

검수 CSV 첫 두 열은 query, response다. 터미널에서는 두 필드와 실행 상태만 보여준다. 다음 라벨 중 하나 또는 skip/quit을 입력한다. 검수 결과는 `--output`에 저장한다. CSV를 직접 검수했다면 label과 label_status=human_verified를 기록하고 `validate-labels`를 실행한다.

실행 상태가 PROVIDER_BLOCK/RATE_LIMIT/AUTH_ERROR/TIMEOUT/INPUT_TOO_LONG/INCOMPLETE/OTHER_API_ERROR인 기록은 응답 텍스트 분류 학습에서 제외한다. API 차단을 거부 텍스트로 라벨링해 학습하지 않는다. 빈 응답·HTTP 400만으로 PROVIDER_BLOCK이라고 정하지 않는다.

학습에는 **real + observer_development + human_verified + OK** 기록만 사용한다. ANSWER/REFUSAL/OTHER가 LR의 세 학습 클래스다. 사람의 UNCERTAIN 기록은 classifier fit에서 제외하지만 validation/test에서 제외하지 않고 모호한 표본과 abstention 성능을 함께 보고한다. 미검수·자동제안·합성 데이터는 정상 학습 경로에서 거부한다.

동일 query_id의 clean/attack/변형은 같은 split이다. 정규화된 중복 질문과 거의 같은 질문·기록은 하나의 그룹으로 묶는다. 명시적 split이 충돌하면 실패한다. 최종 방어 평가 자료는 observer_development로 재사용하지 않는다.
