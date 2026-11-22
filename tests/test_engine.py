from dataclasses import FrozenInstanceError, asdict, replace
import inspect
import pytest
from rag_defense import *
from rag_defense.adapters.callable import CallableGenerator, CallableObserver, CallableVerifier
from rag_defense.adapters.mock import (build_scenario, generated, ANSWER, REFUSAL, UNCERTAIN,
                                      MockObserver, MockVerifier, OTHER)
from rag_defense.fingerprint import observation_key, digest
from rag_defense.observer import UnconfiguredObserver
from rag_defense.patch import apply_patches, changed_characters, full_document_patches


def engine_for(s, **kwargs):
    # These original regressions retain the recorded mask-only v1 schedule.
    kwargs.setdefault("config", DefenseConfig(search_version="v1", max_additional_generation_attempts=20))
    return DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier, **kwargs)


def test_clean_answer_has_no_edits_or_additional_generation(run_scenario):
    s, r = run_scenario("clean")
    assert r.status == DefenseStatus.NO_REPAIR_NEEDED
    assert r.final_response is s.request.initial_response
    assert not r.patches and not s.generator.calls and not s.verifier.calls
    assert r.usage.generation_attempts == 0


@pytest.mark.parametrize("policy", [QueryPolicy.DENY, QueryPolicy.UNKNOWN])
def test_disallowed_or_unknown_policy_never_calls_adapters(policy):
    s = build_scenario()
    r = engine_for(s).repair(replace(s.request, query_policy=policy))
    assert r.status == DefenseStatus.OUT_OF_SCOPE
    assert not s.generator.calls and not s.observer.calls and not s.verifier.calls


@pytest.mark.parametrize("status", [ExecutionStatus.API_ERROR, ExecutionStatus.TIMEOUT,
    ExecutionStatus.INPUT_TOO_LONG, ExecutionStatus.OUTPUT_TRUNCATED,
    ExecutionStatus.EMPTY_RESPONSE, ExecutionStatus.REPLAY_MISS])
def test_initial_execution_failure_is_not_a_semantic_refusal(status):
    s = build_scenario()
    initial = replace(s.request.initial_response, execution_status=status)
    r = engine_for(s).repair(replace(s.request, initial_response=initial))
    assert r.status == DefenseStatus.EXECUTION_ERROR
    assert r.final_response is initial and not s.observer.calls and not s.generator.calls
    assert all(t.observation is None for t in r.trace)


def test_empty_ok_response_is_an_execution_status():
    assert generated(" \n\t").execution_status == ExecutionStatus.EMPTY_RESPONSE


def test_unconfigured_observer_fails_closed():
    s = build_scenario()
    r = DefenseEngine(generator=s.generator).repair(s.request)
    assert r.status == DefenseStatus.OBSERVER_NOT_CONFIGURED
    assert not s.generator.calls and r.final_response is s.request.initial_response


def test_only_one_document_is_changed_and_inputs_are_immutable(run_scenario):
    s = build_scenario()
    before = asdict(s.request)
    r = engine_for(s).repair(s.request)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert r.candidate_documents == ("d2",)
    assert {p.doc_id for p in r.patches} == {"d2"}
    modified = apply_patches(s.request.documents, r.patches)
    assert modified[0] == s.request.documents[0] and modified[2] == s.request.documents[2]
    assert asdict(s.request) == before
    with pytest.raises(FrozenInstanceError):
        s.request.documents[0].text = "changed"


def test_document_probes_always_start_from_original(run_scenario):
    s, r = run_scenario()
    probes = [t for t in r.trace if t.phase == "document_probe"]
    assert len(probes) == len(s.request.documents)
    for event, call in zip(probes, s.generator.calls[1:4]):
        changed_ids = [d.doc_id for d, original in zip(call["documents"], s.request.documents) if d != original]
        assert len(changed_ids) == 1
        assert {p.doc_id for p in event.patches} == set(changed_ids)


