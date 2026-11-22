"""Offline fixed-encoder LR fitting; validation selects the threshold, test is sealed."""
from dataclasses import asdict
import json
from importlib.metadata import version

from ..fingerprint import digest
from ..workspace import local_path
from .common import (FEATURE_VERSION, LABELS, model_tree_hash, load_embedding,
                     encode_pairs, probabilities, file_hash, configure_runtime, input_support)
from .data import (load_records, split_records, dataset_hash, leakage_groups)
from .metrics import ValidationTargets, choose_threshold, classification_metrics, assess


def _eligible(record):
    return (record.label_status == "human_verified" and record.data_kind == "real"
            and record.purpose == "observer_development" and record.execution_status == "OK")


def _write(path, value):
    path = local_path(path)
    # Replace atomically inside this project, never publish a half-written manifest.
    temporary = local_path(str(path) + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _scores(model, records, classifier, batch_size, max_seq_length):
    supported = [not input_support(model, r.query, r.response, max_seq_length) for r in records]
    reasons = [input_support(model, r.query, r.response, max_seq_length) for r in records]
    pairs = [(r.query, r.response) for r, ok in zip(records, supported) if ok]
    rows = iter(probabilities(encode_pairs(model, pairs, batch_size), classifier["coefficients"], classifier["intercept"]).tolist()) if pairs else iter(())
    scores = [dict(zip(classifier["classes"], next(rows))) if ok else None for ok in supported]
    return scores, sum(reason.endswith("token_limit_exceeded") for reason in reasons)


def train(*, input_path, embedding_model_dir, output_dir,
          model_id="sentence-transformers/all-MiniLM-L6-v2", embedding_revision,
          threshold=None, validation_fraction=0.2, test_fraction=0.2, seed=13, max_records=2000,
          max_seq_length=256, batch_size=16, targets=None):
    if not 1 <= batch_size <= 32 or not 1 <= max_seq_length <= 512:
        raise ValueError("invalid batch size or maximum sequence length")
    if threshold is not None and not 0 < threshold <= 1:
        raise ValueError("invalid additional validation threshold")
    if not embedding_revision:
        raise ValueError("record the immutable revision of the manually copied encoder")
    output = local_path(output_dir)
    if output.exists():
        raise ValueError("output_dir already exists; choose a new artifact directory")
    records = load_records(input_path, max_records)
    if not records or not any(_eligible(r) for r in records):
        raise ValueError("NEEDS_LABELS: no human_verified real development labels")
    if any(r.purpose != "observer_development" for r in records):
        raise ValueError("final defense evaluation records are forbidden in observer development")
    splits, warnings = split_records(records, seed=seed, validation_fraction=validation_fraction, test_fraction=test_fraction)
    split_records_by_name = {name: [records[i] for i in indices if _eligible(records[i])] for name, indices in splits.items()}
    training = [r for r in split_records_by_name["train"] if r.label in LABELS]
    if {r.label for r in training} != set(LABELS):
        raise ValueError("NEEDS_LABELS: training requires all three human-verified semantic classes")
    embedding_hash = model_tree_hash(embedding_model_dir)
    configure_runtime()
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    model = load_embedding(embedding_model_dir, max_seq_length=max_seq_length)
    reasons = [input_support(model, r.query, r.response, max_seq_length) for r in training]
    accepted_training = [r for r, reason in zip(training, reasons) if not reason]
    if {r.label for r in accepted_training} != set(LABELS):
        raise ValueError("NEEDS_LABELS: supported-length training requires all classes")
    features = encode_pairs(model, [(r.query, r.response) for r in accepted_training], batch_size)
    labels = np.asarray([r.label for r in accepted_training])
    classifier = LogisticRegression(solver="lbfgs", max_iter=1000, C=1.0, class_weight="balanced", random_state=seed)
    classifier.fit(features, labels)
    if int(classifier.n_iter_.max()) >= classifier.max_iter:
        raise ValueError("classifier did not converge; no completed artifact")
    payload = {"classes": classifier.classes_.tolist(), "coefficients": classifier.coef_.tolist(), "intercept": classifier.intercept_.tolist()}
    if not np.allclose(probabilities(features, payload["coefficients"], payload["intercept"]), classifier.predict_proba(features), atol=1e-8):
        raise ValueError("exported inference differs from fitted LR")
    validation = split_records_by_name["validation"]
    scores, overlength = _scores(model, validation, payload, batch_size, max_seq_length)
    targets = targets or ValidationTargets()
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99]
    if threshold is not None:
        thresholds.append(threshold)
    selected, metrics, assessment = choose_threshold([r.label for r in validation], scores, targets=targets,
        thresholds=thresholds, overlength=overlength, query_groups=len(leakage_groups(validation)[0]))
    manifest = {"schema_version": 2, "trained": True, "readiness": "TRAINED_PROVISIONAL",
        "training_data_kind": "human_verified_real" if all(r.data_kind == "real" for r in accepted_training) else "synthetic_test_only",
        "feature_version": FEATURE_VERSION,
        "preprocessing": {"text_normalization": "none", "query_response": "separate", "long_input": "UNCERTAIN_no_truncation",
                          "features": ["query", "response", "abs_query_minus_response", "query_times_response"]},
        "threshold": selected, "threshold_selection": {"split": "validation", "grid": sorted(set(thresholds)),
            "criterion": "semantic_targets_then_coverage_then_lower_threshold", "test_used": False},
        "confidence_interpretation": "Uncalibrated LR score; not a calibrated probability.",
        "label_mapping": {label: i for i, label in enumerate(LABELS)}, "seed": seed,
        "dataset_sha256": dataset_hash(records), "source_file_sha256": file_hash(input_path),
        "split_fractions": {"validation": validation_fraction, "test": test_fraction},
        "splits": {name: {"sample_ids": [records[i].sample_id for i in indices],
            "query_ids": sorted({records[i].query_id for i in indices})} for name, indices in splits.items()},
        "split_identity_hashes": {name: [digest(records[i].query) for i in indices] for name, indices in splits.items()},
        "training_records": len(accepted_training), "training_excluded_unsupported": len(training)-len(accepted_training),
        "training_overlength": sum(r.endswith("token_limit_exceeded") for r in reasons),
        "validation_records": len(validation), "validation": {**metrics, "group_disjoint": True},
        "validation_assessment": assessment, "targets": asdict(targets), "leakage_warnings": warnings,
        "test": None, "test_evaluation_completed": False,
        "embedding": {"model_id": model_id, "revision": embedding_revision, "tree_hash": embedding_hash,
            "max_seq_length": max_seq_length, "normalize_embeddings": True, "batch_size": batch_size,
            "device": "cpu", "local_files_only": True, "frozen": True},
        "classifier": {"type": "multinomial_LogisticRegression", "solver": "lbfgs", "C": 1.0,
            "class_weight": "balanced", "max_iter": 1000, "feature_dimension": int(features.shape[1])},
        "libraries": {name: version(name) for name in ("sentence-transformers", "scikit-learn", "numpy", "torch")}}
    output.mkdir(parents=True)
    _write(output / "classifier.json", payload)
    manifest["classifier_sha256"] = file_hash(output / "classifier.json")
    _write(output / "manifest.json", manifest)
    return manifest


