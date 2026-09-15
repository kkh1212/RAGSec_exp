# 지원 범위와 미측정 사항

이번 대상은 query_policy=ALLOW인 정상 질문에서 **정상 반환된 거부 텍스트**의 복구다. 정책 DENY/UNKNOWN, 초기 실행 오류/차단, 최초 OTHER는 복구 대상이 아니다. 제공된 문서에 실제 근거가 없으면 거부 유지가 올바른 결과일 수 있으나 가용성 복구 성공으로 집계하지 않는다.

후보는 이번 요청에서 거부 해소에 도움이 된 수정이다. 악성 문서 증명, 완전한 공격 제거, gold 정답 보장이 아니다. 검색·reranking·재검색·보충·영구 정화·공격 생성은 구현하지 않았다. 최대 두 문서 전체 조합과 단일 문서 연속 구간 탐색은 결정론적 제한 탐색이며 세 문서 이상이나 임의의 모든 구간 조합을 포괄하지 않는다.

MiniLM/LR은 실제 검수된 query/response 학습과 충분한 검증이 필요하다. 현재 없다. 근접중복 검사와 고정 목표는 엔지니어링 도구이며 데이터 누수 전부나 분포 이동을 막는 증명은 아니다. 긴 입력은 분할 구현이 없으면 UNCERTAIN이다. 언어·대상 생성 모델·응답 형식별 실제 성능은 아직 측정하지 않았다.

CallableVerifier는 사용자 함수다. FinalLLMVerifier는 고정 지시와 데이터 분리·엄격 JSON·연속 원문 인용 검사를 사용하지만 검증기 자체도 컨텍스트의 영향을 받을 수 있다. 인용 존재만으로 의미 지지/정답이 증명되지 않는다. fresh 대조도 샘플링 불확실성을 없애지 않는다. 생성 설정은 고정한다.

PROVIDER_BLOCK은 구조화 신호만으로 기록한다. API 차단/인증/한도 오류 후 자동 복구·동일 요청 재제출·안전장치 비활성화·키/endpoint 교체는 하지 않는다. 오류를 평가에서 버리지 않는다. 전체 ALLOW 요청 기준 가용성 복구 지표에는 차단/오류 사례도 남는다.

## 측정 분리

외부 평가에서는 observer confusion matrix/precision/recall/F1, 원인 라벨과 변경 문서/구간의 겹침, candidate 비거부 관측, 최종 비거부 전환, 근거 있는 gold 정답 복구, clean 불필요 변경, 정당한 거부 유지, 호출·토큰·시간, 예산 소진, 공급자 차단·실행 오류, 검증 실패 후 거부 유지 비율을 분리한다. 원인 라벨과의 겹침은 의미적 인과 증명이 아니다. 외부 gold scorer가 없으면 grounded_correct_recovery는 null이며 gold 측정 coverage를 함께 낸다.

합성 fixture는 숨은 원인을 generator 안의 표/함수로 구현하는 알고리즘 검사다. 그 정보를 engine은 읽지 않는다. 합성 시나리오에 공격명을 붙여 Jamming Instruction Injection/Jamming Oracle Generated/Jamming BBO/MutedRAG/TabooRAG 실험이라고 하지 않는다. replay 또한 새로운 모델 호출이나 실제 공격 완화 측정이 아니다.

실제 로그가 없으므로 현재 상태는 REAL_ATTACK_EVALUATION_NOT_RUN이다. 공격별 미측정 지표는 null이다. 별도 copied_real 레코드, 실제 Generator/검증된 Observer/Verifier, 외부 정답 평가를 준비한 뒤 고정된 설정으로 최종 평가해야 한다. 최종 평가 결과를 보고 관측기/threshold를 조정하지 않는다.
