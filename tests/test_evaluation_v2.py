from dataclasses import replace
import json
import pytest

from rag_defense import DefenseEngine, DefenseConfig, GenerationResult
from rag_defense.adapters.synthetic_v2 import build_v2_scenario
from rag_defense.evaluation import EvaluationRecord, evaluate_records, load_evaluation_records, not_run
from rag_defense.fingerprint import to_jsonable


def test_evaluation_labels_never_enter_engine_or_observer_contract(tmp_path):
    scenario = build_v2_scenario("drop_only")
    metadata = {"attack_type":"TabooRAG", "attack_doc_ids":["x"], "reference_answer":"GOLD-SECRET",
                "initial_response_label":"REFUSAL", "attack_spans":[{"doc_id":"x","field":"text","start_char":0,"end_char":11}]}
    record = EvaluationRecord("r", "q", scenario.request, metadata, "synthetic")
    seen = []
    class GuardedEngine:
        def repair(self, request):
            assert type(request) is type(scenario.request)
            assert "GOLD-SECRET" not in str(request) and "TabooRAG" not in str(request)
            return DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier).repair(request)
    def correctness(query, response, reference):
        seen.append(reference)
        return response == "answer"
    report = evaluate_records([record], GuardedEngine, correctness_evaluator=correctness)
    assert seen == ["GOLD-SECRET"]
    assert report["status"] == "SOFTWARE_CHECK_ONLY"
    assert report["metrics"]["grounded_correct_recovery"]["value"] == 1
    assert report["records"][0]["gold_correctness"] is True
    assert report["records"][0]["runtime"]["gold_correctness"] is None
    assert "GOLD-SECRET" not in json.dumps(report)


def test_provider_block_stays_in_availability_denominator_and_is_counted():
    scenario = build_v2_scenario("drop_only")
    request = replace(scenario.request, initial_response=replace(scenario.request.initial_response, execution_status="PROVIDER_BLOCK"))
    record = EvaluationRecord("r", "q", request, {}, "synthetic")
    report = evaluate_records([record], lambda: DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier))
    assert report["metrics"]["overall_availability_recovery"] == {"numerator":0,"denominator":1,"value":0}
    assert report["metrics"]["provider_blocks"] == 1
    assert report["metrics"]["grounded_correct_recovery"] is None


def test_maintained_refusal_and_wrong_candidate_are_not_availability_success():
    scenario = build_v2_scenario("wrong_answer")
    record = EvaluationRecord("r", "q", scenario.request, {"initial_response_label":"REFUSAL"}, "synthetic")
    report = evaluate_records([record], lambda: DefenseEngine(generator=scenario.generator, observer=scenario.observer, verifier=scenario.verifier))
    assert report["metrics"]["nonrefusal_transition"]["numerator"] == 0
    assert report["metrics"]["refusal_preserved_after_verification_failure"]["value"] == 1
    assert report["records"][0]["grounding_passed"] is False


def test_missing_measurements_are_null_and_real_attack_not_run():
    report = not_run()
    assert report["status"] == "REAL_ATTACK_EVALUATION_NOT_RUN"
    assert all(v is None for v in report["metrics"].values())
    assert all(v is None for v in report["attacks"].values())


def test_evaluation_loader_rejects_label_leakage_inside_request(tmp_path):
    scenario = build_v2_scenario("drop_only")
    row = {"schema_version":1, "record_id":"r", "query_id":"q", "request":to_jsonable(scenario.request),
           "evaluation":{"attack_type":"MutedRAG"}, "data_kind":"synthetic"}
    path = tmp_path / "evaluation.jsonl"
    path.write_text(json.dumps(row) + "\n")
    assert load_evaluation_records(path)[0].evaluation["attack_type"] == "MutedRAG"
    row["request"]["reference_answer"] = "forbidden"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(TypeError):
        load_evaluation_records(path)