def test_sentence_restoration_reduces_mask_and_preserves_other_sentences(run_scenario):
    s, r = run_scenario()
    full = full_document_patches(s.request.documents[1])
    assert 0 < r.changed_characters < changed_characters(full)
    modified = apply_patches(s.request.documents, r.patches)[1]
    assert modified.title == s.request.documents[1].title
    assert "첫째 기록.\n둘째 기록.\n" in modified.text
    assert any(t.reason == "accepted_confident_answer" for t in r.trace)


def test_interacting_regions_require_testing_both_halves(run_scenario):
    s, r = run_scenario("interaction")
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert {p.field for p in r.patches} == {"title", "text"}
    restore_events = [t for t in r.trace if t.phase == "restoration"]
    assert restore_events[0].reason == restore_events[1].reason == "rejected_without_confident_answer"
    assert any(t.reason == "accepted_confident_answer" for t in restore_events[2:])
    assert r.changed_characters < changed_characters(full_document_patches(s.request.documents[1]))


def test_title_is_part_of_the_search_surface(run_scenario):
    s, r = run_scenario("title")
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert {p.field for p in r.patches} == {"title"}
    assert apply_patches(s.request.documents, r.patches)[1].text == s.request.documents[1].text


def test_removing_necessary_evidence_can_leave_refusal(run_scenario):
    s, r = run_scenario("evidence_loss")
    assert r.status == DefenseStatus.NO_REPAIR_FOUND
    assert not r.candidate_documents and r.final_response is s.request.initial_response
    assert s.generator.calls[1]["documents"][0].text == "[REDACTED]"


@pytest.mark.parametrize("scenario,status", [("grounding_failure", VerificationStatus.FAIL),
                                            ("verifier_uncertain", VerificationStatus.UNCERTAIN)])
def test_answer_without_passing_grounding_is_never_returned(run_scenario, scenario, status):
    s, r = run_scenario(scenario)
    assert r.status == DefenseStatus.NO_VERIFIED_REPAIR
    assert r.verification_status == status
    assert r.candidate_patches and not r.patches and not r.intervention_applied
    assert r.final_response is s.request.initial_response
    assert r.gold_correctness is None


def test_missing_verifier_preserves_original_and_retains_candidate():
    s = build_scenario()
    r = DefenseEngine(generator=s.generator, observer=s.observer).repair(s.request)
    assert r.status == DefenseStatus.NO_VERIFIED_REPAIR
    assert r.verification_status == VerificationStatus.NOT_CONFIGURED
    assert r.best_candidate_patches and not r.patches
    assert r.final_response is s.request.initial_response


def test_unmodified_retry_recovery_has_no_document_attribution(run_scenario):
    s, r = run_scenario("retry")
    assert r.status == DefenseStatus.RETRY_RECOVERY
    assert not r.patches and not r.candidate_documents and not r.intervention_applied
    assert r.usage.generation_attempts == 1
    assert s.verifier.calls[-1][1] == r.final_response.text


def test_unstable_original_is_not_a_confirmed_intervention(run_scenario):
    s, r = run_scenario("unstable_baseline")
    assert r.status == DefenseStatus.UNSTABLE_BASELINE
    assert not r.patches and not s.verifier.calls
    assert r.final_response is s.request.initial_response


def test_uncertain_baseline_needs_confident_refusal_before_search(run_scenario):
    s, r = run_scenario("uncertain_baseline")
    assert r.status == DefenseStatus.UNSTABLE_BASELINE
    assert len(s.generator.calls) == 1
    assert not any(t.phase == "document_probe" for t in r.trace)


def test_uncertain_initial_can_be_confirmed_by_one_original_retry():
    s = build_scenario()
    request = replace(s.request, initial_response=generated(UNCERTAIN))
    r = engine_for(s).repair(request)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert [t for t in r.trace if t.phase == "initial"][0].observation.state == ResponseState.UNCERTAIN


def test_other_is_outside_refusal_repair():
    s = build_scenario()
    r = engine_for(s).repair(replace(s.request, initial_response=generated(OTHER)))
    assert r.status == DefenseStatus.OUT_OF_SCOPE and not s.generator.calls


