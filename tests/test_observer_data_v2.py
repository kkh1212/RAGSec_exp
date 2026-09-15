"""Data contracts and optional CPU fitting checks; all generated rows are synthetic."""
from dataclasses import replace
import importlib
import json
import sys
import pytest

from rag_defense.optional_ml.data import (ObserverRecord, import_data, load_records, write_records,
    review_labels, validate_records, split_records, dataset_hash)
from rag_defense.optional_ml.metrics import classification_metrics, ValidationTargets, assess, choose_threshold
from rag_defense.optional_ml.common import input_support, encode_pairs
from rag_defense.optional_ml.observer import MLObserver
from rag_defense.cli import main


def row(i="sample", **kwargs):
    return ObserverRecord(sample_id=i, query_id="q-"+i, query="Question number " + i,
        response="response", data_kind="synthetic", **kwargs)


def test_import_is_explicit_unreviewed_and_metadata_hidden(tmp_path):
    source = tmp_path / "copied.jsonl"
    source.write_text(json.dumps({"sample_id":"s", "query_id":"q", "query":"Question?", "response":"response",
        "execution_status":"OK", "data_kind":"synthetic", "attack_type":"metadata-only-sentinel", "clean":True}) + "\n", encoding="utf-8")
    output = tmp_path / "review.csv"
    result = import_data(source, output)
    assert result["readiness"] == "NEEDS_LABELS"
    assert output.read_text().splitlines()[0].startswith("query,response,")
    assert "metadata-only-sentinel" not in output.read_text()
    assert "metadata-only-sentinel" in (tmp_path / "review.csv.metadata.jsonl").read_text()
    record = load_records(output)[0]
    assert record.label == "" and record.label_status == "unreviewed"
    displayed = []
    review_labels(output, tmp_path / "reviewed.jsonl", reader=lambda _: "ANSWER", writer=displayed.append)
    assert not any("metadata-only-sentinel" in text for text in displayed)
    assert load_records(tmp_path / "reviewed.jsonl")[0].label_status == "human_verified"


def test_import_never_certifies_suggested_or_preexisting_labels(tmp_path):
    source = tmp_path / "copied.jsonl"
    write_records([row(label="REFUSAL", label_status="human_verified")], source)
    output = tmp_path / "imported.jsonl"
    import_data(source, output)
    assert load_records(output)[0].label_status == "suggested"


def test_only_human_verified_ok_real_development_is_trainable():
    verified = replace(row(label="ANSWER", label_status="human_verified"), data_kind="real")
    variants = [verified, replace(verified, label_status="suggested"), replace(verified, data_kind="synthetic"),
        replace(verified, execution_status="PROVIDER_BLOCK"), replace(verified, label="UNCERTAIN")]
    report = validate_records(variants)
    assert report["human_verified_trainable"] == 1


def test_training_rejects_synthetic_records_before_importing_encoder(tmp_path):
    from rag_defense.optional_ml.train import train
    input_path = tmp_path / "synthetic.jsonl"
    write_records([row(label="ANSWER", label_status="human_verified")], input_path)
    with pytest.raises(ValueError, match="NEEDS_LABELS"):
        train(input_path=input_path, output_dir=tmp_path / "artifact", embedding_model_dir=tmp_path / "missing", embedding_revision="test")
    assert not (tmp_path / "artifact").exists()


def test_three_way_group_split_is_deterministic_and_variants_stay_together():
    records = [replace(row(str(g)), sample_id=f"{g}-{j}", response=f"variant-{j}") for g in range(12) for j in range(3)]
    splits, _ = split_records(records)
    assert splits == split_records(records)[0]
    groups = {name: {records[i].query_id for i in indices} for name, indices in splits.items()}
    assert not groups["train"] & groups["validation"] and not groups["test"] & (groups["train"] | groups["validation"])
    assert sum(map(len, splits.values())) == len(records)


@pytest.mark.parametrize("change", ["same_id", "normalized", "near"])
def test_duplicate_questions_cannot_cross_explicit_splits(change):
    query = "What was the recorded measurement in the coastal laboratory?"
    first = replace(row("a"), query=query, split="train")
    second = replace(row("b"), query=query, split="test")
    if change == "same_id":
        second = replace(second, query_id=first.query_id)
    elif change == "normalized":
        second = replace(second, query="  " + query.upper() + "  ")
    else:
        second = replace(second, query=query.replace("laboratory", "laboratories"))
    records = [first, second, replace(row("c"), split="validation")]
    with pytest.raises(ValueError):
        split_records(records)
    assert not validate_records(records)["valid"]


def test_development_and_final_defense_data_never_share_group():
    first = row("a")
    second = replace(first, sample_id="b", purpose="final_defense_evaluation")
    assert "development_overlaps_final_evaluation" in validate_records([first, second])["errors"]


