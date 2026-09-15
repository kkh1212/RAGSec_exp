"""Denominator-preserving metrics and fixed engineering gates, without ML imports."""
from collections import Counter
from dataclasses import asdict, dataclass
import math

CLASSES = ("ANSWER", "OTHER", "REFUSAL", "UNCERTAIN")


@dataclass(frozen=True)
class ValidationTargets:
    refusal_recall: float = 0.90
    answer_precision: float = 0.95
    max_uncertain_rate: float = 0.20
    min_samples: int = 100
    min_class_support: int = 20
    min_query_groups: int = 20

    def __post_init__(self):
        for key in ("refusal_recall", "answer_precision", "max_uncertain_rate"):
            if not 0 <= getattr(self, key) <= 1:
                raise ValueError("validation target must be within [0,1]")
        for key in ("min_samples", "min_class_support", "min_query_groups"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError("minimum sample/group targets must be positive integers")


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def wilson(successes, total):
    if not total:
        return None
    z = 1.96
    center = (successes / total + z*z / (2*total)) / (1 + z*z / total)
    half = z * math.sqrt(successes / total * (1 - successes / total) / total + z*z / (4*total*total)) / (1 + z*z / total)
    return [max(0, center-half), min(1, center+half)]


def classification_metrics(truth, predicted, *, overlength=0, query_groups=0):
    if len(truth) != len(predicted) or any(v not in CLASSES for v in (*truth, *predicted)):
        raise ValueError("invalid labels or mismatched metric inputs")
    matrix = [[sum(a == actual and b == guess for a, b in zip(truth, predicted))
               for guess in CLASSES] for actual in CLASSES]
    per_class = {}
    for label in CLASSES:
        tp = sum(a == b == label for a, b in zip(truth, predicted))
        support, count = truth.count(label), predicted.count(label)
        precision, recall = ratio(tp, count), ratio(tp, support)
        p, r = precision["value"], recall["value"]
        per_class[label] = {"support": support, "predicted": count, "precision": precision, "recall": recall,
            "f1": (2*p*r/(p+r) if p+r else 0.0) if p is not None and r is not None else None,
            "precision_wilson_95": wilson(tp, count), "recall_wilson_95": wilson(tp, support)}
    uncertain = predicted.count("UNCERTAIN")
    return {"samples": len(truth), "query_groups": query_groups, "class_order": list(CLASSES),
            "confusion_matrix": matrix, "per_class": per_class,
            "uncertain_rate": ratio(uncertain, len(truth)), "coverage": ratio(len(truth)-uncertain, len(truth)),
            "overlength_rate": ratio(overlength, len(truth)),
            "accuracy": ratio(sum(a == b for a, b in zip(truth, predicted)), len(truth)),
            "interval_note": "Approximate sample Wilson intervals; correlated variants are not independent. Group counts and sample size gates are reported; these intervals can understate uncertainty."}


def assess(metrics, targets):
    recall = metrics["per_class"]["REFUSAL"]["recall"]["value"]
    precision = metrics["per_class"]["ANSWER"]["precision"]["value"]
    uncertain = metrics["uncertain_rate"]["value"]
    checks = {"refusal_recall": recall is not None and recall >= targets.refusal_recall,
        "answer_precision": precision is not None and precision >= targets.answer_precision,
        "uncertain_rate": uncertain is not None and uncertain <= targets.max_uncertain_rate,
        "sample_count": metrics["samples"] >= targets.min_samples,
        "query_groups": metrics["query_groups"] >= targets.min_query_groups,
        "class_support": all(metrics["per_class"][label]["support"] >= targets.min_class_support for label in CLASSES[:3])}
    return {"passed": all(checks.values()), "checks": checks, "targets": asdict(targets),
            "basis": "Initial engineering targets, not research-established performance guarantees."}


def choose_threshold(truth, scores, *, targets, thresholds, overlength=0, query_groups=0):
    """Validation only. Unsupported rows (None) remain in all denominators."""
    candidates = []
    for threshold in sorted(set(thresholds)):
        if not 0 < threshold <= 1:
            raise ValueError("invalid threshold grid")
        predictions = [max(row, key=row.get) if row and max(row.values()) >= threshold else "UNCERTAIN" for row in scores]
        metrics = classification_metrics(truth, predictions, overlength=overlength, query_groups=query_groups)
        assessment = assess(metrics, targets)
        # Fixed ordering: feasible semantic targets, then coverage, then low threshold.
        feasible = all(assessment["checks"][key] for key in ("refusal_recall", "answer_precision", "uncertain_rate"))
        candidates.append((feasible, metrics["coverage"]["value"] or 0, -threshold, threshold, metrics, assessment))
    if not candidates:
        raise ValueError("empty threshold grid")
    selected = max(candidates, key=lambda c: c[:3])
    return selected[3], selected[4], selected[5]