def test_low_confidence_answer_is_not_a_recovery():
    s = build_scenario()
    s.observer.table[observation_key(s.request.query, ANSWER, s.observer.version)] = Observation(
        ResponseState.ANSWER, 0.5, s.observer.version)
    r = engine_for(s).repair(s.request)
    assert r.status == DefenseStatus.NO_REPAIR_FOUND and not r.patches


def test_verified_generation_object_is_exactly_returned_and_no_extra_call(run_scenario):
    s, r = run_scenario()
    final_event = [t for t in r.trace if t.phase == "final_patch"][-1]
    assert s.verifier.calls[-1][1] == r.final_response.text
    assert final_event.details["response_hash"] == digest(r.final_response.text)
    assert s.generator.calls[-1]["replicate_id"].startswith("deletion_control")
    final_call = next(call for call in s.generator.calls if call["replicate_id"] == final_event.replicate_id)
    assert r.final_response is s.generator.table[final_call["key"]][0]


@pytest.mark.parametrize("strict", [False, True])
def test_deletion_disagreement_is_recorded_and_optionally_rejects(run_scenario, strict):
    s, r = run_scenario("deletion_sensitive", require_deletion_consistency=strict)
    assert r.transformation_sensitive is True
    assert (r.status == DefenseStatus.PATCH_APPLIED) is (not strict)
    if strict:
        assert r.final_response is s.request.initial_response and not r.patches


def test_baseline_and_patch_final_controls_are_fresh(run_scenario):
    s, r = run_scenario()
    controls = [t for t in r.trace if t.phase in ("final_original", "final_patch", "deletion_control")]
    assert len(controls) == 3
    assert all(t.fresh and not t.cache_hit and t.replicate_id for t in controls)
    assert len({t.replicate_id for t in controls}) == 3


@pytest.mark.parametrize("field", ["gold_answer", "reference_answer", "clean_response", "attack_type",
                                  "attack_doc_id", "poisoned", "qrels"])
def test_evaluation_labels_are_rejected_at_request_boundary(field):
    s = build_scenario()
    with pytest.raises(TypeError):
        DefenseRequest(**{**{f: getattr(s.request, f) for f in s.request.__dataclass_fields__}, field: "sentinel"})


def test_callable_contracts_only_receive_the_permitted_arguments():
    s = build_scenario()
    seen = {"g": [], "o": [], "v": []}

    def generate(query, documents, *, fresh, replicate_id):
        seen["g"].append((query, documents))
        assert all(set(d.__dataclass_fields__) == {"doc_id", "title", "text", "rank", "retrieval_score"} for d in documents)
        return s.generator.generate(query, documents, fresh=fresh, replicate_id=replicate_id)

    def observe(query, response):
        seen["o"].append((query, response))
        assert isinstance(query, str) and isinstance(response, str)
        return s.observer.observe(query, response)

    def verify(query, response, evidence):
        seen["v"].append((query, response, evidence))
        assert all(type(span) is RemainingSpan for span in evidence)
        assert all("[REDACTED]" not in span.text for span in evidence)
        return s.verifier.verify(query, response, evidence)

    engine = DefenseEngine(
        generator=CallableGenerator(generate, fingerprint=s.generator.fingerprint, version=s.generator.version, mode="mock"),
        observer=CallableObserver(observe, version=s.observer.version, mode="mock"),
        verifier=CallableVerifier(verify, version=s.verifier.version, mode="mock"))
    r = engine.repair(s.request)
    assert r.status == DefenseStatus.PATCH_APPLIED and all(seen.values())
    masked = r.patches[0]
    assert not any(span.doc_id == masked.doc_id and span.field == masked.field and
                   span.start_char < masked.end_char and span.end_char > masked.start_char
                   for span in seen["v"][-1][2])


