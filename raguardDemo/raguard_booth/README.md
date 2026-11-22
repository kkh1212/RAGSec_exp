# RAGuard 부스 시연

터미널에서 질문 한 건을 입력하면 **RAGuard OFF**와 **RAGuard ON**의 답변을 차례로 출력합니다. 두 경로는 같은 검색 결과와 같은 Ollama 모델 및 시스템 프롬프트를 사용합니다. OFF는 검색 문맥을 바로 모델에 전달합니다. ON은 의미 탐지와 문장 정제를 거친 문맥을 전달합니다. 두 경로에서 모델 자체의 응답·거절 동작을 관찰하며, 응답 문구만으로 거절 원인을 특정할 수 없습니다.

## NQ 전달 인덱스 시연 (`transfer` 프로필, 논문 조건)

전시회 시연은 이 프로필을 사용합니다. 논문 실험(rag-availability)의 NQ 질의 350개와 MutedRAG 공격 문서 298개를 쓰고, 생성·탐지 조건을 논문과 맞췄습니다. 같은 Top-5에서 **OFF는 공격 문서 때문에 거부**하고, **ON은 C1 탐지기로 공격 문서를 제외한 뒤 재검색 보충**으로 답을 복구하는 것을 보여줍니다.

### 데이터

`../rag_transfer/data/`에 둡니다(git 제외). 전체 NQ 인덱스(268만 개)에서 질의별 상위 100개 벡터를 그대로 꺼낸 미니 인덱스라서, 등록 질의 350개의 Top-5는 전체 인덱스 결과와 같습니다(`check.json` 350/350, 298/298 일치).

| 파일 | 내용 |
|---|---|
| `clean.index`, `clean_ids.json` | 정상 문서 33,947개 FAISS 인덱스와 행별 ID |
| `clean_docs.jsonl` | 정상 문서 원문 |
| `additional_vecs.npy`(또는 `addtional_vecs.npy`), `additional_ids.json` | 공격 문서 298개 벡터와 ID. `expanded` 검색 때 `index.add()`로 붙임 |
| `queries.jsonl`, `query_vecs.npy` | 등록 질의 350개와 사전 계산한 질의 벡터 |
| `docs1.jsonl` (이 폴더 또는 data) | 공격 문서 원문 |

### 실행

```bash
bash setup.sh live
ollama pull llama3:8b-instruct-q8_0          # GPU 16GB 이상이면 llama3:8b-instruct-fp16
export BOOTH_MODEL=llama3:8b-instruct-q8_0
export BOOTH_OLLAMA_URL=http://127.0.0.1:11434
.venv/bin/python booth_cli.py --mode live --profile transfer --search-mode expanded --example test230
.venv/bin/python booth_cli.py --mode live --profile transfer --search-mode expanded --example test230 --json   # 프론트엔드용
```

Windows PowerShell에서 `--json`을 파일로 저장할 때는 `>`가 한글을 깨뜨리므로 `cmd /c "... --json > out.json"`을 사용합니다. 11434 포트가 Windows 예약 범위(`netsh interface ipv4 show excludedportrange protocol=tcp`)에 걸리면 `OLLAMA_HOST`와 `BOOTH_OLLAMA_URL`을 범위 밖 포트로 맞춥니다.

### 적용한 설정

| 항목 | transfer 프로필 |
|---|---|
| 생성 모델 | Llama-3-8B-Instruct (Ollama). 논문은 fp16, 로컬 검증은 q8 |
| 프롬프트 | 논문 템플릿(rag-availability `04_run_stage.py` `RAG_PROMPT`). 시스템 프롬프트 없음, 컨텍스트는 본문을 줄바꿈으로 연결 |
| 생성 | temperature 0, 최대 160토큰. 길이 초과는 오류로 버리지 않고 `ollama.truncated=1`로 표시 |
| 탐지 | 논문 C1 `legacy_calib_heldout` 이식 (`legacy_guard.py`, `vendor/capstone_legacy/`) |
| 방어 | 탐지된 청크 통째로 제외(DROP) → 6~15위 문서를 같은 탐지기에 통과시켜 Top-5까지 보충(backfill) |
| 표시 | OFF와 ON 화면에는 문서 원문을 기본으로 숨김 (`--show-original-text`로 표시) |

