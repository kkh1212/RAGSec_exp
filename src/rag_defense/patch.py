"""Original-coordinate patches: no normalization and no mutation of Documents."""
from dataclasses import replace
import hashlib
from .types import Document, Patch, RemainingSpan, Operation
from .fingerprint import digest

REDACTION = "[REDACTED]"


class PatchError(ValueError):
    pass


def original_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonicalize(documents: tuple[Document, ...], patches: tuple[Patch, ...]) -> tuple[Patch, ...]:
    """Reject overlaps (including duplicates); merge touching equal replacements."""
    by_id = {d.doc_id: d for d in documents}
    if len(by_id) != len(documents):
        raise PatchError("duplicate document IDs")
    order = {d.doc_id: i for i, d in enumerate(documents)}
    dropped = set()
    for p in patches:
        if p.operation == Operation.DROP_DOCUMENT:
            doc = by_id.get(p.doc_id)
            if (doc is None or p.field != "document" or p.original_hash != digest([doc.title, doc.text])
                    or type(p.start_char) is not int or type(p.end_char) is not int
                    or p.start_char != 0 or p.end_char != len(doc.title) + len(doc.text)
                    or p.replacement != "" or p.doc_id in dropped):
                raise PatchError("invalid document removal")
            dropped.add(p.doc_id)
            continue
        if p.doc_id not in by_id or p.field not in ("title", "text"):
            raise PatchError("unknown document or field")
        original = getattr(by_id[p.doc_id], p.field)
        if p.original_hash != original_hash(original):
            raise PatchError("original hash mismatch")
        if (type(p.start_char) is not int or type(p.end_char) is not int or
                not 0 <= p.start_char < p.end_char <= len(original)):
            raise PatchError("invalid original character range")
        if not isinstance(p.replacement, str):
            raise PatchError("replacement must be a string")
        if p.operation == Operation.DROP_SPAN and p.replacement != "":
            raise PatchError("DROP_SPAN requires empty replacement")
    if any(p.doc_id in dropped and p.operation != Operation.DROP_DOCUMENT for p in patches):
        raise PatchError("document removal cannot overlap another edit")
    result: list[Patch] = []
    for p in sorted(patches, key=lambda x: (order[x.doc_id], 0 if x.field == "title" else 1, x.start_char)):
        if result and (result[-1].doc_id, result[-1].field) == (p.doc_id, p.field):
            last = result[-1]
            if p.start_char < last.end_char:
                raise PatchError("overlapping patches are not allowed")
            if p.start_char == last.end_char and p.replacement == last.replacement and p.operation == last.operation:
                result[-1] = replace(last, end_char=p.end_char)
                continue
        result.append(p)
    return tuple(result)


def apply_patches(documents: tuple[Document, ...], patches: tuple[Patch, ...]) -> tuple[Document, ...]:
    patches = canonicalize(documents, patches)
    result = []
    for doc in documents:
        if any(p.doc_id == doc.doc_id and p.operation == Operation.DROP_DOCUMENT for p in patches):
            continue
        updates = {}
        for field in ("title", "text"):
            original = getattr(doc, field)
            pieces, cursor = [], 0
            for p in patches:
                if (p.doc_id, p.field) == (doc.doc_id, field):
                    pieces.extend((original[cursor:p.start_char], p.replacement))
                    cursor = p.end_char
            pieces.append(original[cursor:])
            updates[field] = "".join(pieces)
        result.append(replace(doc, **updates))
    return tuple(result)


def restore_originals(originals: tuple[Document, ...], modified: tuple[Document, ...],
                      patches: tuple[Patch, ...]) -> tuple[Document, ...]:
    """Restoration requires the immutable originals, never guesses deleted text."""
    if apply_patches(originals, patches) != modified:
        raise PatchError("modified documents do not match this patch set")
    return tuple(originals)


def remaining_spans(documents: tuple[Document, ...], patches: tuple[Patch, ...]) -> tuple[RemainingSpan, ...]:
    patches = canonicalize(documents, patches)
    spans = []
    for doc in documents:
        if any(p.doc_id == doc.doc_id and p.operation == Operation.DROP_DOCUMENT for p in patches):
            continue
        for field in ("title", "text"):
            original, cursor = getattr(doc, field), 0
            for p in patches:
                if (p.doc_id, p.field) != (doc.doc_id, field):
                    continue
                if cursor < p.start_char:
                    spans.append(RemainingSpan(doc.doc_id, field, cursor, p.start_char, original[cursor:p.start_char]))
                cursor = p.end_char
            if cursor < len(original):
                spans.append(RemainingSpan(doc.doc_id, field, cursor, len(original), original[cursor:]))
    return tuple(spans)


def full_document_patches(doc: Document, operation=Operation.MASK_SPAN) -> tuple[Patch, ...]:
    if operation == Operation.DROP_DOCUMENT:
        return (Patch(doc.doc_id, "document", digest([doc.title, doc.text]), 0,
                      len(doc.title) + len(doc.text), "", operation),)
    return tuple(Patch(doc.doc_id, field, original_hash(value), 0, len(value), REDACTION, operation)
                 for field in ("title", "text") if (value := getattr(doc, field)))


def changed_characters(patches: tuple[Patch, ...]) -> int:
    return sum(p.end_char - p.start_char for p in patches)


def deletion_patches(patches: tuple[Patch, ...]) -> tuple[Patch, ...]:
    return tuple(replace(p, replacement="", operation=Operation.DROP_SPAN)
                 if p.operation != Operation.DROP_DOCUMENT else p for p in patches)
