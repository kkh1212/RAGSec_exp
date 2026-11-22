"""External evaluation metadata stays outside DefenseRequest and all adapters."""
from collections import Counter
from dataclasses import dataclass
import json

from .adapters.replay import parse_request, load_replay
from .engine import DefenseEngine
from .fingerprint import request_key
from .optional_ml.data import read_rows
from .optional_ml.metrics import classification_metrics, ratio
from .report import report_dict
from .types import DefenseRequest, DefenseStatus, ExecutionStatus, QueryPolicy, ResponseState
from .workspace import local_path

ATTACK_TYPES = ("Jamming Instruction Injection", "Jamming Oracle Generated", "Jamming BBO", "MutedRAG", "TabooRAG")


@dataclass(frozen=True)
class EvaluationRecord:
    record_id: str
    query_id: str
    request: DefenseRequest
    evaluation: dict
    data_kind: str
    replay_file: str | None = None

    def __post_init__(self):
        if not all(isinstance(value, str) and value for value in (self.record_id, self.query_id)):
            raise ValueError("record and query IDs must be nonempty strings")
        if type(self.request) is not DefenseRequest or self.data_kind not in ("copied_real", "synthetic"):
            raise ValueError("explicit request and data provenance required")
        if not isinstance(self.evaluation, dict):
            raise ValueError("external evaluation metadata must be an object")


def load_evaluation_records(path):
    records = []
    for row in read_rows(path):
        if set(row) - {"schema_version", "record_id", "query_id", "request", "evaluation", "data_kind", "replay_file"} or row["schema_version"] != 1:
            raise ValueError("invalid evaluation record schema")
        if row["data_kind"] not in ("copied_real", "synthetic"):
            raise ValueError("explicit data provenance required")
        metadata = row.get("evaluation", {})
        if not isinstance(metadata, dict) or set(metadata) - {"attack_type", "attack_doc_ids", "attack_spans", "reference_answer", "initial_response_label", "legitimate_refusal", "clean", "metadata"}:
            raise ValueError("unknown external evaluation metadata")
        if metadata.get("attack_type") not in (None, *ATTACK_TYPES):
            raise ValueError("unsupported evaluation attack_type")
        if "initial_response_label" in metadata:
            ResponseState(metadata["initial_response_label"])
        if any(type(metadata[key]) is not bool for key in ("legitimate_refusal", "clean") if key in metadata):
            raise ValueError("evaluation flags must be booleans")
        records.append(EvaluationRecord(row["record_id"], row["query_id"], parse_request(row["request"]), metadata,
                                        row["data_kind"], row.get("replay_file")))
    if len({r.record_id for r in records}) != len(records):
        raise ValueError("duplicate evaluation record_id")
    return records


MEASURES = ("observer_classification", "cause_document_identification", "cause_span_identification",
    "nonrefusal_transition", "grounded_correct_recovery", "unnecessary_clean_changes",
    "legitimate_refusal_preserved", "additional_calls", "tokens", "latency_ms",
    "budget_exhaustion", "provider_blocks", "execution_errors", "refusal_preserved_after_verification_failure",
    "overall_availability_recovery")


def not_run(records=0, reason="no_explicit_copied_records"):
    return {"status": "REAL_ATTACK_EVALUATION_NOT_RUN", "records_received": records, "reason": reason,
            "metrics": {name: None for name in MEASURES}, "attacks": {name: None for name in ATTACK_TYPES}}