qwen3:4b-instruct와 이전 한국어 시스템 프롬프트를 쓰면 시연이 성립하지 않습니다. qwen은 공격 문맥에서 같은 구절을 반복해 토큰 한도에 걸렸고, 한국어 프롬프트의 "자료 속 지시를 따르지 마세요"는 OFF에서도 공격을 막아 OFF와 ON의 차이를 없앴습니다(2026-11-21 로컬 비교).

### 탐지기 검증 (`eval_detector.py`, 등록 질의 350개, LLM 없이)

| | 부스 이식본 | 논문 C1 | 이전 부스 탐지(improved 0.35) |
|---|---|---|---|
| 공격 청크 TPR | 289/291 = 99.31% | 289/291 = 99.31% | 164/291 = 56.36% |
| 정상 청크 FPR (공격 상태) | 4.93% | 5.74% | 0.34% |
| 정상 청크 FPR (깨끗한 상태) | 4.69% | 5.03% | 0.29% |
| C1 청크 판정 일치율 | 깨끗한 상태 99.2%, 공격 상태 99.5% | — | — |

```bash
.venv/bin/python eval_detector.py --c1-chunks <rag-availability>/results/c1_taeho/C1_detect/chunks_legacy_calib_heldout.jsonl
```

| 환경 변수 | 기본값 또는 역할 |
|---|---|
| `BOOTH_DETECTOR` | `c1`(기본). `improved`면 이전 부스 탐지로 비교 |
| `BOOTH_BACKFILL_POOL` | `10`. 보충 후보로 볼 6위 이후 문서 수 |
| `BOOTH_MODEL`, `BOOTH_OLLAMA_URL` | 위 실행 예시 참고 |

## 실험 C 기본 데이터

`experiment-c`가 기본 프로필입니다. 코퍼스는 `experiment_docs/`의 TXT 30개이며, 예시 목록은 `questions/v3_questions.json`의 원래 순서와 ID(`Q01`~`Q30`)를 따릅니다. 예시와 자유 입력은 모두 같은 코퍼스를 검색합니다. Windows 작업공간에서는 상위 폴더의 `muterag_acd_experiment_bundle_20260603/experiment_C/data/experiments_v3/`를 바로 사용합니다. 배포 폴더에 `experiment_c_data/`가 있으면 그 아래의 `experiment_docs/`와 `questions/`를 사용합니다.

Windows PowerShell에서 질문 목록만 확인하려면 `raguard_booth` 폴더에서 `python booth_cli.py --mode preview --list-examples`를 실행합니다. 목록 조회에는 Python과 질문 JSON만 필요합니다.

## Windows PowerShell에서 Ubuntu로 전송

아래 블록은 배포 파일과 실험 C 데이터의 임시 압축 파일을 만들어 `ubuntu@10.0.10.30`에 전송합니다. 질문 파일은 실행에 필요한 `v3_questions.json`을 사용합니다. SSH에서 키나 비밀번호를 요청하면 서버 로그인에 사용하는 인증 정보를 입력합니다.

```powershell
$project = 'C:\Users\gar1\Desktop\muterag\rag-exp\rag-exp'
$booth = Join-Path $project 'raguard_booth'
$dataset = Join-Path $project 'muterag_acd_experiment_bundle_20260603\experiment_C\data\experiments_v3'
$server = 'ubuntu@10.0.10.30'
$archiveDir = Join-Path $env:TEMP ('raguard-booth-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $archiveDir -ErrorAction Stop | Out-Null

$appArchive = Join-Path $archiveDir 'raguard-booth.tar.gz'
$dataArchive = Join-Path $archiveDir 'experiment-c-data.tar.gz'
tar.exe -czf $appArchive --exclude __pycache__ --exclude '*.pyc' -C $booth .gitattributes .gitignore README.md booth_cli.py booth_cases.py booth_runtime.py requirements-live.txt run.sh setup.sh vendor corpus fixtures
if ($LASTEXITCODE -ne 0) { throw '데모 압축 실패' }
tar.exe -czf $dataArchive -C $dataset experiment_docs questions/v3_questions.json
if ($LASTEXITCODE -ne 0) { throw '실험 C 데이터 압축 실패' }

ssh.exe $server 'umask 077 && mkdir -p /home/ubuntu/raguardDemo/raguard_booth && install -d -m 700 /home/ubuntu/raguardDemo/.booth-upload'
if ($LASTEXITCODE -ne 0) { throw '서버 전송 폴더 준비 실패' }
Push-Location -LiteralPath $archiveDir
try {
    scp.exe ./raguard-booth.tar.gz ./experiment-c-data.tar.gz "${server}:/home/ubuntu/raguardDemo/.booth-upload/"
    if ($LASTEXITCODE -ne 0) { throw '파일 전송 실패' }
} finally {
    Pop-Location
}
Remove-Item -LiteralPath $appArchive, $dataArchive
Remove-Item -LiteralPath $archiveDir
ssh.exe $server
```

