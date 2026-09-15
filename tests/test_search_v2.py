"""Synthetic algorithm checks. Hidden causes stay inside the fixture callback."""
from dataclasses import asdict, replace

import pytest

from rag_defense import (DefenseConfig, DefenseEngine, DefenseRequest, DefenseStatus,
    Document, GenerationResult, Observation, QueryPolicy, Citation, VerificationResult)
from rag_defense.adapters.callable import CallableGenerator, CallableObserver, CallableVerifier
from rag_defense import Operation, ExecutionStatus
from rag_defense.patch import apply_patches, remaining_spans, full_document_patches, PatchError
from rag_defense.fingerprint import digest
from rag_defense.adapters.synthetic_v2 import build_v2_scenario


def fixture(rule, documents=None, verifier_rule=None):
    documents = documents or (
        Document("e", "", "The recorded value is seven.", 0),
        Document("x", "", "opaque sequence 481", 1),
    )
    calls, verified = [], []
    def generate(query, contexts, *, fresh, replicate_id):
        calls.append((query, contexts, fresh, replicate_id))
        return GenerationResult(rule(contexts), "OK", "fixed-v2-fixture")
    def observe(query, response):
        return Observation({"answer": "ANSWER", "wrong": "ANSWER", "refusal": "REFUSAL"}[response], 1, "observer")
    def verify(query, response, evidence):
        verified.append((response, evidence))
        support = next((s for s in evidence if "seven" in s.text), None)
        passed = response == "answer" and support is not None
        if verifier_rule:
            passed = passed and verifier_rule(response, evidence)
        citations = (Citation(support.doc_id, support.field, support.start_char, support.end_char, support.text),) if passed else ()
        return VerificationResult("PASS" if passed else "FAIL", passed, passed, citations, "verifier")
    request = DefenseRequest("r", "What value was recorded?", documents,
        GenerationResult("refusal", "OK", "fixed-v2-fixture"), "fixed-v2-fixture", QueryPolicy.ALLOW)
    engine = DefenseEngine(generator=CallableGenerator(generate, fingerprint=request.generation_fingerprint, mode="mock"),
        observer=CallableObserver(observe, version="observer", mode="mock"),
        verifier=CallableVerifier(verify, version="verifier", mode="mock"))
    return engine, request, calls, verified


def test_drop_only_recovery_does_not_require_mask_success():
    engine, request, calls, verified = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal")
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert result.transformation_sensitive is True
    assert any(len(c[1]) == 1 for c in calls)
    assert verified[-1][0] == result.final_response.text


def test_partial_search_survives_failed_whole_document_probes():
    docs = (Document("e", "", "The recorded value is seven. opaque sequence 481.", 0),)
    engine, request, _, _ = fixture(lambda ds: "answer" if ds and "seven" in ds[0].text and "481" not in ds[0].text else "refusal", docs)
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert result.changed_characters < len(docs[0].text)


def test_pair_search_when_neither_single_edit_works():
    docs = (Document("e", "", "The recorded value is seven.", 0),
            Document("x", "", "opaque 481", 1), Document("y", "", "opaque 592", 2))
    engine, request, _, _ = fixture(lambda ds: "answer" if any("seven" in d.text for d in ds)
        and not any("opaque" in d.text for d in ds) else "refusal", docs)
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert {p.doc_id for p in result.patches} == {"x", "y"}


def test_drop_operation_is_structural_and_original_spans_are_removed():
    engine, request, _, _ = fixture(lambda ds: "refusal")
    doc = request.documents[1]
    patches = full_document_patches(doc, Operation.DROP_DOCUMENT)
    assert apply_patches(request.documents, patches) == request.documents[:1]
    assert all(s.doc_id != doc.doc_id for s in remaining_spans(request.documents, patches))
    assert patches[0].operation == Operation.DROP_DOCUMENT
    with pytest.raises(PatchError):
        apply_patches(request.documents, patches + full_document_patches(doc))


def test_drop_empty_document_still_removes_slot():
    doc = Document("empty", "", "", 0)
    assert apply_patches((doc,), full_document_patches(doc, Operation.DROP_DOCUMENT)) == ()


def test_drop_only_refinement_does_not_assume_empty_slot_equals_removal():
    engine, request, calls, _ = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal")
    result = engine.repair(request)
    assert all(p.operation == Operation.DROP_DOCUMENT for p in result.patches)
    attempts = [t for t in result.trace if t.phase == "drop_representation_probe"]
    assert attempts and attempts[0].observation.state.value == "REFUSAL"
    assert any(len(c[1]) == 2 and c[1][1].text == "" for c in calls)


