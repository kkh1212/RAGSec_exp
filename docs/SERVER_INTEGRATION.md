# 다른 서버의 실험 환경에 적용하기

이 저장소는 방어 엔진과 연결 계약을 제공한다. 원래 RAG의 소스·로그·인덱스·API 키·가상환경은 포함하지 않는다. 현재 실제 관측기 학습과 다섯 공격의 실측은 미실행이다.

## 1. 독립 설치와 mock 검사

Python 3.10 이상, CPU, 프로젝트 디렉터리 쓰기 권한이 필요하다. Python 3.12에서 개발 테스트를 수행했다. 설치 명령은 CPU의 기본 테스트 의존성만 설치하며, 모델을 다운로드하거나 생성 API를 호출하지 않는다.

```bash
git clone --branch feat/rag-defense-v2 https://github.com/kkh1212/RAGSec_exp.git
cd RAGSec_exp
bash scripts/setup.sh
source scripts/env.sh
.venv/bin/python -m rag_defense.cli doctor
.venv/bin/python -m pytest -q --junitxml=runs/server-tests.xml
.venv/bin/python -m rag_defense.cli demo --config configs/defense.v2.mock.yaml --output-dir runs/server-demo
.venv/bin/python examples/integration_example.py
```

PR이 merge된 뒤에는 기본 브랜치로 clone할 수 있다. 실행할 revision은 `git rev-parse HEAD`로 기록한다. 기본 설치에는 NumPy/scikit-learn/torch/encoder가 없으므로 선택 CPU fitting 테스트는 skip될 수 있다. 그 소프트웨어 검사까지 실행하려면 프로젝트 `.venv`에서 `pip install -e '.[cpu-test]'`를 추가한다. 실제 관측기에는 별도의 검수 라벨과 로컬 encoder가 필요하다.

기존 실험 가상환경의 패키지를 자동으로 변경하지 않는다. 기본 제공 프로세스·서버·데몬도 없다. 실험 환경과 Python 의존성을 결합할지는 사용자가 별도로 정한다. 실험 코드가 방어 엔진을 같은 프로세스에서 import하는 경우에도 질문·문서·최초 응답은 아래 명시적 계약으로 전달한다.

## 2. 실험 코드에서 넘길 값

검색을 마치고 최초 생성이 끝난 지점에서 DefenseRequest를 만든다. 공격 레이블과 정답은 실험 기록 객체에만 둔다. engine은 추가 검색을 하지 않는다.

```python
from rag_defense import (
    DefenseConfig, DefenseEngine, DefenseRequest, Document,
    ExecutionStatus, GenerationResult, QueryPolicy, TokenUsage,
)
from rag_defense.adapters.callable import CallableGenerator, CallableVerifier
from rag_defense.fingerprint import make_generation_fingerprint
from rag_defense.optional_ml.observer import MLObserver

# 아래 값과 함수는 사용자가 준비한 실험 코드에서 명시적으로 공급한다.
fingerprint = make_generation_fingerprint(
    model=model_id,
    endpoint=endpoint_identity,  # 키나 비밀정보를 넣지 않는다.
    prompt=fixed_prompt_template,
    settings=fixed_generation_settings,
    renderer_version="experiment-renderer-v1",
)

def generate_once(query, documents, *, fresh, replicate_id):
    # 전달받은 documents를 그 순서대로 렌더링한다. 원래 retrieved_docs를
    # 다시 사용하면 마스킹/삭제 시험이 실제 생성에 적용되지 않는다.
    # 이 함수는 모델/프롬프트/생성 설정을 바꾸지 않고 외부 호출 최대 1회만 한다.
    output = experiment_generate(query, documents, settings=fixed_generation_settings)
    return GenerationResult(
        text=output.text,
        execution_status=output.execution_status,  # 구조화된 provider 상태를 정규화
        generation_fingerprint=fingerprint,
        usage=TokenUsage(input_tokens=output.input_tokens,
                         output_tokens=output.output_tokens, provider_attempts=1),
    )

request = DefenseRequest(
    request_id=request_id,
    query=query,
    documents=tuple(Document(doc_id=d.doc_id, title=d.title, text=d.text, rank=i)
                    for i, d in enumerate(retrieved_docs)),
    initial_response=GenerationResult(initial_text, initial_execution_status, fingerprint),
    generation_fingerprint=fingerprint,
    query_policy=trusted_query_policy,
)

engine = DefenseEngine(
    generator=CallableGenerator(generate_once, fingerprint=fingerprint),
    observer=MLObserver("artifacts/observer-v2", embedding_model_dir="models/all-MiniLM-L6-v2"),
    verifier=CallableVerifier(verify_final),
    config=DefenseConfig(allow_live=False),
)
result = engine.repair(request)
final_text = result.final_response.text
```

