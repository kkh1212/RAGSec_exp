import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from .config import DefenseConfig, load_config
from .engine import DefenseEngine
from .report import save_run
from .workspace import local_path, project_root
from .version import __version__


def doctor(*, config, labels=None, artifact=None, embedding_model_dir=None):
    root = project_root()
    label_report = None
    if labels:
        from .optional_ml.data import load_records, validate_records
        label_report = validate_records(load_records(labels))
    observer_state, observer_connected = "UNCONFIGURED", False
    artifact_note = "no_artifact_supplied"
    if artifact:
        from .optional_ml.observer import inspect_artifact, MLObserver
        try:
            manifest, _ = inspect_artifact(artifact)
            observer_state = manifest["readiness"]
            artifact_note = "encoder_connection_not_checked"
            if embedding_model_dir:
                observer = MLObserver(artifact, embedding_model_dir=embedding_model_dir)
                observer_state, observer_connected = observer.readiness.value, observer.configured
                artifact_note = observer.unavailable_reason
        except (ValueError, KeyError, TypeError, OSError):
            observer_state, artifact_note = "INCOMPATIBLE_ARTIFACT", "invalid_artifact"
    elif label_report is not None:
        observer_state = "NEEDS_LABELS"
    imported = root / "data/imported"
    # Presence only; do not search other projects or implicitly import copied files.
    files = [local_path(p) for p in imported.iterdir()] if imported.is_dir() else []
    actual_files = [str(p.relative_to(root)) for p in files if p.is_file() and p.suffix in (".jsonl", ".csv", ".json")]
    return dict(project_root=str(root), python=sys.version.split()[0], executable=sys.executable,
        project_venv=Path(sys.prefix).resolve() == root / ".venv", disk_free_bytes=shutil.disk_usage(root).free,
        package_version=__version__, default_mode="mock", allow_live=config.allow_live,
        search_engine={"state": "IMPLEMENTED_SYNTHETIC_TESTED", "version": config.search_version,
                       "generation_budget": config.max_additional_generation_attempts},
        generator={"state": "UNCONFIGURED", "available": ["mock", "replay", "explicit CallableGenerator"]},
        observer={"state": observer_state, "connected": observer_connected, "note": artifact_note},
        verifier={"state": "UNCONFIGURED", "available": ["mock", "replay", "CallableVerifier", "FinalLLMVerifier"]},
        label_data_state="NEEDS_LABELS" if not label_report or not label_report["human_verified_trainable"] else "REVIEWED_LABELS_PRESENT",
        label_validation=label_report, actual_data_files=actual_files, actual_data_present=bool(actual_files),
        actual_data_content_verified=False, real_attack_evaluation="REAL_ATTACK_EVALUATION_NOT_RUN",
        executable_modes=["mock", "replay", "data_import_review_validation"],
        ml_dependencies_installed=all(importlib.util.find_spec(m) is not None for m in ("sentence_transformers", "sklearn")),
        network_called=False)