def test_unpunctuated_cause_is_found_without_dictionary_or_instruction():
    scenario = build_v2_scenario("unpunctuated")
    result = DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier).repair(scenario.request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert any(p.start_char == 240 and p.end_char == 480 for p in result.patches)
    assert any(t.phase == "partial_probe" for t in result.trace)


@pytest.mark.parametrize("policy", ["DENY", "UNKNOWN"])
def test_legitimate_disallowed_query_is_not_repaired(policy):
    engine, request, calls, verified = fixture(lambda ds: "answer")
    request = replace(request, query="A dangerous request", query_policy=policy)
    result = engine.repair(request)
    assert result.status == DefenseStatus.OUT_OF_SCOPE
    assert result.final_response is request.initial_response and not calls and not verified


def test_insufficient_evidence_refusal_stays_refusal():
    engine, request, _, verified = fixture(lambda ds: "refusal")
    result = engine.repair(request)
    assert not result.patches and not result.grounding_passed and not verified
    assert result.final_response is request.initial_response


def test_wrong_nonrefusal_answer_fails_grounding_and_exhausts_verification_budget():
    engine, request, _, verified = fixture(lambda ds: "wrong" if len(ds) == 1 else "refusal")
    result = engine.repair(request)
    assert result.status not in (DefenseStatus.PATCH_APPLIED, DefenseStatus.RETRY_RECOVERY)
    assert not result.grounding_passed and result.final_response is request.initial_response
    assert verified and result.verification_status.value == "FAIL"


def test_policy_note_on_normal_document_is_not_a_maliciousness_label():
    scenario = build_v2_scenario("policy_note")
    result = DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier).repair(scenario.request)
    assert result.status == DefenseStatus.NO_REPAIR_NEEDED
    assert result.usage.generation_attempts == 0 and not result.candidate_documents
    assert not hasattr(result, "attack")


def test_retry_recovery_remains_separate_from_edit_attribution():
    engine, request, calls, verified = fixture(lambda ds: "answer")
    result = engine.repair(request)
    assert result.status == DefenseStatus.RETRY_RECOVERY
    assert len(calls) == 1 and not result.patches and not result.candidate_documents
    assert verified[-1][0] == result.final_response.text


def test_failed_grounding_resumes_independent_partial_search():
    docs = (Document("e", "", "The recorded value is seven.", 0),
            Document("x", "", "Necessary normal detail. opaque 481.", 1))
    def rule(ds):
        if any("opaque" in d.text for d in ds):
            return "refusal"
        return "answer" if any("Necessary" in d.text for d in ds) else "wrong"
    engine, request, _, verified = fixture(rule, docs)
    engine.config = DefenseConfig(max_candidates_to_refine=0)
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert result.usage.grounding_verifications == 2
    assert [r for r, _ in verified] == ["wrong", "answer"]
    assert any(t.phase == "search_resume" and t.details["phase"] == "partial_probe" for t in result.trace)
    assert result.final_response.text == verified[-1][0]


def test_two_document_drop_only_preserves_operation_and_order():
    docs = (Document("x", "", "opaque 481", 8), Document("e", "", "The recorded value is seven.", 2), Document("y", "", "opaque 592", 0))
    engine, request, calls, _ = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal", docs)
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    assert {p.operation for p in result.patches} == {Operation.DROP_DOCUMENT}
    assert {p.doc_id for p in result.patches} == {"x", "y"}
    assert apply_patches(docs, result.patches) == (docs[1],)
    assert result.transformation_sensitive is True


def test_every_probe_starts_from_original_across_documents_and_requests():
    engine, request, calls, _ = fixture(lambda ds: "refusal")
    before = asdict(request)
    first = engine.repair(request)
    second = engine.repair(replace(request, request_id="new", query="What value was recorded again?"))
    assert before == asdict(request)
    for result in (first, second):
        actual = [c for c in calls if c[0] == (request.query if result is first else "What value was recorded again?")]
        events = [t for t in result.trace if t.reason == "generation_attempt"]
        assert len(events) == len(actual)
        for event, call in zip(events, actual):
            assert call[1] == apply_patches(request.documents, event.patches)
            assert len({p.doc_id for p in event.patches}) <= 2


def test_partial_schedule_round_robin_keeps_failed_parent_children():
    engine, request, _, _ = fixture(lambda ds: "refusal", tuple(Document(str(i), "", "First sentence. Second sentence.", i) for i in range(3)))
    engine.config = DefenseConfig(max_partial_probes=12, enable_pairs=False)
    result = engine.repair(request)
    events = [t for t in result.trace if t.phase == "partial_probe" and t.reason == "generation_attempt"]
    # Full mask duplicate may be skipped, then slot-preserving deletion and child intervals.
    assert {t.patches[0].doc_id for t in events[:3]} == {"0", "1", "2"}
    assert any(t.patches[0].end_char < len(request.documents[0].text) for t in events)
    keys = [t.request_fingerprint for t in result.trace if t.reason == "generation_attempt" and not t.fresh]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("limit", [0, 1, 4, 5, 8, 12, 20, 48])
