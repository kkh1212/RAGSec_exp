NQ 미니 인덱스 이전용 패키지

Python 3.11 환경에서 검증했습니다. Ollama는 답변 생성에만 필요합니다.
다른 서버로 이 디렉터리 전체를 옮긴 뒤 아래 명령을 실행하세요.

  python3.11 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt

기준 Top-5 검색
  .venv/bin/python run_rag.py --mode baseline --qid test12

확장 Top-5 검색
  .venv/bin/python run_rag.py --mode expanded --qid test12

기준 Top-5 원문으로 Ollama 답변 생성
  .venv/bin/python run_rag.py --mode baseline --qid test12 --generate --model qwen3:4b

Ollama 기본 주소는 http://127.0.0.1:11434입니다. 다른 주소나 태그를 사용하는 경우
--ollama-url 및 --model을 지정하세요. 모델 파일은 Ollama 서버에서 관리합니다.

검색은 data/queries.jsonl에 나열된 350개 질문 ID와 사전 계산한 질의 벡터를 사용합니다.
확장 모드는 clean.index를 메모리에 읽고 addtional_vecs.npy를 index.add()로 붙입니다.
출력에서 additional=true는 추가 파일에서 검색된 벡터를 뜻합니다.
확장 모드는 문서 ID와 점수를 출력합니다. Ollama 답변 생성에는 기준 원문이 사용됩니다.
test12에서는 기준 33,947개, 확장 34,245개이며 추가 벡터 한 건이 3위에 나옵니다.

data/clean.index                기준 FAISS 인덱스
data/clean_ids.json             기준 인덱스 행별 문서 ID
data/clean_docs.jsonl           모델에 전달할 기준 문서 원문
data/addtional_vecs.npy         확장할 추가 벡터
data/additional_ids.json        추가 행별 문서 ID
data/queries.jsonl              질문 ID와 질문 텍스트
data/query_vecs.npy             질문 벡터
prompt.txt                      기준 RAG 프롬프트 템플릿
