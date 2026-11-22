from dataclasses import replace
import pytest
from rag_defense import Document, Patch, Citation, VerificationResult, VerificationStatus
from rag_defense.patch import (original_hash, apply_patches, restore_originals, canonicalize,
                               remaining_spans, PatchError)
from rag_defense.segment import segment_document, units_to_patches
from rag_defense.verify import validate_citations


DOC = Document("u", "표제😀", "가나다\r\ne\u0301🙂 abc\n끝", 4)


def patch(start, end, field="text", replacement="[REDACTED]"):
    return Patch(DOC.doc_id, field, original_hash(getattr(DOC, field)), start, end, replacement)


def test_unicode_newlines_and_untouched_characters_round_trip():
    edits = (patch(2, 5), patch(0, 1, "title"))
    changed = apply_patches((DOC,), edits)
    assert changed[0].text == DOC.text[:2] + "[REDACTED]" + DOC.text[5:]
    assert changed[0].title == "[REDACTED]" + DOC.title[1:]
    assert changed[0].rank == DOC.rank
    assert restore_originals((DOC,), changed, edits) == (DOC,)
    assert apply_patches((DOC,), ()) == (DOC,)
    with pytest.raises(PatchError):
        restore_originals((DOC,), (replace(changed[0], text="unexpected"),), edits)


@pytest.mark.parametrize("start,end", [(-1, 2), (0, 99), (3, 2), (2, 2), (True, 3), (1.5, 3)])
def test_invalid_original_ranges_are_errors(start, end):
    with pytest.raises(PatchError):
        apply_patches((DOC,), (patch(start, end),))


def test_hash_mismatch_unknown_field_and_unknown_document():
    for p in (replace(patch(0, 1), original_hash="wrong"), replace(patch(0, 1), field="metadata"),
              replace(patch(0, 1), doc_id="absent")):
        with pytest.raises(PatchError):
            apply_patches((DOC,), (p,))


def test_overlaps_rejected_adjacent_equal_replacements_merged():
    with pytest.raises(PatchError):
        canonicalize((DOC,), (patch(1, 4), patch(3, 5)))
    with pytest.raises(PatchError):
        canonicalize((DOC,), (patch(1, 4), patch(1, 4)))
    merged = canonicalize((DOC,), (patch(3, 5), patch(1, 3)))
    assert merged == (patch(1, 5),)
    assert apply_patches((DOC,), merged)[0].text.count("[REDACTED]") == 1
    assert len(canonicalize((DOC,), (patch(1, 3), patch(3, 5, replacement="")))) == 2


def test_title_and_text_coordinate_spaces_do_not_overlap():
    assert len(canonicalize((DOC,), (patch(0, 2), patch(0, 2, field="title")))) == 2


def test_segmentation_is_deterministic_complete_and_bounded():
    doc = Document("d", "긴표제" * 9, "문장.\r\nSecond! Next?\n" + "x" * 41 + " e\u0301", 1)
    units = segment_document(doc, 8)
    assert units == segment_document(doc, 8)
    for field in ("title", "text"):
        selected = [u for u in units if u.field == field]
        assert all(0 < u.end_char - u.start_char <= 8 for u in selected)
        assert "".join(getattr(doc, field)[u.start_char:u.end_char] for u in selected) == getattr(doc, field)
        assert all(a.end_char == b.start_char for a, b in zip(selected, selected[1:]))
    assert apply_patches((doc,), units_to_patches((doc,), units))[0].text == "[REDACTED]"


@pytest.mark.parametrize("citation", [Citation("x", "text", 3, 6, "DEF"),
    Citation("x", "text", 0, 9, "abcghi"), Citation("x", "text", 3, 13, "[REDACTED]"),
    Citation("x", "text", 0, 9, "abcDEFghi")])
def test_removed_or_disconnected_or_replacement_citations_are_rejected(citation):
    doc = Document("x", "", "abcDEFghi", 0)
    edit = Patch("x", "text", original_hash(doc.text), 3, 6)
    spans = remaining_spans((doc,), (edit,))
    v = VerificationResult("PASS", True, True, (citation,), "test-v1")
    assert validate_citations(v, spans).status == VerificationStatus.FAIL


def test_valid_original_citation_and_semantic_checks_are_both_required():
    doc = Document("x", "", "abcDEFghi", 0)
    spans = remaining_spans((doc,), (Patch("x", "text", original_hash(doc.text), 3, 6),))
    citation = Citation("x", "text", 6, 9, "ghi")
    valid = VerificationResult("PASS", True, True, (citation,), "test-v1")
    assert validate_citations(valid, spans).status == VerificationStatus.PASS
    for invalid in (replace(valid, supported=False), replace(valid, addresses_query=None), replace(valid, citations=())):
        assert validate_citations(invalid, spans).status == VerificationStatus.FAIL


def test_masked_whitespace_cannot_be_bridged_by_a_quote():
    doc = Document("x", "", "left right", 0)
    edit = Patch("x", "text", original_hash(doc.text), 4, 5, "")
    citation = Citation("x", "text", 0, 10, "leftright")
    result = VerificationResult("PASS", True, True, (citation,))
    assert validate_citations(result, remaining_spans((doc,), (edit,))).status == VerificationStatus.FAIL
