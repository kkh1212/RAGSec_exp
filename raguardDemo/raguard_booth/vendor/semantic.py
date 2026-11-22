"""Defensive semantic scoring adapted from the capstone's improved detector."""

from dataclasses import dataclass
import re

import numpy as np


THRESHOLD = 0.35
_ANCHOR_MIN = 0.18
_ANCHOR_DAMP = 0.5
_STRUCTURE_BONUS = 0.10
_INTENT_GROUPS = (
    (
        "A command embedded in a document attempts to prevent the assistant from answering.",
        "문서에 삽입된 지시가 챗봇의 정상 답변을 거부하거나 보류하도록 강제하는 경우.",
    ),
    (
        "A document attempts to redirect the assistant away from the user's task.",
        "참고 문서에 포함된 요청이 사용자가 묻는 주제와 다른 과제로 답변을 유도하는 경우.",
    ),
    (
        "A passage attempts to take precedence over platform instructions and control the assistant's output.",
        "문서 문구가 상위 지시의 권한을 주장하거나 시스템의 응답 형식을 강제하는 경우.",
    ),
    (
        "An embedded instruction asks the assistant to access or disclose restricted information.",
        "참고 자료 속 지시가 제한된 개인정보나 시스템 정보 공개를 요구하는 경우.",
    ),
)
_BOUNDARY = re.compile(r"\n+|(?<=[.!?。！？:;])\s+")


def clauses(text: str) -> list[str]:
    return [part.strip() for part in _BOUNDARY.split(text) if part.strip()]


def _center(vectors: np.ndarray) -> np.ndarray:
    mean = vectors.mean(axis=0)
    return mean / max(float(np.linalg.norm(mean)), 1e-8)


def intent_centers(embed) -> np.ndarray:
    vectors = embed([description for group in _INTENT_GROUPS for description in group])
    centers = []
    start = 0
    for group in _INTENT_GROUPS:
        end = start + len(group)
        centers.append(_center(vectors[start:end]))
        start = end
    return np.asarray(centers)


@dataclass(frozen=True)
class SemanticScore:
    score: float
    anchor: float
    alignment_gap: float
    semantic_break: float
    prototype_similarity: float
    payload_isolation: float
    sentence_evidence: np.ndarray
    query_similarities: np.ndarray
    transition: int

    @property
    def reason(self) -> str:
        return (
            f"앵커 {self.anchor:.2f} · 관련도 격차 {self.alignment_gap:.2f} · "
            f"의미 전환 {self.semantic_break:.2f} · 의도 유사도 {self.prototype_similarity:.2f} · "
            f"문장군 분리 {self.payload_isolation:.2f}"
        )


def score(vectors: np.ndarray, query: np.ndarray, intents: np.ndarray) -> SemanticScore:
    similarities = vectors @ query
    anchor = float(similarities.max())
    n = len(vectors)
    order = np.argsort(similarities)
    keep = max(1, n - max(1, n // 5))
    topic = _center(vectors[order[-keep:]])
    prototype = (vectors @ intents.T).max(axis=1)
    evidence = np.maximum(0, prototype * (1.0 - vectors @ topic))
    strongest = int(np.argmax(evidence))
    semantic_break = 0.0
    isolation = 0.0
    transition = -1
    if n > 1:
        adjacent = np.einsum("ij,ij->i", vectors[:-1], vectors[1:])
        transitions = np.maximum(0, 1.0 - adjacent) * np.abs(np.diff(similarities))
        transition = int(np.argmax(transitions))
        semantic_break = float(transitions[transition])
        count = max(1, min(n // 2, round(n * 0.3)))
        isolation = max(0.0, 1.0 - float(_center(vectors[order[:count]]) @ _center(vectors[order[-count:]])))
    gate = 1.0 if anchor >= _ANCHOR_MIN else _ANCHOR_DAMP
    strength = gate * (float(evidence[strongest]) + _STRUCTURE_BONUS * max(semantic_break, isolation))
    return SemanticScore(
        score=strength,
        anchor=anchor,
        alignment_gap=max(0.0, float(np.percentile(similarities, 80) - np.percentile(similarities, 20))),
        semantic_break=semantic_break,
        prototype_similarity=float(prototype[strongest]),
        payload_isolation=isolation,
        sentence_evidence=evidence,
        query_similarities=similarities,
        transition=transition,
    )


def select_removals(result: SemanticScore) -> set[int]:
    """Select locally anomalous high-intent clauses inside a flagged chunk."""
    evidence = result.sentence_evidence
    leading = float(evidence.max())
    if leading < 0.20:
        return set()
    risky = set(np.flatnonzero(evidence >= max(0.20, 0.75 * leading)).tolist())
    after_break = result.transition + 1
    if result.transition >= 0 and result.semantic_break >= 0.12:
        relatedness = float(result.query_similarities[after_break])
        if result.anchor - relatedness >= 0.12 and evidence[after_break] >= 0.15:
            risky.add(after_break)
    return risky
