from dataclasses import replace
import pytest
from rag_defense import *
from rag_defense.adapters.mock import build_scenario, generated, REFUSAL, ANSWER
from rag_defense.adapters.callable import CallableGenerator
from rag_defense.budget import Budget, BudgetExceeded
from rag_defense.fingerprint import generation_key, make_generation_fingerprint
from rag_defense.patch import full_document_patches
from rag_defense.probe import ProbeRunner


@pytest.mark.parametrize("limit", [0, 1, 2, 3, 4, 5, 6])
def test_budget_stops_before_overrun_and_never_returns_unverified_patch(limit):
    s = build_scenario()
    r = DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier,
        config=DefenseConfig(max_additional_generation_attempts=limit)).repair(s.request)
    assert r.usage.generation_attempts <= limit
    assert r.usage.generation_attempts == len(s.generator.calls)
    if r.status != DefenseStatus.PATCH_APPLIED:
        assert r.final_response is s.request.initial_response and not r.patches
    else:
        assert r.verification_status == VerificationStatus.PASS


def test_final_calls_are_reserved_even_if_refinement_is_cut_short(run_scenario):
    s, r = run_scenario(max_additional_generation_attempts=7)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert r.usage.generation_attempts == 7 and r.search_truncated
    assert [t.phase for t in r.trace if t.fresh][-3:] == ["final_original", "final_patch", "deletion_control"]


def test_no_grounding_budget_means_no_success(run_scenario):
    s, r = run_scenario(max_grounding_verifications=0)
    assert r.status == DefenseStatus.BUDGET_EXHAUSTED
    assert r.usage.grounding_verifications == 0 and not s.verifier.calls
    assert not r.patches


def test_multiple_candidates_and_fallbacks_share_one_budget():
    s = build_scenario("grounding_failure")
    patches = full_document_patches(s.request.documents[2])
    from rag_defense.patch import apply_patches
    key = s.generator.key(s.request.query, apply_patches(s.request.documents, patches))
    s.generator.table[key] = (generated(ANSWER),)
    r = DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier,
        config=DefenseConfig(max_additional_generation_attempts=13, max_refinement_attempts=2)).repair(s.request)
    assert set(r.candidate_documents) == {"d2", "d3"}
    assert r.candidate_documents[0] == "d3"  # fewer original characters
    assert r.usage.generation_attempts <= 13 and r.usage.refinement_attempts <= 2
    assert r.usage.grounding_verifications <= 2 and not r.patches


def test_network_retries_are_counted_and_logged_as_fresh_attempts():
    s = build_scenario()
    key = s.generator.key(s.request.query, s.request.documents)
    s.generator.table[key] = (generated("", ExecutionStatus.TIMEOUT), generated(REFUSAL))
    r = DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier,
        config=DefenseConfig(search_version="v1", max_execution_retries=1)).repair(s.request)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert r.usage.generation_attempts == len(s.generator.calls) == 11
    retry_events = [t for t in r.trace if t.details.get("retry_index") == 1]
    assert len(retry_events) == 1 and retry_events[0].fresh
    assert any(t.execution_status == ExecutionStatus.TIMEOUT and t.observation is None for t in r.trace)


def test_thrown_request_exception_consumes_one_attempt_and_message_is_not_logged():
    s = build_scenario()
    calls = []
    def broken(query, documents, *, fresh, replicate_id):
        calls.append(1)
        raise TimeoutError("private-key-sentinel")
    r = DefenseEngine(generator=CallableGenerator(broken, fingerprint=s.generator.fingerprint, mode="mock"),
        observer=s.observer, verifier=s.verifier,
        config=DefenseConfig(max_execution_retries=4, max_additional_generation_attempts=2)).repair(s.request)
    assert len(calls) == r.usage.generation_attempts == 2
    assert r.status == DefenseStatus.BUDGET_EXHAUSTED
    assert "private-key-sentinel" not in str(r.trace)


