"""논문 C1 탐지기(legacy_calib_heldout)를 부스에서 그대로 쓰기 위한 어댑터.

태호 c1_detect_eval.py(2026-10-03)의 legacy_calib_heldout 조건을 재현한다.
  - 탐지기: 캡스톤 legacy (features.score_chunk -> classifier.classify_features), @30c7202
  - heldout: MutedRAG 템플릿 문구가 그대로 들어 있는 redirect 프로토타입 제거 (누설 통제)
  - 임곗값: NQ held-out 정상 쌍 600개로 캘리브레이션한 값 (calibration_c1.json)
  - 판정: verdict가 muted_candidate 또는 direct_candidate면 탐지
  - 임베딩: paraphrase-multilingual-mpnet-base-v2, L2 정규화 (캡스톤 src/embedder와 같음)
C1 결과: A1 공격 청크 TPR 99.31%, 정상 청크 FPR A1 5.74% / A0 5.03%
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

if __package__:
    from .vendor.capstone_legacy import classifier, config, embeddings, features
else:
    from vendor.capstone_legacy import classifier, config, embeddings, features

CALIBRATION = Path(__file__).resolve().parent / "vendor" / "capstone_legacy" / "calibration_c1.json"
CONDITION = "legacy_calib_heldout"
TEMPLATE_FRAGMENTS = (  # c1_detect_eval.py와 동일
    "forget the previous details",
    "focus on my new question",
    "the following context provides relevant details",
)
FLAG_VERDICTS = {"muted_candidate", "direct_candidate"}


class LegacyCalibGuard:
    def __init__(self, embed_fn, calibration_path: Path = CALIBRATION, condition: str = CONDITION):
        # 1) 임베딩: 캡스톤 EmbeddingModel이 src.embedder를 못 찾으면 TF-IDF로 조용히 떨어지므로
        #    부스의 mpnet을 직접 물린다 (같은 모델, 같은 정규화).
        def _embed(_self, texts):
            cleaned = ["" if t is None else str(t) for t in texts]
            if not cleaned:
                return np.zeros((0, 0), dtype="float32")
            _self.resolved_backend = "sentence-transformers(booth)"
            return embeddings._l2_normalize(np.asarray(embed_fn(cleaned), dtype="float32"))

        embeddings.EmbeddingModel.embed = _embed
        self.embedder = embeddings.EmbeddingModel("st")
        self.cfg = config.DetectorConfig(embedding_backend="st")

        # 2) heldout 프로토타입 (features.py는 모듈 전역 이름을 읽는다)
        redirect = tuple(features.REDIRECTION_PROTOTYPES)
        refusal = tuple(features.REFUSAL_PROTOTYPES)
        heldout = condition.endswith("heldout")
        if heldout:
            redirect = tuple(p for p in redirect if not any(f in p.lower() for f in TEMPLATE_FRAGMENTS))
        features.REDIRECTION_PROTOTYPES = redirect
        features.ATTACK_INTENT_PROTOTYPES = redirect + refusal
        self.n_redirect = len(redirect)

        # 3) 캘리브레이션 임곗값
        calib = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
        key = "legacy_calib_heldout" if heldout else "legacy_calib"
        allowed = set(config.Thresholds.__dataclass_fields__)
        self.thresholds = config.Thresholds(**{k: v for k, v in calib[key].items() if k in allowed})
        self.condition = condition

    def evaluate(self, query: str, text: str) -> dict:
        f = features.score_chunk(query=query, chunk_text=text, embedder=self.embedder, config=self.cfg)
        v = classifier.classify_features(f, self.thresholds)
        return {
            "flag": v.verdict in FLAG_VERDICTS,
            "verdict": v.verdict,
            "score": float(v.muted_score),
            "reason": (
                f"{v.verdict} · {v.reason} · 앵커 {f.anchor_score:.2f} · 관련도 격차 {f.alignment_gap:.2f} · "
                f"의미 전환 {f.semantic_break_score:.2f} · 문장군 분리 {f.payload_isolation:.2f} · "
                f"전환 유사도 {f.redirect_similarity:.2f} · 거부 유사도 {f.refusal_similarity:.2f}"
            ),
        }