def _display(result, path, label):
    print(json.dumps(dict(scenario=label, mode=result.mode.value, status=result.status.value,
        generation_attempts=result.usage.generation_attempts, changed_characters=result.changed_characters,
        grounding_verified=result.verification_status.value == "PASS", gold_correctness=None,
        report=str(path)), ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent offline RAG refusal-repair experiments")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("doctor", help="read-only local runtime check; never inspect API keys")
    check.add_argument("--config", default="configs/defense.v2.mock.yaml")
    check.add_argument("--labels")
    check.add_argument("--artifact-dir")
    check.add_argument("--embedding-model-dir")
    demo = sub.add_parser("demo", help="synthetic, network-free scenarios")
    demo.add_argument("--config", default="configs/defense.v2.mock.yaml")
    demo.add_argument("--scenario", default="all")
    demo.add_argument("--output-dir", default="runs")
    replay = sub.add_parser("replay", help="read a complete JSONL file copied into this project")
    replay.add_argument("--input", required=True)
    replay.add_argument("--config", default="configs/defense.mock.yaml")
    replay.add_argument("--output-dir", default="runs")
    for name in ("import-observer-data", "review-labels", "validate-labels"):
        command = sub.add_parser(name)
        command.add_argument("--input", required=True)
        if name != "validate-labels":
            command.add_argument("--output", required=True)
        else:
            command.add_argument("--final-evaluation-input", help="explicit copied evaluation file for cross-dataset leakage audit")
    training = sub.add_parser("train-observer")
    training.add_argument("--input", required=True)
    training.add_argument("--embedding-model-dir", required=True)
    training.add_argument("--embedding-revision", required=True)
    training.add_argument("--model-id", default="sentence-transformers/all-MiniLM-L6-v2")
    training.add_argument("--output-dir", required=True)
    training.add_argument("--targets", default="configs/observer.targets.json")
    training.add_argument("--seed", type=int, default=13)
    training.add_argument("--max-records", type=int, default=2000)
    training.add_argument("--threshold", type=float)
    training.add_argument("--validation-fraction", type=float, default=0.2)
    training.add_argument("--test-fraction", type=float, default=0.2)
    evaluation = sub.add_parser("evaluate-observer")
    evaluation.add_argument("--input", required=True)
    evaluation.add_argument("--artifact-dir", required=True)
    evaluation.add_argument("--embedding-model-dir", required=True)
    records = sub.add_parser("evaluate-records")
    records.add_argument("--input")
    records.add_argument("--config", default="configs/defense.v2.mock.yaml")
    records.add_argument("--output", default="runs/real-evaluation-readiness.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            print(json.dumps(doctor(config=load_config(local_path(args.config)), labels=args.labels,
                artifact=args.artifact_dir, embedding_model_dir=args.embedding_model_dir), ensure_ascii=False, indent=2))
            return 0
        if args.command in ("import-observer-data", "review-labels", "validate-labels"):
            from .optional_ml.data import import_data, review_labels, load_records, validate_records
            if args.command == "import-observer-data":
                result = import_data(args.input, args.output)
            elif args.command == "review-labels":
                result = review_labels(args.input, args.output, reader=input)
            else:
                labeled = load_records(args.input)
                result = validate_records(labeled)
                if args.final_evaluation_input:
                    from .evaluation import load_evaluation_records
                    from .optional_ml.data import ObserverRecord
                    evaluation_rows = load_evaluation_records(args.final_evaluation_input)
                    cross_rows = [ObserverRecord(sample_id="evaluation:" + row.record_id, query_id=row.query_id,
                        query=row.request.query, response=row.request.initial_response.text,
                        execution_status=row.request.initial_response.execution_status.value,
                        data_kind="real" if row.data_kind == "copied_real" else "synthetic",
                        purpose="final_defense_evaluation") for row in evaluation_rows]
                    separation = validate_records(labeled + cross_rows)
                    result["final_evaluation_separation"] = separation
                    result["valid"] = result["valid"] and separation["valid"]
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("valid", True) else 2
        if args.command == "train-observer":
            from .optional_ml.train import train
            from .optional_ml.metrics import ValidationTargets
            targets = ValidationTargets(**json.loads(local_path(args.targets).read_text(encoding="utf-8")))
            manifest = train(input_path=args.input, embedding_model_dir=args.embedding_model_dir,
                embedding_revision=args.embedding_revision, model_id=args.model_id, output_dir=args.output_dir,
                seed=args.seed, max_records=args.max_records, threshold=args.threshold,
                validation_fraction=args.validation_fraction, test_fraction=args.test_fraction, targets=targets)
            print(json.dumps({"readiness": manifest["readiness"], "validation": manifest["validation"],
                              "assessment": manifest["validation_assessment"]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "evaluate-observer":
            from .optional_ml.train import evaluate
            print(json.dumps(evaluate(input_path=args.input, artifact_dir=args.artifact_dir,
                embedding_model_dir=args.embedding_model_dir), ensure_ascii=False, indent=2))
            return 0
        config = load_config(local_path(args.config))
        if config.allow_live:
            raise ValueError("mock/replay CLI requires allow_live=false; no live CLI is provided")
        if args.command == "evaluate-records":
            from .evaluation import evaluate_copied_file
            output = local_path(args.output)
            result = evaluate_copied_file(args.input, config)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"status": result["status"], "report": str(output)}, ensure_ascii=False))
            return 0
        # Check output location before any work, including symlinks.
        local_path(args.output_dir)
        if args.command == "demo":
            from .adapters.mock import build_scenario, SCENARIOS
            from .adapters.synthetic_v2 import build_v2_scenario, V2_SCENARIOS
            names = (SCENARIOS + V2_SCENARIOS if config.search_version == "v2" else SCENARIOS) if args.scenario == "all" else (args.scenario,)
            for name in names:
                scenario = build_v2_scenario(name) if name in V2_SCENARIOS else build_scenario(name)
                result = DefenseEngine(generator=scenario.generator, observer=scenario.observer,
                    verifier=scenario.verifier, config=config).repair(scenario.request)
                _display(result, save_run(result, args.output_dir), name)
        else:
            from .adapters.replay import load_replay
            request, generator, observer, verifier = load_replay(args.input)
            result = DefenseEngine(generator=generator, observer=observer, verifier=verifier, config=config).repair(request)
            _display(result, save_run(result, args.output_dir), "copied_recording")
        return 0
    except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
        # Do not echo untrusted file contents or adapter exception messages.
        print(f"configuration/input error ({type(exc).__name__}); check the local input and contract", file=sys.stderr)
        if args.command == "train-observer":
            print(json.dumps({"readiness": "NEEDS_LABELS", "trained": False,
                "reason": "requires_verified_real_labels_sufficient_groups_local_encoder_and_optional_dependencies"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