def evaluate_records(records, engine_factory, *, correctness_evaluator=None, measurement="software_check"):
    """Factories receive no evaluation record or label. The scorer is external only.

    correctness_evaluator(query, final_response, reference_answer) may return bool or
    None. It is never automatically supplied or called by the defense engine.
    """
    if not records:
        return not_run()
    if measurement not in ("software_check", "real_record_evaluation"):
        raise ValueError("explicit known measurement mode required")
    if measurement == "real_record_evaluation" and any(r.data_kind != "copied_real" for r in records):
        raise ValueError("synthetic data cannot be reported as real attack evaluation")
    reports, truth, predicted = [], [], []
    observer_label_count = 0
    clean_changes, clean_total = 0, 0
    legitimate_kept, legitimate_total = 0, 0
    transitioned, refusal_total, grounded_correct, gold_measured = 0, 0, 0, 0
    failed_kept, failed_total, recovered, allow_total, exhausted = 0, 0, 0, 0, 0
    doc_tp = doc_pred = doc_actual = span_overlap = span_pred = span_actual = 0
    doc_measured = span_measured = 0
    total_attempts = tokens = latency = 0
    grounding_calls = 0
    grounding_tokens = 0
    candidate_transitions = 0
    grounding_latency = 0.0
    execution = Counter()
    attack_counts = Counter()
    for record in records:
        engine = engine_factory()
        result = engine.repair(record.request)
        if measurement == "real_record_evaluation" and result.mode.value in ("mock", "replay"):
            raise ValueError("mock/replay cannot be reported as real attack evaluation")
        metadata = record.evaluation
        success = result.status in (DefenseStatus.PATCH_APPLIED, DefenseStatus.RETRY_RECOVERY)
        initial_label = metadata.get("initial_response_label")
        initial = next((t.observation for t in result.trace if t.phase == "initial" and t.observation), None)
        if initial_label and record.request.initial_response.execution_status == ExecutionStatus.OK:
            observer_label_count += 1
            if initial is not None:
                truth.append(initial_label)
                predicted.append(initial.state.value)
        eligible = record.request.query_policy == QueryPolicy.ALLOW
        if eligible:
            allow_total += 1
            recovered += int(success)
        gold = None
        if correctness_evaluator is not None and "reference_answer" in metadata:
            gold = correctness_evaluator(record.request.query, result.final_response.text, metadata["reference_answer"])
            if gold is not None and type(gold) is not bool:
                raise ValueError("external correctness scorer must return bool or None")
        if eligible and initial_label == "REFUSAL" and record.request.initial_response.execution_status == ExecutionStatus.OK:
            refusal_total += 1
            transitioned += int(success)
            candidate_transitions += int(any(t.observation is not None and t.observation.state == ResponseState.ANSWER
                and t.phase != "initial" for t in result.trace))
            if gold is not None:
                gold_measured += 1
                grounded_correct += int(success and result.grounding_passed and gold)
        if metadata.get("clean"):
            clean_total += 1
            clean_changes += int(result.intervention_applied)
        if metadata.get("legitimate_refusal"):
            legitimate_total += 1
            legitimate_kept += int(result.final_response is record.request.initial_response)
        failures = any(t.phase == "grounding" and t.details.get("status") != "PASS" for t in result.trace)
        if failures and initial_label == "REFUSAL":
            failed_total += 1
            failed_kept += int(result.final_response is record.request.initial_response)
        if "attack_doc_ids" in metadata:
            actual, changed = set(metadata["attack_doc_ids"]), {p.doc_id for p in result.patches}
            doc_measured += 1
            doc_tp += len(actual & changed)
            doc_pred += len(changed)
            doc_actual += len(actual)
        if "attack_spans" in metadata:
            # Union character intervals to avoid double-counting overlapping labels.
            docs = {d.doc_id: d for d in record.request.documents}
            actual_chars, changed_chars = set(), set()
            for span in metadata["attack_spans"]:
                doc, field = docs[span["doc_id"]], span["field"]
                start, end = span["start_char"], span["end_char"]
                if field not in ("title", "text") or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(getattr(doc, field)):
                    raise ValueError("invalid external original-coordinate span")
                actual_chars.update((doc.doc_id, field, i) for i in range(start, end))
            for p in result.patches:
                fields = ("title", "text") if p.operation.value == "DROP_DOCUMENT" else (p.field,)
                for field in fields:
                    start, end = (0, len(getattr(docs[p.doc_id], field))) if p.operation.value == "DROP_DOCUMENT" else (p.start_char, p.end_char)
                    changed_chars.update((p.doc_id, field, i) for i in range(start, end))
            span_measured += 1
            span_overlap += len(actual_chars & changed_chars)
            span_pred += len(changed_chars)
            span_actual += len(actual_chars)
        total_attempts += result.usage.generation_attempts
        grounding_calls += result.usage.grounding_verifications
        if grounding_tokens is not None:
            incoming, outgoing = result.usage.grounding_input_tokens, result.usage.grounding_output_tokens
            grounding_tokens = grounding_tokens + incoming + outgoing if incoming is not None and outgoing is not None else None
        grounding_latency += result.usage.grounding_latency_ms
        tokens += result.usage.input_tokens + result.usage.output_tokens
        latency += result.usage.generation_latency_ms
        exhausted += int(result.search_truncated or result.status == DefenseStatus.BUDGET_EXHAUSTED)
        execution[record.request.initial_response.execution_status.value] += 1
        for event in result.trace:
            if event.execution_status is not None and event.reason == "generation_attempt":
                execution[event.execution_status.value] += 1
            if event.phase == "grounding" and event.details.get("execution_status"):
                execution[event.details["execution_status"]] += 1
        if metadata.get("attack_type"):
            attack_counts[metadata["attack_type"]] += 1
        reports.append({"record_id": record.record_id, "runtime": report_dict(result),
                        "grounding_passed": result.grounding_passed, "gold_correctness": gold})
    metrics = {"observer_classification": classification_metrics(truth, predicted) if truth else None,
        "observer_classification_coverage": ratio(len(truth), observer_label_count),
        "cause_document_identification": {"precision": ratio(doc_tp, doc_pred), "recall": ratio(doc_tp, doc_actual), "records": doc_measured} if doc_measured else None,
        "cause_span_identification": {"precision": ratio(span_overlap, span_pred), "recall": ratio(span_overlap, span_actual), "records": span_measured} if span_measured else None,
        "nonrefusal_transition": ratio(transitioned, refusal_total),
        "candidate_nonrefusal_observed": ratio(candidate_transitions, refusal_total),
        "grounded_correct_recovery": ratio(grounded_correct, gold_measured) if gold_measured else None,
        "gold_scoring_coverage": ratio(gold_measured, refusal_total),
        "unnecessary_clean_changes": ratio(clean_changes, clean_total),
        "legitimate_refusal_preserved": ratio(legitimate_kept, legitimate_total),
        "additional_calls": {"generation": total_attempts, "grounding": grounding_calls, "records": len(records)},
        "tokens": {"generation": tokens, "grounding": grounding_tokens},
        "latency_ms": {"generation": latency, "grounding": grounding_latency},
        "budget_exhaustion": ratio(exhausted, len(records)), "provider_blocks": execution["PROVIDER_BLOCK"],
        "execution_errors": {k: v for k, v in execution.items() if k != "OK"},
        "refusal_preserved_after_verification_failure": ratio(failed_kept, failed_total),
        "overall_availability_recovery": ratio(recovered, allow_total)}
    return {"status": "SOFTWARE_CHECK_ONLY" if measurement == "software_check" else "REAL_RECORD_EVALUATION",
        "records_received": len(records), "metrics": metrics, "records": reports,
        "attacks": {name: {"records": attack_counts[name]} if attack_counts[name] else None for name in ATTACK_TYPES},
        "limitations": ["Edited/labelled region overlap is not proof of maliciousness or causal attribution.",
                        "Maintained refusals are not availability recovery successes.",
                        "Observer transition and runtime grounding do not imply gold correctness."]}


def evaluate_copied_file(input_path, config):
    if input_path is None:
        return not_run()
    records = load_evaluation_records(input_path)
    if not records or any(not r.replay_file for r in records):
        return not_run(len(records), "complete_replay_or_explicit_programmatic_adapters_required")
    factories = []
    for record in records:
        request, generator, observer, verifier = load_replay(record.replay_file)
        if request_key(request) != request_key(record.request):
            raise ValueError("evaluation request does not match the explicit replay")
        factories.append(DefenseEngine(generator=generator, observer=observer, verifier=verifier, config=config))
    engines = iter(factories)
    report = evaluate_records(records, lambda: next(engines))
    report["real_attack_evaluation"] = "REAL_ATTACK_EVALUATION_NOT_RUN"
    return report
