# 실제 RAG 연결 계약

다른 프로젝트를 자동 import·검색·패치하지 않는다. 사용자가 준비한 callback과 불변 요청만 받는다. 현재 실행 예제는 네트워크 없는 mock이다.

## Generator

```python
from rag_defense import DefenseEngine, DefenseConfig
from rag_defense.adapters.callable import CallableGenerator, CallableVerifier
from rag_defense.optional_ml.observer import MLObserver

# generate_once와 verify_final은 사용자가 준비할 함수다.
# 이 문서는 실제 서비스를 실행하지 않는다.
generator = CallableGenerator(generate_once, fingerprint=fixed_fingerprint)
observer = MLObserver("artifacts/observer-v2", embedding_model_dir="models/all-MiniLM-L6-v2")
verifier = CallableVerifier(verify_final)
engine = DefenseEngine(generator=generator, observer=observer, verifier=verifier,
                       config=DefenseConfig(allow_live=False))
```

`generate_once(query, documents, *, fresh, replicate_id)`는 GenerationResult를 반환한다. documents는 실제 생성에 사용할 문서 튜플이다. DROP_DOCUMENT는 항목이 사라지고 나머지 상대 순서는 유지된다. 빈 문자열 슬롯과 같다고 렌더링하지 않는다. MASK_DOCUMENT는 슬롯을 유지하고 제목·본문을 가린다. 검색 인덱스/공유 원본은 수정하지 않고 재검색·보충도 하지 않는다.

모델, endpoint, 프롬프트, 템플릿, 생성 파라미터와 렌더러 버전을 고정하고 `make_generation_fingerprint`로 확인한다. fingerprint에는 비밀키를 넣지 않는다. 각 adapter 호출은 실제 외부 요청 최대 1회여야 하며 SDK 자동 retry를 끈다. 생성 토큰 사용량은 TokenUsage에 실제 값을 넣는다. provider_attempts>1은 거부한다. engine은 호출 횟수와 callback 소요 시간을 직접 기록한다.

`query_policy`는 신뢰할 수 있는 외부 판단이다. 컨텍스트 문구로 ALLOW로 바꾸지 않는다. DENY/UNKNOWN은 최초 응답을 유지한다. Observer는 정확히 `observe(query, response)`만 받는다. label/attack/gold/qrels를 허용하는 필드는 없다.

## CallableVerifier

`verify_final(query, response, evidence)`는 VerificationResult를 반환한다. evidence는 수정 후 남은 원문의 **연속 구간**인 RemainingSpan 튜플이다. doc_id/field/start_char/end_char/text만 있으며 제거된 텍스트, 마스크 토큰, gold, clean 답변, 공격명·위치 라벨은 전달하지 않는다.

PASS에는 addresses_query=True, supported=True, 실제 남은 한 구간 안의 Citation이 필요하다. code의 인용 검사는 의미적 정확성을 대신하지 않는다. CallableVerifier의 사용자는 질문 부합성·근거 지지·불확실 여부를 판단하는 실제 함수를 제공해야 한다. mock 함수는 mock으로 표시한다.

## 선택 FinalLLMVerifier

```python
from rag_defense.adapters.final_llm import FinalLLMConfig, FinalLLMVerifier

verifier = FinalLLMVerifier(FinalLLMConfig(
    model=explicit_model,
    endpoint=explicit_https_chat_completions_endpoint,
    api_key=explicit_key,
    allow_live=False,
    max_calls=2,
))
```

명시적 HTTPS chat-completions JSON endpoint용 선택 adapter다. API key는 환경에서 자동 탐색하지 않는다. 고정 system 지시와 별도 user JSON 데이터, 고정 temperature=0, JSON 출력 검증을 사용한다. 지원하지 않는 endpoint는 CallableVerifier로 연결한다. API의 JSON mode 호환성은 실제로 시험하지 않았다.

live 연결을 실행하려면 사용자의 별도 명시적 지시 후 engine과 verifier 양쪽의 allow_live를 켜야 한다. 기본 CLI에는 live 실행 경로가 없다. 이번 작업에서는 모든 전송 테스트를 가짜 transport로 실행했다.

각 request의 최종 후보에 최대 2회만 호출한다. 후보 선택·탐색·재작성에 관여하지 않는다. JSON 키, 타입, 중복 키, 상태, citation 좌표와 원문 일치를 검사하며 fenced JSON도 허용하지 않는다. 삭제된 부분을 건너뛴 결합 인용은 탈락한다. 지원하지 않는 긴 입력은 UNCERTAIN이다. 인증 오류·한도·공급자 차단 후 retry, 모델/키/endpoint 교체가 없다. redirect도 따르지 않는다. 고정 지시/데이터 분리만으로 완전한 prompt injection 내성이 생기지는 않는다.

## 실행 상태

권장 상태: OK, PROVIDER_BLOCK, RATE_LIMIT, AUTH_ERROR, TIMEOUT, INPUT_TOO_LONG, INCOMPLETE, OTHER_API_ERROR. 과거 API_ERROR/EMPTY_RESPONSE/OUTPUT_TRUNCATED와 replay 전용 상태는 호환을 위해 유지한다. `adapters.execution.classify_execution`은 구조화 error_code/finish_reason/차단 표시를 받아 분류한다. provider별 코드 계약을 확인해 정규화하고 사용한다.

HTTP 400 전체, 빈 출력, 텍스트 거부를 PROVIDER_BLOCK으로 추정하지 않는다. 정상 반환된 거부 문장은 execution_status=OK 상태에서 observer가 REFUSAL로 분류한다. provider 차단과 AUTH_ERROR/RATE_LIMIT이 관측되면 요청의 추가 탐색을 중단한다. 안전장치 비활성화, 우회 프롬프트, endpoint/키 변경, 같은 차단 요청 반복 제출은 지원하지 않는다.

## 외부 평가

`data/templates/evaluation-record.schema.json`에서 request와 evaluation은 분리된다. defense에는 request만 전달한다. 기존 필드 documents/initial_response는 요구되는 contexts/response의 명칭을 유지한 것이다. request_id와 고정 generation fingerprint는 동작 추적용이며 평가 라벨이 아니다.

`evaluate_records(records, engine_factory, correctness_evaluator=...)`는 평가용 API다. factory는 인자 없이 engine을 만들며 label을 받지 않는다. reference answer를 받는 correctness_evaluator는 외부 평가에서만 사용한다. 기본 scorer는 없다. callback의 외부 API 사용도 사용자가 명시적으로 허용해야 한다.

CLI `evaluate-records --input data/imported/final-evaluation.jsonl`은 complete replay_file이 있는 복사 레코드를 오프라인 재생한다. 수정 입력/fresh 반복 기록이 빠지면 실제 응답을 추정하지 않는다. replay 결과는 SOFTWARE_CHECK_ONLY이며 실제 새 공격 평가로 집계하지 않는다. 복사본이나 연결이 없으면 REAL_ATTACK_EVALUATION_NOT_RUN과 null 지표를 기록한다.