def test_adapter_must_not_report_hidden_retries():
    with pytest.raises(ValueError):
        TokenUsage(provider_attempts=2)


def test_cache_hits_do_not_consume_attempts_and_fresh_bypasses_cache():
    s = build_scenario()
    config = DefenseConfig()
    budget = Budget(config)
    trace = []
    runner = ProbeRunner(s.request, s.generator, s.observer, config, budget, trace)
    patches = full_document_patches(s.request.documents[1])
    a = runner.run(patches, "test")
    b = runner.run(patches, "test")
    c = runner.run(patches, "test", fresh=True, replicate_id="independent-1")
    assert a.generation == b.generation == c.generation
    assert len(s.generator.calls) == budget.generation_attempts == 2
    assert budget.cache_hits == 1
    assert [t.cache_hit for t in trace] == [False, True, False]
    assert trace[-1].fresh and trace[-1].replicate_id == "independent-1"


def test_cache_fingerprints_cover_content_order_config_version_and_repetition():
    s = build_scenario()
    q, docs, fp, version = s.request.query, s.request.documents, s.generator.fingerprint, s.generator.version
    keys = {
        generation_key(q, docs, fp, version),
        generation_key(q + " ", docs, fp, version),
        generation_key(q, docs[::-1], fp, version),
        generation_key(q, (replace(docs[0], title=docs[0].title + " "),) + docs[1:], fp, version),
        generation_key(q, (replace(docs[0], text=docs[0].text + "\n"),) + docs[1:], fp, version),
        generation_key(q, docs, fp + "new-prompt", version),
        generation_key(q, docs, fp, version + "-new"),
        generation_key(q, docs, fp, version, fresh=True, replicate_id="1"),
        generation_key(q, docs, fp, version, fresh=True, replicate_id="2"),
    }
    assert len(keys) == 9
    params = dict(model="m", endpoint="e", prompt="p", settings={"temperature": 0}, renderer_version="v")
    assert make_generation_fingerprint(**params) != make_generation_fingerprint(**{**params, "prompt": "p2"})
    assert make_generation_fingerprint(**params) != make_generation_fingerprint(**{**params, "model": "m2"})
    assert make_generation_fingerprint(**params) != make_generation_fingerprint(**{**params, "endpoint": "e2"})


@pytest.mark.parametrize("where", ["initial", "returned", "adapter"])
def test_configuration_fingerprint_change_aborts_comparison(where):
    s = build_scenario()
    request = s.request
    if where == "initial":
        request = replace(request, initial_response=replace(request.initial_response, generation_fingerprint="different"))
    elif where == "adapter":
        s.generator.fingerprint = "different"
    else:
        key = s.generator.key(request.query, request.documents)
        s.generator.table[key] = (replace(generated(REFUSAL), generation_fingerprint="different"),)
    r = DefenseEngine(generator=s.generator, observer=s.observer, verifier=s.verifier).repair(request)
    assert r.status == DefenseStatus.EXECUTION_ERROR and not r.patches
    assert len(s.generator.calls) == (1 if where == "returned" else 0)
    assert any(t.execution_status == ExecutionStatus.CONFIG_MISMATCH for t in r.trace)


def test_full_document_fallback_can_pass_when_small_candidate_is_not_grounded():
    s = build_scenario()
    from rag_defense.adapters.callable import CallableVerifier
    def verify(query, response, evidence):
        if any(span.doc_id == "d2" for span in evidence):
            return VerificationResult("FAIL", False, False, verifier_version=s.verifier.version)
        return s.verifier.verify(query, response, evidence)
    r = DefenseEngine(generator=s.generator, observer=s.observer,
        verifier=CallableVerifier(verify, version=s.verifier.version, mode="mock"),
        config=DefenseConfig(search_version="v1")).repair(s.request)
    assert r.status == DefenseStatus.PATCH_APPLIED
    assert r.usage.grounding_verifications == 2
    assert r.patches == full_document_patches(s.request.documents[1])
    assert r.best_candidate_patches != r.patches