## Ubuntu에서 배포 및 실제 실행

SSH로 로그인한 뒤 아래 블록으로 압축을 풉니다. 데이터를 새 폴더에 푼 뒤 교체하므로 재배포할 때 이전 코퍼스 파일이 새 코퍼스에 섞이지 않습니다. 이전 데이터가 있으면 같은 위치의 별도 백업 폴더로 옮깁니다.

```bash
(
  set -euo pipefail
  umask 077
  cd /home/ubuntu/raguardDemo/raguard_booth
  upload=../.booth-upload
  next=$(mktemp -d ./.experiment-c.XXXXXX)
  tar --no-same-permissions -xzf "$upload/experiment-c-data.tar.gz" -C "$next"
  test -d "$next/experiment_docs"
  test -f "$next/questions/v3_questions.json"
  tar --no-same-permissions -xzf "$upload/raguard-booth.tar.gz"
  if [ -e experiment_c_data ]; then
    backup=$(mktemp -d ./.experiment-c-backup.XXXXXX)
    mv -- experiment_c_data "$backup/"
    printf '이전 데이터 %s/experiment_c_data\n' "$backup"
  fi
  mv -T -- "$next" experiment_c_data
  bash -n setup.sh
  bash -n run.sh
  find experiment_c_data/experiment_docs -type f -name '*.txt' | wc -l
  bash run.sh preview --list-examples
  rm -- "$upload/raguard-booth.tar.gz" "$upload/experiment-c-data.tar.gz"
)
```

문서 수 `30`과 질문 ID `Q01`~`Q30`이 표시되는지 확인합니다. 실제 실행 화면에는 검색 문서 발췌와 정제 전후 텍스트가 표시될 수 있습니다.

