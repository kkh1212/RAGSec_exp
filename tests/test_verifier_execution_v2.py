import json
from dataclasses import replace
import pytest

from rag_defense import (ExecutionStatus, DefenseEngine, DefenseStatus, GenerationResult, RemainingSpan,
                         DefenseConfig, QueryPolicy)
from rag_defense.adapters.execution import classify_execution, ProviderExecutionError
from rag_defense.adapters.final_llm import FinalLLMConfig, FinalLLMVerifier, parse_verdict
from rag_defense.adapters.mock import build_scenario
from rag_defense.adapters.callable import CallableGenerator


@pytest.mark.parametrize("kwargs,expected", [
    ({"http_status": 400}, "OTHER_API_ERROR"),
    ({"http_status": 400, "error_code": "invalid_request"}, "OTHER_API_ERROR"),
    ({"http_status": 400, "error_code": "content_filter"}, "PROVIDER_BLOCK"),
    ({"http_status": 403, "error_code": "content_filter"}, "PROVIDER_BLOCK"),
    ({"finish_reason": "content_filter"}, "PROVIDER_BLOCK"),
    ({"structured_block": True}, "PROVIDER_BLOCK"),
    ({"http_status": 401}, "AUTH_ERROR"), ({"http_status": 403}, "AUTH_ERROR"),
    ({"http_status": 429}, "RATE_LIMIT"), ({"timed_out": True}, "TIMEOUT"),
    ({"error_code": "context_length_exceeded"}, "INPUT_TOO_LONG"),
    ({"finish_reason": "length", "text": "partial"}, "INCOMPLETE"),
    ({"text": ""}, "INCOMPLETE"), ({"text": "I cannot answer for safety reasons."}, "OK"),
])
def test_structured_status_mapping_never_infers_block_from_refusal_text(kwargs, expected):
    assert classify_execution(**kwargs).value == expected


@pytest.mark.parametrize("status", ["PROVIDER_BLOCK", "RATE_LIMIT", "AUTH_ERROR", "TIMEOUT", "INPUT_TOO_LONG", "INCOMPLETE", "OTHER_API_ERROR"])
def test_initial_operational_status_is_kept_without_repair(status):
    scenario = build_scenario()
    request = replace(scenario.request, initial_response=replace(scenario.request.initial_response, execution_status=status))
    result = DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier).repair(request)
    assert result.status == DefenseStatus.EXECUTION_ERROR
    assert not scenario.generator.calls and not scenario.observer.calls
    assert result.final_response is request.initial_response


@pytest.mark.parametrize("status", ["PROVIDER_BLOCK", "RATE_LIMIT", "AUTH_ERROR"])
def test_operational_stop_is_not_retried_or_bypassed(status):
    scenario = build_scenario()
    calls = []
    def generate(query, documents, *, fresh, replicate_id):
        calls.append(documents)
        raise ProviderExecutionError(status)
    engine = DefenseEngine(generator=CallableGenerator(generate, fingerprint=scenario.request.generation_fingerprint, mode="mock"),
        observer=scenario.observer, verifier=scenario.verifier, config=DefenseConfig(max_execution_retries=5))
    result = engine.repair(scenario.request)
    assert len(calls) == result.usage.generation_attempts == 1
    assert result.termination_reason == status and not result.patches


SPAN = RemainingSpan("d", "text", 5, 10, "seven")


def verdict(**overrides):
    return json.dumps({"status": "PASS", "addresses_query": True, "supported": True,
        "citations": [{"doc_id": "d", "field": "text", "start_char": 5, "end_char": 10, "quote": "seven"}], **overrides})


def test_final_verifier_exact_schema_and_contiguous_original_evidence():
    assert parse_verdict(verdict(), (SPAN,), "v").status.value == "PASS"
    for bad in (verdict(extra="forbidden"), verdict(addresses_query="true"), "```json\n" + verdict() + "\n```",
                '{"status":"PASS","status":"FAIL"}', verdict(citations=[{"doc_id":"d","field":"text","start_char":0,"end_char":10,"quote":"joinedseven"}])):
        with pytest.raises((ValueError, TypeError, KeyError)):
            parse_verdict(bad, (SPAN,), "v")
    assert parse_verdict(verdict(citations=[]), (SPAN,), "v").status.value == "FAIL"
    separated = (RemainingSpan("d", "text", 0, 3, "abc"), RemainingSpan("d", "text", 7, 10, "def"))
    with pytest.raises(ValueError):
        parse_verdict(verdict(citations=[{"doc_id":"d","field":"text","start_char":0,"end_char":10,"quote":"abcdef"}]), separated, "v")


