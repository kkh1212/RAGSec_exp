"""Deterministic structural units; every original character belongs to one unit."""
from dataclasses import dataclass
import re
from .patch import original_hash, canonicalize
from .types import Document, Patch

SEGMENT_VERSION = "punctuation-newline-block-v1"
_BOUNDARY = re.compile(r"(?:[。！？]+|[.!?]+(?:[^\S\r\n]+|(?=$))|\r\n|\r|\n)")


@dataclass(frozen=True, slots=True)
class Unit:
    doc_id: str
    field: str
    start_char: int
    end_char: int


def segment_document(doc: Document, max_chars: int = 240) -> tuple[Unit, ...]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    units = []
    for field in ("title", "text"):
        text = getattr(doc, field)
        # Title is a structural unit, with the same long-string fallback.
        ends = [] if field == "title" else [m.end() for m in _BOUNDARY.finditer(text)]
        if not ends or ends[-1] != len(text):
            ends.append(len(text))
        start = 0
        for end in ends:
            for block_start in range(start, end, max_chars):
                units.append(Unit(doc.doc_id, field, block_start, min(end, block_start + max_chars)))
            start = end
    return tuple(units)


def units_to_patches(documents: tuple[Document, ...], units: tuple[Unit, ...]) -> tuple[Patch, ...]:
    by_id = {d.doc_id: d for d in documents}
    return canonicalize(documents, tuple(Patch(u.doc_id, u.field,
        original_hash(getattr(by_id[u.doc_id], u.field)), u.start_char, u.end_char) for u in units))
