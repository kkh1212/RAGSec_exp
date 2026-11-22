"""Read the portable FAISS bundle without opening documents during retrieval."""

from __future__ import annotations

import json
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


APP_ROOT = Path(__file__).resolve().parent
TOP_K = 5


def _json_lines(path: Path):
    try:
        with path.open(encoding="utf-8-sig") as source:
            for number, line in enumerate(source, 1):
                try:
                    yield json.loads(line)
                except ValueError:
                    raise ValueError(f"{path.name}의 {number}번째 줄을 읽을 수 없습니다.") from None
    except UnicodeError:
        raise ValueError(f"{path.name}의 UTF-8 인코딩을 확인해 주세요.") from None
    except OSError:
        raise OSError(f"{path.name}을 열 수 없습니다.") from None


def load_questions(root: Path) -> list[dict[str, str]]:
    path = root / "data" / "queries.jsonl"
    questions = []
    used_ids = set()
    for number, row in enumerate(_json_lines(path), 1):
        qid = row.get("qid") if isinstance(row, dict) else None
        text = row.get("text") if isinstance(row, dict) else None
        if not isinstance(qid, str) or not qid.strip() or not isinstance(text, str) or not text.strip():
            raise ValueError(f"{path.name}의 {number}번째 줄에 질문 ID 또는 질문이 없습니다.")
        if qid in used_ids:
            raise ValueError(f"{path.name}에 중복된 질문 ID가 있습니다.")
        used_ids.add(qid)
        questions.append({"qid": qid, "text": text})
    if not questions:
        raise ValueError(f"{path.name}에 등록된 질문이 없습니다.")
    return questions


def _load_ids(path: Path) -> list[str]:
    try:
        ids = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError:
        raise ValueError(f"{path.name}을 읽을 수 없습니다.") from None
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError(f"{path.name}은 문서 ID 문자열의 배열이어야 합니다.")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path.name}에 중복된 문서 ID가 있습니다.")
    return ids


def _load_vectors(path: Path, rows: int, dimension: int) -> np.ndarray:
    import numpy as np

    try:
        vectors = np.load(path, mmap_mode="r", allow_pickle=False)
    except ValueError:
        raise ValueError(f"{path.name}을 벡터 배열로 읽을 수 없습니다.") from None
    if not isinstance(vectors, np.ndarray) or vectors.shape != (rows, dimension):
        raise ValueError(f"{path.name}의 행 수 또는 차원이 ID 매핑과 맞지 않습니다.")
    if vectors.dtype.kind not in "fiu" or not np.isfinite(vectors).all():
        raise ValueError(f"{path.name}에 유한한 실수 벡터가 필요합니다.")
    return vectors


def _as_float32(vectors: np.ndarray, filename: str) -> np.ndarray:
    import numpy as np

    with np.errstate(over="ignore", invalid="ignore"):
        converted = np.ascontiguousarray(vectors, dtype=np.float32)
    if not np.isfinite(converted).all():
        raise ValueError(f"{filename}에 float32로 표현할 수 없는 벡터 값이 있습니다.")
    return converted