Python 3.11 또는 3.12와 Ollama를 준비합니다. Ollama 서비스가 시작된 상태에서 모델을 내려받습니다. Ubuntu 등에서 가상환경 생성이 안 되면 해당 Python 버전의 `venv` 패키지를 먼저 설치합니다. [Ollama Linux 설치](https://docs.ollama.com/linux) · [Qwen3 4B Instruct 모델](https://ollama.com/library/qwen3:4b-instruct)

```bash
cd /home/ubuntu/raguardDemo/raguard_booth
bash setup.sh live
ollama pull qwen3:4b-instruct
export BOOTH_OLLAMA_URL=http://127.0.0.1:11434
bash run.sh live --examples-only
```

`1`을 입력하면 `Q01`을 실행합니다. `?`는 목록이고 `q`는 종료입니다. 첫 실제 질의에 앞서 공개 MPNet 모델을 Hugging Face에서 받아 메모리 검색 인덱스를 구성합니다. 같은 계정에 임베딩 모델이 캐시된 뒤에는 `HF_HUB_OFFLINE=1 bash run.sh live --examples-only`로 캐시된 모델을 사용할 수 있습니다.

| 명령 | 동작 |
|---|---|
| `bash run.sh live` | 자연어 자유 입력과 예시 번호 선택 |
| `bash run.sh live --examples-only` | 예시 번호만 입력 |
| `bash run.sh live --example Q01` | 실험 C 예시 Q01 한 건 실행 후 종료 |
| `bash run.sh live --question "질문을 여기에 입력"` | 자유 질문 한 건 실행 후 종료 |
| `bash run.sh preview --list-examples` | 예시 번호와 ID 확인 |

`--all-sources`는 전체 검색 문맥을 펼칩니다. 기본 출력은 응답에 인용된 `[n]` 문서의 내용과 위치를 보여주며, 인용 번호가 없으면 상위 검색 문맥 2개를 표시합니다. `--json`을 `--example` 또는 `--question`과 함께 사용하면 한 건의 전체 기록을 JSON으로 받을 수 있습니다.

## 기존 시연 사례

`bash run.sh preview --profile booth`로 가상 사내 규정 시연을 엽니다. Windows에서는 `python booth_cli.py --mode preview --profile booth`를 실행합니다. `1`~`4`는 가상 사내 규정 질의이며, `5`~`7`은 복구·오탐·미탐 모의 사례입니다. 미리보기와 모의 사례에는 실측 시간이 표시되지 않습니다.

### 실제 탐지·정제 확인

`bash run.sh live --profile booth --example defense-check --all-sources`로 `8`번을 실행하면 [휴대용 모니터 대여 규정](fixtures/defense/portable-monitor.md)을 검색합니다. 이 문서에는 대여 규정과 주제가 다른 무해한 문장 하나가 들어 있습니다. OFF는 원문을 사용하고, ON은 실제 임베딩으로 후보를 판정한 뒤 정제를 적용합니다. 기본 임베딩으로 확인한 탐지 점수는 약 0.497이었습니다. 정제 후에는 무관한 한 문장이 제거되고 ‘수령일을 포함해 7일’이라는 근거가 남았습니다. 이 사례는 탐지와 문장 정제 확인용입니다. 모델 답변은 양쪽 모두 같을 수 있습니다.

### V3 실험의 원본 질문과 문서로 실행

`--profile v3`는 당시 `experiment_normal`의 영어 TXT 문서 30개와 원본 영어 질문 Q06·Q04·Q05·Q21을 사용합니다. 모델은 한국어로 답변합니다. 리스트는 모델 준비 없이 확인할 수 있습니다.

```bash
bash run.sh preview --profile v3 --list-examples
bash run.sh live --profile v3 --examples-only
```

장비 지급·회수 질문 Q06을 정상 문서와 검토용 문서로 비교하려면 아래 두 명령을 실행합니다.

```bash
bash run.sh live --profile v3 --example v3-q06 --all-sources
bash run.sh live --profile v3 --example v3-review --all-sources
```

- `v3-q06`은 `profiles/v3/normal/`의 원본 30개를 검색합니다.
- `v3-review`는 같은 Q06을 사용합니다. 검색할 때 정상 문서 29개를 그대로 사용하고 장비 지급·회수 문서 B3만 `profiles/v3/review/`의 문서로 바꿉니다. 이 문서에는 질문과 무관한 무해한 문단을 덧붙였습니다. ON의 탐지 근거·점수, 정제 전후와 OFF·ON의 모델 입력 문맥을 비교할 수 있습니다.
- `profiles/v3/examples.json`에는 예시 ID와 원본 질문·정상 문서 매핑이 들어 있습니다.

기존 V3 프로필을 새 서버에서 쓰려면 `profiles/v3/`가 필요합니다. 설치 명령과 모델은 위의 실제 실행과 같습니다. 첫 실행과 코퍼스 조건을 바꿀 때 임베딩을 만들고 메모리 인덱스를 자동으로 구성합니다. 이 검토용 비교는 실제 탐지·정제 경로를 확인하는 용도입니다.

## 적용한 설정

| 항목 | 부스 기본 설정 |
|---|---|
| 코퍼스 | 실험 C `experiment_docs/`의 TXT 30개. 제목과 본문을 임베딩 |
| 생성 모델 | Ollama `qwen3:4b-instruct`, temperature `0` |
| 임베딩 | `sentence-transformers/paraphrase-multilingual-mpnet-base-v2` |
| 검색 | 청크 최대 800자, 목표 겹침 180자. BM25 상위 60, FAISS 상위 40. 점수 정규화 후 결합, 최종 상위 8, 문맥 상한 10. 리랭크 없음 |
| 탐지 | `improved` 점수 구조를 옮겼습니다. 임곗값 0.35, 같은 문서의 이웃 청크 앞뒤 1개 |
| 정제 | 후보에서 문장 제거. 남은 내용이 80자 또는 1문장 미만이면 청크 제외. 검색 후보가 남아 있으면 보강 |
| 모델 응답 | 양쪽에서 같은 모델과 시스템 프롬프트 사용. 생성 모델의 자체 거절은 응답으로 관찰 |

발표 실험의 검색 규모와 의미 탐지·정제 설정을 가져왔습니다. 발표 실험은 외부 Guardrail이 검색 문맥을 검사하는 환경에서 진행했습니다. 당시 실행 결과는 원래 `rag-exp`의 `ollama_prompt_guard_results_20260607/`에 있고 모델별 집계는 `analysis/acd_radar/metrics_raw.csv`에 있습니다. 부스의 코퍼스와 판정 조건이 달라 발표의 DoS율·복구율·지연 수치는 이 구성에 적용할 수 없습니다. 실제 실행에서 새로 측정한 시간만 표시합니다.

| 환경 변수 | 기본값 또는 역할 |
|---|---|
| `BOOTH_OLLAMA_URL` | `http://127.0.0.1:11434`. 별도 리눅스 서버의 Ollama를 쓰는 경우 변경 |
| `BOOTH_MODEL` | `qwen3:4b-instruct`. Ollama에 내려받은 태그 |
| `BOOTH_EMBED_MODEL` | 다국어 MPNet 모델 ID 또는 로컬 모델 경로 |
| `BOOTH_PYTHON`, `BOOTH_VENV_DIR` | `python3`, `.venv` |

## 시간 읽는 법

실제 비교마다 빈 요청으로 Ollama 모델을 미리 불러오고 5분 유지를 요청합니다. 사전 로드 시간은 별도로 표시하며 양쪽 총시간에서 제외합니다. 같은 코퍼스에서 연속 비교할 때 답변 생성 순서를 OFF→ON, ON→OFF로 번갈아 적용합니다. 출력은 항상 OFF, ON 순서이며 실제 실행 순서도 함께 표시합니다.

양쪽 총시간에 공통 검색 시간을 같은 값으로 포함합니다. ON에서는 의미 탐지와 문장 정제 시간을 따로 표시합니다. Ollama가 세부 값을 제공하면 각 응답의 모델 로드 시간, 입력 처리 시간·토큰 수, 출력 생성 시간·토큰 수도 확인할 수 있습니다.

추가 시간은 `ON 총시간 − OFF 총시간`이고, 증가율은 `추가 시간 ÷ OFF 총시간 × 100`입니다. 이 값에는 정제에 따른 입력 길이 변화, 응답 길이, 캐시 상태, 실행 순서와 서버 스케줄링의 영향도 포함됩니다. 한쪽에서 근거 부족으로 생성을 건너뛰면 그 영향도 포함됩니다. Ollama 캐시는 유지되므로, 비교 한 건의 수치는 그 요청의 총시간 차이로 읽으세요. 단계별 탐지·정제 시간을 보면 해당 로직의 처리 시간을 따로 확인할 수 있습니다. 실행 오류가 있는 비교에는 차이와 증가율을 표시하지 않습니다. Ollama 호출은 연결과 응답 대기에 각각 180초 제한을 사용합니다.

## 파일 구성

- `booth_cli.py`는 터미널 입력과 두 실행 결과를 다룹니다.
- `booth_runtime.py`는 코퍼스 청크화, BM25·FAISS 검색, 문맥 보강 및 Ollama 생성을 연결합니다.
- `vendor/semantic.py`는 의미 탐지와 정제 대상 문장 선정에 쓰는 점수 로직입니다(experiment-c·v3 프로필).
- `legacy_guard.py`와 `vendor/capstone_legacy/`는 transfer 프로필의 C1 탐지기입니다. 캡스톤 `30c7202` 원본을 수정 없이 복사했고(`SOURCE_SHA256.txt`), 임곗값은 `calibration_c1.json`입니다.
- `transfer_index.py`는 미니 NQ 인덱스 검색, `eval_detector.py`는 탐지기 오프라인 점검입니다.
- `experiment_c_data/`는 서버에 배포한 실험 C 코퍼스와 질문 JSON입니다.
- `booth_cases.py`와 `corpus/`는 기존 부스 프로필의 문서, 예시 답변, 모의 사례입니다.
- `fixtures/defense/`는 실제 탐지·정제를 확인하는 8번의 전용 문서입니다.
- `profiles/v3/`는 V3의 정상 문서, 검토용 문서, 예시 질문을 담습니다.