def config(**kwargs):
    return FinalLLMConfig(model="explicit-model", endpoint="https://provider.invalid/chat/completions", api_key="secret-key", **kwargs)


def test_final_verifier_is_disabled_by_default_and_never_uses_environment_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-trigger-live")
    verifier = FinalLLMVerifier(config(), transport=lambda *args: pytest.fail("network must not run"))
    assert verifier.verify("query", "answer", (SPAN,)).status.value == "NOT_CONFIGURED"
    assert verifier.calls == 0 and "secret-key" not in repr(verifier.config)


def test_final_verifier_calls_only_supplied_endpoint_model_and_strict_json():
    calls = []
    def transport(endpoint, key, payload, timeout):
        calls.append((endpoint, key, payload))
        return 200, {"choices": [{"finish_reason": "stop", "message": {"content": verdict()}}]}
    verifier = FinalLLMVerifier(config(allow_live=True), transport=transport)
    for _ in range(2):
        assert verifier.verify("query", "answer", (SPAN,)).status.value == "PASS"
    assert verifier.verify("query", "answer", (SPAN,)).status.value == "ERROR"
    assert len(calls) == 2
    for endpoint, key, payload in calls:
        assert endpoint == verifier.config.endpoint and payload["model"] == verifier.config.model
        data = json.loads(payload["messages"][1]["content"])
        assert set(data) == {"query", "response", "remaining_spans"}
        assert data["remaining_spans"] == [{"doc_id":"d", "field":"text", "start_char":5,"end_char":10,"text":"seven"}]
        assert key == "secret-key"
    assert verifier.for_request().calls == 0


@pytest.mark.parametrize("http_status", [401, 429])
def test_final_verifier_auth_and_rate_errors_have_no_fallback_or_retry(http_status):
    calls = []
    def transport(*args):
        calls.append(1)
        return http_status, {"error": {"code": "error"}}
    verifier = FinalLLMVerifier(config(allow_live=True), transport=transport)
    assert verifier.verify("query", "answer", (SPAN,)).status.value == "ERROR"
    assert verifier.verify("query", "answer", (SPAN,)).status.value == "ERROR"
    assert len(calls) == 1


def test_final_verifier_overlength_abstains_without_call():
    verifier = FinalLLMVerifier(config(allow_live=True, max_input_chars=10), transport=lambda *args: pytest.fail("unexpected call"))
    result = verifier.verify("q", "answer", (SPAN,))
    assert result.status.value == "UNCERTAIN" and result.reason == "INPUT_TOO_LONG"
    assert verifier.calls == 0


def test_final_llm_adapter_is_connected_at_final_stage_and_reports_usage():
    from rag_defense.adapters.synthetic_v2 import build_v2_scenario, FACT
    scenario = build_v2_scenario("drop_only")
    calls = []
    def transport(endpoint, key, payload, timeout):
        data = json.loads(payload["messages"][1]["content"])
        calls.append(data)
        assert data["response"] == "answer"
        assert all(span["doc_id"] != "x" for span in data["remaining_spans"])
        span = data["remaining_spans"][0]
        text = json.dumps({"status":"PASS", "addresses_query":True, "supported":True, "citations":[{
            "doc_id":span["doc_id"], "field":span["field"], "start_char":span["start_char"],
            "end_char":span["end_char"], "quote":span["text"]}]})
        return 200, {"choices":[{"finish_reason":"stop", "message":{"content":text}}], "usage":{"prompt_tokens":42,"completion_tokens":17}}
    verifier = FinalLLMVerifier(config(allow_live=True), transport=transport)
    engine = DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=verifier,
                           config=DefenseConfig(allow_live=True))
    result = engine.repair(scenario.request)
    assert result.status == DefenseStatus.PATCH_APPLIED and len(calls) == 1
    assert result.usage.grounding_input_tokens == 42 and result.usage.grounding_output_tokens == 17
    assert result.usage.grounding_verifications == 1 and result.usage.grounding_latency_ms >= 0
    assert result.final_response.text == calls[-1]["response"]
