# 명시적으로 복사한 입력만 사용

이 디렉터리는 현재 실제 데이터가 없는 상태다. 다른 RAG 작업이 끝난 뒤 사용자가 준비한
완결된 로그 복사본만 여기에 둔다. 코드가 원래 프로젝트를 탐색하거나 실시간 로그를 읽지 않는다.

- 관측기 개발: `../templates/observer-import.schema.json`, `../templates/observer-review.csv` 참고.
- 최종 방어 평가: `../templates/evaluation-record.schema.json` 참고.
- 개발 데이터와 최종 방어 평가 데이터는 query/유사 기록까지 겹치지 않게 분리한다.
- 예시 행이나 실제 로그를 가장한 합성 데이터는 포함되어 있지 않다.