def test_v2_budget_and_unexplored_scope_never_overclaim(limit):
    engine, request, calls, _ = fixture(lambda ds: "refusal", tuple(Document(str(i), "Title", "x"*1000, i) for i in range(5)))
    engine.config = DefenseConfig(max_additional_generation_attempts=limit)
    result = engine.repair(request)
    assert result.usage.generation_attempts == len(calls) <= limit
    assert result.usage.partial_probes <= 12 and result.usage.pair_probes <= 20
    assert not result.search_complete
    if limit > 1:
        assert result.unexplored
        assert any(section.get("untested_count", 0) > 0 for section in result.unexplored)
    assert result.termination_reason


def test_four_generation_calls_reserved_and_returned_response_is_fresh_verified():
    engine, request, _, verified = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal")
    engine.config = DefenseConfig(max_additional_generation_attempts=10, max_candidates_to_refine=0)
    result = engine.repair(request)
    assert result.status == DefenseStatus.PATCH_APPLIED
    final_events = [t for t in result.trace if t.phase in ("final_original", "final_patch")]
    assert all(t.fresh and not t.cache_hit for t in final_events)
    assert final_events[-1].details["response_hash"] == digest(result.final_response.text)
    assert result.final_response.text == verified[-1][0]
    assert DefenseConfig().final_generation_reserve >= 4


def test_no_verifier_never_returns_candidate_response():
    engine, request, _, _ = fixture(lambda ds: "answer" if len(ds) == 1 else "refusal")
    from rag_defense.verify import UnconfiguredVerifier
    engine.verifier = UnconfiguredVerifier()
    result = engine.repair(request)
    assert result.verification_status.value == "NOT_CONFIGURED"
    assert result.final_response is request.initial_response and not result.patches


def test_fixture_hidden_cause_is_never_read_by_engine():
    engine, request, _, _ = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal")
    underlying = engine.generator
    class Guarded:
        def __getattribute__(self, name):
            if name not in {"fingerprint", "version", "mode", "generate"}:
                raise AssertionError("engine attempted to inspect hidden fixture state: " + name)
            return getattr(underlying, name)
    engine.generator = Guarded()
    assert engine.repair(request).status == DefenseStatus.PATCH_APPLIED


def test_final_verification_budget_stops_instead_of_returning_unverified_answer():
    engine, request, calls, verified = fixture(lambda ds: "wrong" if len(ds) < 2 else "refusal")
    engine.config = DefenseConfig(max_grounding_verifications=1)
    result = engine.repair(request)
    assert len(verified) == 1
    assert result.status == DefenseStatus.BUDGET_EXHAUSTED
    assert result.termination_reason == "grounding_verification_budget_exhausted"
    assert result.final_response is request.initial_response


def test_candidate_metadata_preserves_original_and_generation_fingerprints():
    engine, request, _, _ = fixture(lambda ds: "answer" if len(ds) == 1 and ds[0].doc_id == "e" else "refusal")
    result = engine.repair(request)
    detail = result.candidate_details[0]
    assert detail["original_fingerprint"] == result.original_request_fingerprint
    assert detail["generation_fingerprint"] == request.generation_fingerprint
    assert detail["operations"] == ["DROP_DOCUMENT"] and detail["observed_state"] == "ANSWER"


def test_zero_budget_still_records_unexplored_document_partial_pair_ranges():
    engine, request, _, _ = fixture(lambda ds: "refusal")
    engine.config = DefenseConfig(max_additional_generation_attempts=0)
    result = engine.repair(request)
    assert {"document_probe", "partial_probe", "pair_probe"} <= {item["phase"] for item in result.unexplored}
    assert result.usage.generation_attempts == 0 and not result.search_complete


def test_large_pair_scope_is_reported_without_allocating_all_combinations():
    engine, request, _, _ = fixture(lambda ds: "refusal", tuple(Document(str(i), "", "text", i) for i in range(100)))
    engine.config = DefenseConfig(max_additional_generation_attempts=0)
    result = engine.repair(request)
    bound = next(item for item in result.unexplored if item["reason"] == "schedule_materialization_bound")
    assert bound["schedule_index_range"] == [20, 9900]
    assert bound["untested_count"] == 9880 and not result.search_complete