def test_external_callbacks_are_disabled_by_default():
    s = build_scenario()
    def forbidden(*args, **kwargs):
        raise AssertionError("must not be invoked")
    r = DefenseEngine(generator=CallableGenerator(forbidden, fingerprint=s.generator.fingerprint),
        observer=CallableObserver(forbidden), verifier=CallableVerifier(forbidden)).repair(s.request)
    assert r.status == DefenseStatus.OUT_OF_SCOPE and r.usage.generation_attempts == 0


def test_observer_exception_fails_closed_without_logging_its_message():
    s = build_scenario()
    def broken(query, response):
        raise RuntimeError("secret-sentinel")
    r = DefenseEngine(generator=s.generator, observer=CallableObserver(broken, mode="mock")).repair(s.request)
    assert r.status == DefenseStatus.EXECUTION_ERROR and not s.generator.calls
    assert "secret-sentinel" not in str(r.trace)


def test_verifier_exception_fails_closed_and_is_budgeted():
    s = build_scenario()
    def broken(query, response, evidence):
        raise TimeoutError("secret-sentinel")
    r = DefenseEngine(generator=s.generator, observer=s.observer,
        verifier=CallableVerifier(broken, mode="mock"), config=DefenseConfig(search_version="v1")).repair(s.request)
    assert r.status == DefenseStatus.NO_VERIFIED_REPAIR
    assert r.verification_status == VerificationStatus.ERROR
    assert r.usage.grounding_verifications == 2
    assert "secret-sentinel" not in str(r.trace)


def test_fresh_final_original_instability_rejects_an_answer_candidate():
    s = build_scenario("unstable_baseline")
    r = engine_for(s, config=DefenseConfig(search_version="v1", max_candidates_to_refine=0)).repair(s.request)
    assert r.status == DefenseStatus.UNSTABLE_BASELINE
    assert any(t.phase == "final_original" and t.observation.state == ResponseState.ANSWER for t in r.trace)
    assert not r.patches and not s.verifier.calls


def test_fresh_patch_must_reproduce_answer_even_after_successful_search():
    s = build_scenario()
    context = apply_patches(s.request.documents, full_document_patches(s.request.documents[1]))
    key = s.generator.key(s.request.query, context)
    s.generator.table[key] = (generated(ANSWER), generated(REFUSAL))
    r = engine_for(s, config=DefenseConfig(search_version="v1", max_candidates_to_refine=0)).repair(s.request)
    assert r.status == DefenseStatus.NO_VERIFIED_REPAIR
    assert any(t.phase == "final_patch" and t.fresh and t.observation.state == ResponseState.REFUSAL for t in r.trace)
    assert not r.patches and r.final_response is s.request.initial_response


@pytest.mark.parametrize("text,status", [(OTHER, ExecutionStatus.OK), (UNCERTAIN, ExecutionStatus.OK),
                                        ("", ExecutionStatus.TIMEOUT)])
def test_nonanswer_restorations_are_not_adopted(text, status):
    from rag_defense.segment import segment_document, units_to_patches
    s = build_scenario()
    units = segment_document(s.request.documents[1])
    attempted = units_to_patches(s.request.documents, units[len(units) // 2:])
    context = apply_patches(s.request.documents, attempted)
    s.generator.table[s.generator.key(s.request.query, context)] = (generated(text, status),)
    r = engine_for(s).repair(s.request)
    events = [t for t in r.trace if t.phase == "restoration" and t.patches == attempted]
    assert events and all(t.reason == "rejected_without_confident_answer" for t in events)
    assert r.usage.refinement_attempts <= 8


def test_deletion_execution_error_prevents_unverified_success():
    from rag_defense.patch import deletion_patches
    s = build_scenario()
    full = full_document_patches(s.request.documents[1])
    key = s.generator.key(s.request.query, apply_patches(s.request.documents, deletion_patches(full)))
    s.generator.table[key] = (generated("", ExecutionStatus.TIMEOUT),)
    r = engine_for(s, config=DefenseConfig(search_version="v1", max_candidates_to_refine=0)).repair(s.request)
    assert r.status == DefenseStatus.NO_VERIFIED_REPAIR
    assert not s.verifier.calls and not r.patches