def _read_selected(path: Path, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    documents = {}
    remaining = set(ids)
    for number, row in enumerate(_json_lines(path), 1):
        doc_id = row.get("_id", row.get("id")) if isinstance(row, dict) else None
        if not isinstance(doc_id, str):
            raise ValueError(f"{path.name}의 {number}번째 줄에 문자열 문서 ID(_id 또는 id)가 없습니다.")
        if doc_id not in remaining:
            continue
        text = row.get("text", row.get("contents"))
        if not isinstance(text, str):
            raise ValueError(f"{path.name}의 {number}번째 줄에 문자열 원문(text 또는 contents)이 없습니다.")
        documents[doc_id] = text
        remaining.remove(doc_id)
        if not remaining:
            break
    return documents


class TransferIndex:
    """Preserve the original positional ID mapping and FAISS ranking."""

    def __init__(self, root: Path, *, expanded: bool):
        import faiss

        self._data = root / "data"
        self._expanded = expanded
        self._index = faiss.read_index(str(self._data / "clean.index"))
        self._clean_count = self._index.ntotal
        clean_ids = _load_ids(self._data / "clean_ids.json")
        if len(clean_ids) != self._clean_count:
            raise ValueError("clean_ids.json의 문서 수가 기준 인덱스와 맞지 않습니다.")
        self._clean_ids = frozenset(clean_ids)
        self._ids = clean_ids

        questions = load_questions(root)
        self._questions = {question["qid"]: (row, question["text"]) for row, question in enumerate(questions)}
        self._query_vecs = _load_vectors(self._data / "query_vecs.npy", len(questions), self._index.d)

        self._additional_ids: frozenset[str] = frozenset()
        if expanded:
            extra_ids = _load_ids(self._data / "additional_ids.json")
            self._additional_ids = frozenset(extra_ids)
            if self._clean_ids & self._additional_ids:
                raise ValueError("기준과 추가 인덱스 간에 동일한 문서 ID가 있습니다.")
            vector_path = self._data / "addtional_vecs.npy"
            if not vector_path.is_file():
                vector_path = self._data / "additional_vecs.npy"
            extra = _load_vectors(vector_path, len(extra_ids), self._index.d)
            self._index.add(_as_float32(extra, vector_path.name))
            self._ids.extend(extra_ids)

        if self._index.ntotal != len(self._ids):
            raise ValueError("인덱스의 벡터 수가 문서 ID 매핑과 맞지 않습니다.")

    def search(self, qid: str, top_k: int = TOP_K) -> dict:
        """top_k: 기본 5. 부스 ON 백필은 6위 이후까지 받아간다 (미니 인덱스에 질의별 상위 100개가 있어 정확)."""
        if qid not in self._questions:
            raise ValueError("등록되지 않은 질문 ID입니다. queries.jsonl을 확인해 주세요.")
        row, question = self._questions[qid]
        query = _as_float32(self._query_vecs[row:row + 1], "query_vecs.npy")
        scores, positions = self._index.search(query, top_k)
        hits = []
        for rank, (score, position) in enumerate(zip(scores[0], positions[0]), 1):
            if position < 0:
                continue
            if position >= len(self._ids) or not isfinite(score):
                raise ValueError("검색 결과가 문서 ID 매핑 또는 점수 형식과 맞지 않습니다.")
            hits.append({
                "rank": rank,
                "doc_id": self._ids[position],
                "score": float(score),
                "additional": bool(position >= self._clean_count),
            })
        return {
            "qid": qid,
            "question": question,
            "mode": "expanded" if self._expanded else "baseline",
            "vector_count": self._index.ntotal,
            "top_k": top_k,
            "top5": hits,
        }

    def _document_sources(self, hits: list[dict]) -> list[tuple[Path, set[str]]]:
        ids = {hit["doc_id"] for hit in hits}
        sources = []
        clean_ids = ids & self._clean_ids
        if clean_ids:
            sources.append((self._data / "clean_docs.jsonl", clean_ids))
        extra_ids = ids & self._additional_ids
        if extra_ids:
            candidates = [self._data / "docs1.jsonl"]
            default_bundle = (APP_ROOT.parent / "rag_transfer").resolve()
            if self._data.parent.resolve() == default_bundle:
                candidates.append(APP_ROOT / "docs1.jsonl")
            candidates.append(self._data / "additional_docs.jsonl")
            extra_path = next((path for path in candidates if path.is_file()), None)
            if extra_path is not None:
                sources.append((extra_path, extra_ids))
        return sources

    def read_documents(self, hits: list[dict]) -> dict[str, str]:
        """Read ranked text only when explicitly requested for live generation."""
        documents = {}
        for path, ids in self._document_sources(hits):
            documents.update(_read_selected(path, ids))
        return documents

    def check_mapping(self, hits: list[dict]) -> dict:
        """Check the ranked ID-to-text join, returning metadata only."""
        matched = {
            doc_id: path.name
            for path, ids in self._document_sources(hits)
            for doc_id, text in _read_selected(path, ids).items()
            if text.strip()
        }
        matches = [
            {
                "rank": hit["rank"],
                "doc_id": hit["doc_id"],
                "additional": hit["additional"],
                "mapped": hit["doc_id"] in matched,
                "source_file": matched.get(hit["doc_id"]),
            }
            for hit in hits
        ]
        mapped_count = sum(match["mapped"] for match in matches)
        return {"mapped_count": mapped_count, "missing_text_count": len(matches) - mapped_count, "matches": matches}