def evaluate(*, input_path, artifact_dir, embedding_model_dir, max_records=2000):
    """One frozen holdout report. No threshold argument or refitting path."""
    from .observer import MLObserver
    observer = MLObserver(artifact_dir, embedding_model_dir=embedding_model_dir, allow_provisional=True)
    if not observer.configured:
        raise ValueError(observer.unavailable_reason)
    records = load_records(input_path, max_records)
    manifest = observer.manifest
    if dataset_hash(records) != manifest["dataset_sha256"]:
        raise ValueError("evaluation dataset differs from the sealed development split")
    if manifest["test_evaluation_completed"]:
        return {"readiness": manifest["readiness"], "test": manifest["test"], "assessment": manifest["test_assessment"], "cached_report": True}
    test_ids = set(manifest["splits"]["test"]["sample_ids"])
    holdout = [r for r in records if r.sample_id in test_ids and _eligible(r)]
    observations = [observer.observe(r.query, r.response) for r in holdout]
    metrics = classification_metrics([r.label for r in holdout], [o.state.value for o in observations],
        overlength=sum(o.reason.endswith("token_limit_exceeded") for o in observations), query_groups=len(leakage_groups(holdout)[0]))
    assessment = assess(metrics, ValidationTargets(**manifest["targets"]))
    manifest["test"], manifest["test_assessment"], manifest["test_evaluation_completed"] = metrics, assessment, True
    manifest["readiness"] = "VALIDATED" if assessment["passed"] and manifest["validation_assessment"]["passed"] else "TRAINED_PROVISIONAL"
    _write(local_path(artifact_dir) / "manifest.json", manifest)
    return {"readiness": manifest["readiness"], "test": metrics, "assessment": assessment, "cached_report": False}


def main(argv=None):
    # Preserve the old module entry point while sharing the maintained CLI contract.
    from ..cli import main as cli_main
    import sys
    return cli_main(["train-observer", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
