"""Only pure-Python contracts are tested; no fitting, embeddings, or model download."""
import json
import sys
import subprocess
import pytest
from rag_defense import DefenseEngine, DefenseStatus
from rag_defense.adapters.mock import build_scenario
from rag_defense.optional_ml.observer import MLObserver
from rag_defense.optional_ml.common import LabeledResponse, split_by_query, load_labels
from rag_defense.optional_ml.train import train


def test_untrained_ml_observer_is_not_configured_and_import_is_lightweight(tmp_path):
    for observer in (MLObserver(), MLObserver(tmp_path / "absent")):
        s = build_scenario()
        r = DefenseEngine(generator=s.generator, observer=observer, verifier=s.verifier).repair(s.request)
        assert r.status == DefenseStatus.OBSERVER_NOT_CONFIGURED and not s.generator.calls
    # A fresh process checks lazy imports even when separate optional CPU tests ran.
    done = subprocess.run([sys.executable, "-c", "from rag_defense.optional_ml.observer import MLObserver; import sys; MLObserver(); assert not {'torch', 'sentence_transformers', 'sklearn', 'numpy'} & set(sys.modules)"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_query_group_split_has_no_overlap_and_is_reproducible():
    # These objects test ONLY the split function; they are never used to fit a model.
    records = [LabeledResponse(str(group), f"question-{group}", f"response-{i}", label, True, "human_reviewed_real")
               for group in range(6) for i, label in enumerate(("ANSWER", "REFUSAL", "OTHER"))]
    training, validation = split_by_query(records, 0.33, 13)
    assert (training, validation) == split_by_query(records, 0.33, 13)
    assert not {records[i].query_id for i in training} & {records[i].query_id for i in validation}
    assert sorted(training + validation) == list(range(len(records)))


def test_duplicate_questions_with_different_ids_rejected_before_training():
    rows = [LabeledResponse(str(i), "same question", "response", "ANSWER", True, "human_reviewed_real") for i in range(2)]
    with pytest.raises(ValueError):
        split_by_query(rows)


@pytest.mark.parametrize("reviewed,kind,label", [(False, "human_reviewed_real", "ANSWER"),
    (True, "synthetic", "REFUSAL"), (True, "human_reviewed_real", "UNCERTAIN")])
def test_nonreviewed_synthetic_or_uncertain_training_labels_rejected(reviewed, kind, label):
    with pytest.raises(ValueError):
        LabeledResponse("q", "question", "response", label, reviewed, kind)


def test_no_training_artifact_is_created_without_real_label_data(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    output = tmp_path / "untrained"
    with pytest.raises(ValueError):
        train(input_path=path, embedding_model_dir=tmp_path / "no-model", output_dir=output, embedding_revision="none")
    assert not output.exists()


def test_training_input_schema_rejects_gold_labels(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps(dict(query_id="q", query="question", response="response", label="ANSWER",
        human_reviewed=True, data_kind="human_reviewed_real", gold_answer="excluded")), encoding="utf-8")
    with pytest.raises(TypeError):
        load_labels(path)