def test_insufficient_independent_groups_are_reported():
    with pytest.raises(ValueError, match="three"):
        split_records([row("one"), row("two")])
    metrics = classification_metrics(["REFUSAL", "ANSWER", "OTHER"], ["REFUSAL", "ANSWER", "OTHER"], query_groups=3)
    assessment = assess(metrics, ValidationTargets())
    assert not assessment["passed"] and not assessment["checks"]["sample_count"]
    assert metrics["per_class"]["REFUSAL"]["recall_wilson_95"][0] < 0.5


def test_uncertain_refusals_remain_in_recall_denominator():
    metrics = classification_metrics(["REFUSAL"]*10 + ["ANSWER"], ["REFUSAL"]*8 + ["UNCERTAIN"]*2 + ["ANSWER"])
    assert metrics["per_class"]["REFUSAL"]["recall"] == {"numerator":8, "denominator":10, "value":0.8}
    assert metrics["uncertain_rate"]["numerator"] == 2
    assert not assess(metrics, ValidationTargets())["checks"]["refusal_recall"]


def test_threshold_selection_uses_validation_without_dropping_unsupported_rows():
    labels = ["REFUSAL", "ANSWER", "OTHER", "REFUSAL"]
    scores = [{"REFUSAL":.91, "ANSWER":.09}, {"ANSWER":.99, "OTHER":.01}, {"OTHER":.7, "ANSWER":.3}, None]
    threshold, metrics, assessment = choose_threshold(labels, scores, targets=ValidationTargets(), thresholds=[.5,.8,.95], overlength=1)
    assert threshold == .5 and metrics["samples"] == 4
    assert metrics["overlength_rate"]["value"] == .25
    assert metrics["per_class"]["REFUSAL"]["recall"]["denominator"] == 2
    assert not assessment["passed"]


