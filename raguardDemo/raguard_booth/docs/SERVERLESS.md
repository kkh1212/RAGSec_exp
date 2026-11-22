# B안: LLM만 런팟 서버리스, 나머지는 CPU 서버

```
[노트북 브라우저/터미널] → [CPU 서버: booth_cli · 검색 · C1 탐지 · DROP/백필 · 프롬프트 조립]
                                         │  HTTPS (프롬프트 텍스트만)
                                         ▼
                         [런팟 서버리스: vLLM + Llama-3-8B-Instruct]
```

데이터(인덱스·문서)는 CPU 서버에만 있다. 런팟에는 모델만 올라가고 네트워크 볼륨을 쓰지 않으므로 데이터센터에 묶이지 않는다.

## 1. 준비

- Hugging Face 토큰: `meta-llama/Meta-Llama-3-8B-Instruct` 라이선스에 동의한 계정의 토큰 (read 권한)
- 런팟 API 키: Settings → API Keys에서 생성. **git에 올리지 않는다.**

## 2. 엔드포인트 만들기 (런팟 콘솔)

1. Serverless → **vLLM** (Runpod Hub의 `runpod-workers/worker-vllm`) → Deploy, 최신 버전
2. Model: `meta-llama/Meta-Llama-3-8B-Instruct`, Hugging Face 토큰 입력
3. Advanced
   - Max Model Length: `4096` (프롬프트 약 600토큰 + 답변 160토큰이면 충분)
   - 환경변수 `DTYPE=float16` (논문 실험이 fp16)
4. 엔드포인트 설정
   - GPU: 24GB급을 1순위로 두고 24GB PRO, 48GB 등 **여러 종류를 우선순위로 지정** (재고 대비)
   - 데이터센터 제한 없음, **네트워크 볼륨 붙이지 않음**
   - Max workers `1`, Active workers `0` (개발 중), FlashBoot 켜기
   - Idle timeout: 개발 중엔 짧게(예: 60초)
5. 생성 후 **Endpoint ID** 기록. 첫 요청에서 모델을 받느라 몇 분 걸린다.

## 3. 동작 확인

```bash
export RUNPOD_API_KEY=...            # PowerShell: $env:RUNPOD_API_KEY="..."
export EID=...                       # Endpoint ID
curl -s https://api.runpod.ai/v2/$EID/openai/v1/chat/completions \
  -H "Authorization: Bearer $RUNPOD_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"meta-llama/Meta-Llama-3-8B-Instruct","messages":[{"role":"user","content":"Say hi"}],"temperature":0,"max_tokens":16}'
```

모델 이름이 다르다는 오류가 나면 엔드포인트의 `OPENAI_SERVED_MODEL_NAME_OVERRIDE` 값이나 `/openai/v1/models` 응답에 나온 이름을 `BOOTH_MODEL`에 넣는다.

## 4. 부스에서 쓰기

```bash
export BOOTH_LLM_BACKEND=openai
export BOOTH_OPENAI_BASE_URL=https://api.runpod.ai/v2/$EID/openai/v1
export BOOTH_OPENAI_API_KEY=$RUNPOD_API_KEY
export BOOTH_MODEL=meta-llama/Meta-Llama-3-8B-Instruct
.venv/bin/python booth_cli.py --mode live --profile transfer --search-mode expanded --example test230
```

- 요청은 user 메시지 하나(논문 템플릿), temperature 0, max_tokens 160, seed 0. chat template은 vLLM이 한 번만 씌우므로 BOS가 중복되지 않는다.
- 첫 비교 전에 짧은 요청으로 워커를 깨운다(`preload_seconds`). 콜드 스타트면 몇 분 걸릴 수 있어 대기 상한은 `BOOTH_OPENAI_WARMUP_TIMEOUT`(기본 600초), 일반 요청은 `BOOTH_OPENAI_TIMEOUT`(기본 120초).
- `BOOTH_LLM_BACKEND`를 지정하지 않으면 기존처럼 로컬 Ollama를 쓴다.

## 5. 전시 당일

- 부스 열기 전날 저녁: Active workers `1`로 올려 워커를 미리 잡아둔다. 켜둔 시간만큼 과금된다.
- 부스 닫으면 Active workers `0`.
- 시연 질의는 **이 엔드포인트로 다시 확인한 것만** 쓴다. vLLM fp16은 논문(HF transformers fp16)과 같은 정밀도지만 연산 경로가 달라 답이 바뀔 수 있다.
- 엔드포인트가 응답하지 않을 때를 대비해 노트북에 캐시 재생본을 둔다.

## 6. 보안

- API 키는 환경변수로만 넘긴다. 셸 히스토리나 스크린샷에 남지 않게 주의.
- 이 레포는 공개다. 키, `.env`, 데이터 파일을 커밋하지 않는다.