위 코드는 연결 지점을 설명하는 템플릿이다. 정의되지 않은 실험 변수나 함수를 자동 탐색하지 않는다. 바로 실행할 수 있는 callback 예제는 `examples/integration_example.py`이며 mock을 사용한다. 현재 스크립트에 실험 환경의 실제 함수명을 추측해 연결하지 않았다.

## 3. 연결 시 확인할 계약

- `query_policy`는 검색 문서와 분리된 신뢰할 수 있는 판단이다. ALLOW만 복구한다.
- Generator는 변경된 문서 튜플을 사용한다. DROP_DOCUMENT에서는 항목 자체가 빠진다. 재검색·보충·문서 정렬 변경을 하지 않는다.
- SDK 자동 retry를 끈다. 실제 요청 실패도 전체 추가 호출 예산에 포함한다.
- 초기 생성과 추가 생성에서 모델/endpoint/프롬프트/생성 파라미터를 고정한다.
- `fresh=True` 호출은 실험 코드의 캐시도 우회해야 한다. `replicate_id`를 실험 기록에 보관한다.
- Observer는 query/response만 받는다. 학습 artifact가 미검증이면 기본 repair에 사용되지 않는다.
- Verifier는 query/최종 response/남은 원문의 연속 구간만 받는다. 제거된 텍스트나 정답을 전달하지 않는다.
- 최초 거부를 대체할 때에는 `result.final_response`를 그대로 사용한다. 검증 후 다시 생성하지 않는다.

실제 외부 호출은 사용자가 명시적으로 허용한 뒤에만 `allow_live=True`로 바꾼다. FinalLLMVerifier를 사용한다면 그 설정에도 별도의 live 허용이 필요하다. 환경에 키가 있는 것만으로 자동 실행하지 않는다. 공급자 차단/인증 오류/한도 오류 이후 endpoint나 키를 교체하거나 요청을 재제출하지 않는다.

## 4. 실제 관측기와 검증기 준비

1. 완결된 개발 로그 복사본을 `data/imported/`에 둔다.
2. [LABELING.md](LABELING.md)에 따라 query/response 상태를 human_verified로 검수한다.
3. [OBSERVER_WORKFLOW.md](OBSERVER_WORKFLOW.md)에 따라 그룹 분리 학습과 봉인된 test 평가를 수행한다.
4. `doctor --artifact-dir ... --embedding-model-dir ...`에서 artifact 상태와 실제 연결 상태를 확인한다.
5. 사용자가 준비한 CallableVerifier 또는 선택 FinalLLMVerifier를 연결한다.

학습 모델·실제 라벨·API 키는 Git에 추가하지 않는다. mock 연결이나 인용 문자열 존재만으로 실제 검증기가 준비되었다고 표시하지 않는다.

## 5. 실험 결과 기록

엔진 결과에는 설정, 실제 추가 호출 수, phase별 상태, operation과 원문 좌표, search_complete, 미탐색 범위, 종료 사유가 있다. `report.save_run(result)`로 원문이 없는 실행 보고서를 저장할 수 있다. 완결 replay를 만들려면 실제 호출 입력과 반환 결과를 별도로 기록해야 한다.

`grounding_passed`와 외부 평가의 `gold_correctness`를 별도로 기록한다. 실제 평가의 attack_type, attack_spans, reference_answer는 `data/templates/evaluation-record.schema.json`의 evaluation 영역에 둔다. 원인 레이블을 방어 입력에 합치지 않는다.

현재 지원하는 탐색은 단일 문서 연속 구간과 최대 두 문서 전체 변경이다. 미탐색 범위가 있는 결과를 전체 공격 제거로 해석하지 않는다. 가용성 복구가 안 된 사례, provider block과 일반 오류도 평가 분모에서 유지한다.

## 6. 배포 내용

`scripts/export_source.py`는 코드·테스트·합성 fixture·설정·문서·빈 데이터 schema만 포함한 배포본을 만든다. `.venv`, 캐시, `runs`, 실제 imported 데이터, 모델과 artifact는 포함하지 않는다. Git 상태를 바꾸지 않는다.

```bash
source scripts/env.sh
.venv/bin/python scripts/export_source.py --output dist/rag-defense-v2-source.tar.gz
```

모든 파일의 SHA-256은 archive 안의 `SOURCE_MANIFEST.json`에 기록한다. 다른 서버로 복사한 뒤에도 어떤 소스인지 확인할 수 있다. 이 hash는 출처 서명이나 실제 방어 성능의 증명은 아니다.
