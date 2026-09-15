from dataclasses import dataclass, fields
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DefenseConfig:
    max_additional_generation_attempts: int = 48
    search_version: str = "v2"
    max_partial_probes: int = 12
    max_pair_probes: int = 20
    max_refinement_probes: int = 8
    max_changed_documents: int = 2
    enable_drop: bool = True
    enable_partial: bool = True
    enable_pairs: bool = True
    max_refinement_attempts: int = 8
    max_candidates_to_refine: int = 2
    max_grounding_verifications: int = 2
    max_masked_documents: int = 1
    concurrency: int = 1
    allow_live: bool = False
    min_observer_confidence: float = 0.8
    max_segment_chars: int = 240
    deletion_control: bool = True
    require_deletion_consistency: bool = False
    max_execution_retries: int = 0

    def __post_init__(self):
        for name in ("max_additional_generation_attempts", "max_refinement_attempts",
                     "max_candidates_to_refine", "max_grounding_verifications",
                     "max_execution_retries", "max_partial_probes", "max_pair_probes",
                     "max_refinement_probes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if type(self.max_segment_chars) is not int or self.max_segment_chars < 1:
            raise ValueError("max_segment_chars must be positive")
        if (type(self.concurrency) is not int or type(self.max_masked_documents) is not int
                or self.concurrency != 1 or self.max_masked_documents != 1):
            raise ValueError("this implementation supports concurrency=1 and max_masked_documents=1")
        if self.search_version not in ("v1", "v2"):
            raise ValueError("search_version must be v1 or v2")
        if type(self.max_changed_documents) is not int or self.max_changed_documents not in (1, 2):
            raise ValueError("only one or two changed documents are supported")
        if self.max_grounding_verifications > 2:
            raise ValueError("at most two final grounding verifications per request")
        if not 0 <= self.min_observer_confidence <= 1:
            raise ValueError("invalid observer confidence threshold")
        for name in ("allow_live", "deletion_control", "require_deletion_consistency",
                     "enable_drop", "enable_partial", "enable_pairs"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a boolean")
        if self.require_deletion_consistency and not self.deletion_control:
            raise ValueError("deletion consistency requires the deletion control")
        if self.search_version == "v2" and self.require_deletion_consistency:
            raise ValueError("v2 never requires another transformation to succeed; use v1 for legacy strict control")

    @property
    def final_generation_reserve(self) -> int:
        return 4 if self.search_version == "v2" else 2 + int(self.deletion_control)


def load_config(path: str | Path) -> DefenseConfig:
    """Read JSON, the explicitly supported YAML 1.2 subset (no YAML dependency)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {f.name for f in fields(DefenseConfig)}:
        raise ValueError("configuration must be an object of known DefenseConfig fields")
    return DefenseConfig(**data)
