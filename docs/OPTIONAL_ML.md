# 선택 ML

기존 optional_ml의 고정 임베딩 + LogisticRegression 구성을 확장했다.

- [검수 기준과 데이터 형식](LABELING.md)
- [설치, 그룹 분할, 학습, 봉인된 test 평가](OBSERVER_WORKFLOW.md)
- [준비 상태와 현재 미실행 항목](READINESS.md)

현재 실제 labels/encoder/classifier는 없다. 테스트용 CPU fitting은 synthetic_test_only 산출물이며 실전 MLObserver가 거부한다. 기본 demo/doctor/replay는 torch/sentence-transformers를 import하거나 모델을 다운로드하지 않는다.