class TokenEncoder:
    max_seq_length = 6
    def tokenizer(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens is True and truncation is False
        return {"input_ids": list(range(len(text.split()) + 2))}
    def encode(self, *args, **kwargs):
        pytest.fail("long inputs must not reach encode")


def test_tokenizer_length_checks_both_fields_before_embedding():
    model = TokenEncoder()
    assert input_support(model, "one", "one two three four five", 6) == "response_token_limit_exceeded"
    assert input_support(model, "one two three four five", "one", 6) == "query_token_limit_exceeded"
    with pytest.raises(ValueError, match="token_limit"):
        encode_pairs(model, [("one", "one two three four five")])


def observer_stub(model):
    observer = MLObserver()
    observer.configured, observer.model = True, model
    observer.manifest = {"embedding":{"max_seq_length":6,"batch_size":1}, "threshold":.8}
    observer.classifier = {"classes":["ANSWER", "OTHER", "REFUSAL"], "coefficients":[], "intercept":[]}
    return observer


def test_long_input_returns_uncertain_with_reason_without_truncation():
    observer = observer_stub(TokenEncoder())
    result = observer.observe("one", "one two three four five")
    assert result.state.value == "UNCERTAIN" and result.confidence == 0
    assert result.reason == "response_token_limit_exceeded"


def test_low_confidence_is_uncertain_and_not_reported_as_calibrated(monkeypatch):
    module = importlib.import_module("rag_defense.optional_ml.observer")
    class Score(list):
        def argmax(self):
            return 0
    monkeypatch.setattr(module, "encode_pairs", lambda *args: [])
    monkeypatch.setattr(module, "probabilities", lambda *args: [Score([.6,.2,.2])])
    result = observer_stub(TokenEncoder()).observe("query", "response")
    assert result.state.value == "UNCERTAIN" and result.reason == "below_confidence_threshold"


def test_incompatible_artifact_is_not_validated(tmp_path):
    (tmp_path / "manifest.json").write_text('{"trained":true,"readiness":"VALIDATED"}')
    (tmp_path / "classifier.json").write_text('{}')
    observer = MLObserver(tmp_path)
    assert observer.readiness.value == "INCOMPATIBLE_ARTIFACT" and not observer.configured


def test_no_data_cli_states_are_explicit_and_null_metrics(tmp_path, capsys):
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["observer"]["state"] == "UNCONFIGURED"
    assert report["verifier"]["state"] == "UNCONFIGURED" and report["label_data_state"] == "NEEDS_LABELS"
    assert not report["allow_live"] and not report["network_called"]
    output = tmp_path / "evaluation.json"
    assert main(["evaluate-records", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "REAL_ATTACK_EVALUATION_NOT_RUN"
    assert all(value is None for value in report["metrics"].values())


def test_import_review_validate_train_evaluate_cli_commands(tmp_path, capsys, monkeypatch):
    source, imported, reviewed = (tmp_path / name for name in ("source.jsonl", "review.csv", "reviewed.jsonl"))
    write_records([row()], source)
    assert main(["import-observer-data", "--input", str(source), "--output", str(imported)]) == 0
    monkeypatch.setattr("builtins.input", lambda _: "OTHER")
    assert main(["review-labels", "--input", str(imported), "--output", str(reviewed)]) == 0
    assert main(["validate-labels", "--input", str(reviewed)]) == 0
    assert load_records(reviewed)[0].label == "OTHER"
    assert main(["train-observer", "--input", str(reviewed), "--embedding-model-dir", str(tmp_path / "missing"),
                 "--embedding-revision", "synthetic", "--output-dir", str(tmp_path / "artifact")]) == 2
    assert not (tmp_path / "artifact").exists()
    assert main(["evaluate-observer", "--input", str(reviewed), "--artifact-dir", str(tmp_path / "missing"),
                 "--embedding-model-dir", str(tmp_path / "missing")]) == 2
    assert "NEEDS_LABELS" in capsys.readouterr().out


def test_cli_audits_explicit_separate_final_evaluation_file(tmp_path, capsys):
    from rag_defense.adapters.synthetic_v2 import build_v2_scenario
    from rag_defense.fingerprint import to_jsonable
    scenario = build_v2_scenario("drop_only")
    development = tmp_path / "development.jsonl"
    write_records([replace(row("q"), query=scenario.request.query)], development)
    final = tmp_path / "final.jsonl"
    final.write_text(json.dumps({"schema_version":1,"record_id":"e","query_id":"different-id",
        "data_kind":"synthetic","request":to_jsonable(scenario.request)}) + "\n")
    assert main(["validate-labels","--input",str(development),"--final-evaluation-input",str(final)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert "development_overlaps_final_evaluation" in report["final_evaluation_separation"]["errors"]


def test_cpu_fitting_export_and_sealed_test_workflow_with_synthetic_encoder(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    training = importlib.import_module("rag_defense.optional_ml.train")
    observer_module = importlib.import_module("rag_defense.optional_ml.observer")
    records = [replace(row(str(g)), sample_id=f"{g}-{label}", response=str(index), label=label,
        label_status="human_verified") for g in range(20) for index, label in enumerate(("ANSWER", "OTHER", "REFUSAL"))]
    source = tmp_path / "explicitly-synthetic.jsonl"
    write_records(records, source)
    encoder_dir = tmp_path / "synthetic-encoder"
    encoder_dir.mkdir()
    (encoder_dir / "fixture.txt").write_text("test-only encoder; not a downloaded or real language model")
    class Encoder(TokenEncoder):
        max_seq_length = 256
        seen = []
        def encode(self, texts, **kwargs):
            self.seen.extend(texts)
            return np.asarray([[float(text == str(i)) for i in range(3)] if not text.startswith("Question") else [1.,1.,1.] for text in texts])
        def get_sentence_embedding_dimension(self):
            return 3
    model = Encoder()
    monkeypatch.setattr(training, "load_embedding", lambda *args, **kwargs: model)
    # Deliberately inject eligibility for this synthetic software check. The normal
    # path rejecting these very rows is tested above; no fake real logs are made.
    monkeypatch.setattr(training, "_eligible", lambda r: r.label_status == "human_verified")
    monkeypatch.setattr(training, "version", lambda name: "synthetic-test-backend")
    artifact = tmp_path / "synthetic-artifact"
    manifest = training.train(input_path=source, embedding_model_dir=encoder_dir, output_dir=artifact, embedding_revision="synthetic-fixture")
    assert manifest["training_data_kind"] == "synthetic_test_only"
    assert manifest["readiness"] == "TRAINED_PROVISIONAL" and manifest["test"] is None
    assert manifest["threshold_selection"]["test_used"] is False
    assert "calibrated probability" in manifest["confidence_interpretation"]
    test_ids = set(manifest["splits"]["test"]["sample_ids"])
    assert all(r.query not in model.seen for r in records if r.sample_id in test_ids)
    assert not MLObserver(artifact, embedding_model_dir=encoder_dir).configured
    # Exercise holdout reporting separately with a test-only observer; never register it.
    from rag_defense import Observation
    class TestObserver:
        configured = True
        def __init__(self, *args, **kwargs):
            self.manifest = json.loads((artifact / "manifest.json").read_text())
        def observe(self, query, response):
            return Observation(("ANSWER", "OTHER", "REFUSAL")[int(response)], 1, "synthetic")
    monkeypatch.setattr(observer_module, "MLObserver", TestObserver)
    report = training.evaluate(input_path=source, artifact_dir=artifact, embedding_model_dir=encoder_dir)
    assert report["test"]["samples"] == len(test_ids) and not report["assessment"]["passed"]
    assert report["readiness"] == "TRAINED_PROVISIONAL"
    again = training.evaluate(input_path=source, artifact_dir=artifact, embedding_model_dir=encoder_dir)
    assert again["cached_report"] and again["test"] == report["test"]
    assert json.loads((artifact / "manifest.json").read_text())["threshold"] == manifest["threshold"]
    write_records([replace(records[0], response="changed"), *records[1:]], source)
    with pytest.raises(ValueError, match="sealed"):
        training.evaluate(input_path=source, artifact_dir=artifact, embedding_model_dir=encoder_dir)
