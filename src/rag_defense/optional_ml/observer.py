"""Fixed local encoder + explicitly trained classifier. Provisional is opt-in for evaluation."""
import json
import math
from ..fingerprint import digest
from ..types import ExecutionMode, Observation, ResponseState, Readiness
from ..workspace import local_path
from .common import (FEATURE_VERSION, LABELS, model_tree_hash, file_hash,
                     load_embedding, encode_pairs, probabilities, input_support)
from .metrics import ValidationTargets, assess


def inspect_artifact(artifact_dir):
    directory = local_path(artifact_dir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    classifier = json.loads((directory / "classifier.json").read_text(encoding="utf-8"))
    if (manifest["schema_version"] != 2 or manifest["trained"] is not True
            or manifest["training_data_kind"] != "human_verified_real"
            or manifest["feature_version"] != FEATURE_VERSION or classifier["classes"] != list(LABELS)
            or not math.isfinite(manifest["threshold"]) or not 0 < manifest["threshold"] <= 1
            or not manifest["validation"]["group_disjoint"]
            or manifest["threshold_selection"]["split"] != "validation"
            or manifest["threshold_selection"]["test_used"] is not False
            or manifest["preprocessing"] != {"text_normalization": "none", "query_response": "separate",
                "long_input": "UNCERTAIN_no_truncation", "features": ["query", "response", "abs_query_minus_response", "query_times_response"]}
            or manifest["label_mapping"] != {label: i for i, label in enumerate(LABELS)}):
        raise ValueError("untrained or incompatible artifact")
    if file_hash(directory / "classifier.json") != manifest["classifier_sha256"]:
        raise ValueError("classifier hash mismatch")
    groups = [set(manifest["splits"][name]["query_ids"]) for name in ("train", "validation", "test")]
    samples = [set(manifest["splits"][name]["sample_ids"]) for name in ("train", "validation", "test")]
    if any(groups[i] & groups[j] or samples[i] & samples[j] for i in range(3) for j in range(i)):
        raise ValueError("artifact splits overlap")
    width = manifest["classifier"]["feature_dimension"]
    if (type(width) is not int or width < 4 or width % 4 or len(classifier["coefficients"]) != 3
            or len(classifier["intercept"]) != 3 or any(len(row) != width for row in classifier["coefficients"])
            or not all(type(v) in (int, float) and math.isfinite(v) for row in classifier["coefficients"] for v in row)
            or not all(type(v) in (int, float) and math.isfinite(v) for v in classifier["intercept"])):
        raise ValueError("invalid finite classifier dimensions")
    embedding = manifest["embedding"]
    if (embedding["device"] != "cpu" or embedding["frozen"] is not True or embedding["local_files_only"] is not True
            or embedding["normalize_embeddings"] is not True or not embedding["model_id"] or not embedding["revision"]
            or not 1 <= embedding["max_seq_length"] <= 512 or not 1 <= embedding["batch_size"] <= 32):
        raise ValueError("incompatible encoder configuration")
    readiness = Readiness(manifest["readiness"])
    if readiness not in (Readiness.TRAINED_PROVISIONAL, Readiness.VALIDATED):
        raise ValueError("invalid trained readiness")
    if readiness == Readiness.VALIDATED:
        targets = ValidationTargets(**manifest["targets"])
        if (manifest["test_evaluation_completed"] is not True or not assess(manifest["validation"], targets)["passed"]
                or not assess(manifest["test"], targets)["passed"]):
            raise ValueError("validated claim does not pass recorded fixed gates")
    return manifest, classifier


class MLObserver:
    mode = ExecutionMode.LOCAL

    def __init__(self, artifact_dir=None, *, embedding_model_dir=None, allow_provisional=False):
        self.configured = False
        self.readiness = Readiness.UNCONFIGURED
        self.version = "unconfigured-ml-observer-v2"
        self.unavailable_reason = "no_trained_artifact"
        if artifact_dir is None:
            return
        try:
            manifest, classifier = inspect_artifact(artifact_dir)
            self.manifest, self.classifier = manifest, classifier
            self.readiness = Readiness(manifest["readiness"])
            self.version = "ml-observer-v2:" + digest(manifest)
            if embedding_model_dir is None:
                raise ValueError("explicit project-local embedding_model_dir is required")
            if model_tree_hash(embedding_model_dir) != manifest["embedding"]["tree_hash"]:
                raise ValueError("embedding model fingerprint mismatch")
            self.model = load_embedding(embedding_model_dir, max_seq_length=manifest["embedding"]["max_seq_length"])
            if self.model.get_sentence_embedding_dimension() * 4 != manifest["classifier"]["feature_dimension"]:
                raise ValueError("encoder feature dimension mismatch")
            self.configured = self.readiness == Readiness.VALIDATED or allow_provisional
            self.unavailable_reason = "" if self.configured else "TRAINED_PROVISIONAL_not_validated_for_repair"
        except (ImportError, ValueError, OSError, KeyError, TypeError, RuntimeError, AttributeError):
            self.readiness = Readiness.INCOMPATIBLE_ARTIFACT
            self.unavailable_reason = "artifact_or_local_encoder_incompatible"

    def observe(self, query, response):
        if not self.configured:
            raise RuntimeError("OBSERVER_NOT_CONFIGURED")
        reason = input_support(self.model, query, response, self.manifest["embedding"]["max_seq_length"])
        if reason:
            return Observation(ResponseState.UNCERTAIN, 0.0, self.version, reason)
        try:
            features = encode_pairs(self.model, [(query, response)], self.manifest["embedding"]["batch_size"])
            scores = probabilities(features, self.classifier["coefficients"], self.classifier["intercept"])[0]
            index = int(scores.argmax())
            confidence = float(scores[index])
            state = ResponseState(self.classifier["classes"][index]) if confidence >= self.manifest["threshold"] else ResponseState.UNCERTAIN
            return Observation(state, confidence, self.version, "below_confidence_threshold" if state == ResponseState.UNCERTAIN else "")
        except Exception:
            return Observation(ResponseState.UNCERTAIN, 0.0, self.version, "unsupported_encoder_input")
