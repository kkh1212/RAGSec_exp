"""Small immutable contracts. Evaluation labels intentionally have no input field."""
from dataclasses import dataclass, field as dc_field
from enum import Enum
import math
from typing import Any


class QueryPolicy(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"


class ResponseState(str, Enum):
    ANSWER = "ANSWER"
    REFUSAL = "REFUSAL"
    OTHER = "OTHER"
    UNCERTAIN = "UNCERTAIN"


class ExecutionStatus(str, Enum):
    OK = "OK"
    PROVIDER_BLOCK = "PROVIDER_BLOCK"
    RATE_LIMIT = "RATE_LIMIT"
    AUTH_ERROR = "AUTH_ERROR"
    INCOMPLETE = "INCOMPLETE"
    OTHER_API_ERROR = "OTHER_API_ERROR"
    API_ERROR = "API_ERROR"
    TIMEOUT = "TIMEOUT"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    REPLAY_MISS = "REPLAY_MISS"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"


class DefenseStatus(str, Enum):
    NO_REPAIR_NEEDED = "NO_REPAIR_NEEDED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    OBSERVER_NOT_CONFIGURED = "OBSERVER_NOT_CONFIGURED"
    RETRY_RECOVERY = "RETRY_RECOVERY"
    PATCH_APPLIED = "PATCH_APPLIED"
    NO_REPAIR_FOUND = "NO_REPAIR_FOUND"
    NO_VERIFIED_REPAIR = "NO_VERIFIED_REPAIR"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    UNSTABLE_BASELINE = "UNSTABLE_BASELINE"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class VerificationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNCERTAIN = "UNCERTAIN"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NOT_RUN = "NOT_RUN"
    ERROR = "ERROR"


class ExecutionMode(str, Enum):
    MOCK = "mock"
    REPLAY = "replay"
    EXTERNAL = "externally_connected"
    LOCAL = "local"


class Readiness(str, Enum):
    UNCONFIGURED = "UNCONFIGURED"
    NEEDS_LABELS = "NEEDS_LABELS"
    TRAINED_PROVISIONAL = "TRAINED_PROVISIONAL"
    VALIDATED = "VALIDATED"
    INCOMPATIBLE_ARTIFACT = "INCOMPATIBLE_ARTIFACT"


class Operation(str, Enum):
    MASK_DOCUMENT = "MASK_DOCUMENT"
    DROP_DOCUMENT = "DROP_DOCUMENT"
    MASK_SPAN = "MASK_SPAN"
    DROP_SPAN = "DROP_SPAN"


@dataclass(frozen=True, slots=True)
class Document:
    doc_id: str
    title: str
    text: str
    rank: int
    retrieval_score: float | None = None

    def __post_init__(self):
        if not isinstance(self.doc_id, str) or not self.doc_id:
            raise ValueError("doc_id must be a nonempty opaque string")
        if not isinstance(self.title, str) or not isinstance(self.text, str):
            raise TypeError("title and text must be strings")
        if type(self.rank) is not int or self.rank < 0:
            raise ValueError("rank must be a nonnegative integer; sequence order is authoritative")
        if self.retrieval_score is not None and not math.isfinite(self.retrieval_score):
            raise ValueError("retrieval_score must be finite")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    # Exactly one request per adapter invocation; retries belong to the engine.
    provider_attempts: int = 1

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in
               (self.input_tokens, self.output_tokens, self.provider_attempts)):
            raise ValueError("usage counts must be nonnegative integers")
        if self.provider_attempts > 1:
            raise ValueError("hidden adapter retries violate the single-attempt contract")


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    execution_status: ExecutionStatus
    generation_fingerprint: str
    usage: TokenUsage = dc_field(default_factory=TokenUsage)
    latency_ms: float = 0.0

    def __post_init__(self):
        object.__setattr__(self, "execution_status", ExecutionStatus(self.execution_status))
        if (not isinstance(self.text, str) or not isinstance(self.generation_fingerprint, str)
                or not self.generation_fingerprint):
            raise ValueError("text and generation fingerprint are required")
        if type(self.usage) is not TokenUsage:
            raise TypeError("usage must be TokenUsage")
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError("latency_ms must be nonnegative and finite")
        if self.execution_status == ExecutionStatus.OK and not self.text.strip():
            object.__setattr__(self, "execution_status", ExecutionStatus.EMPTY_RESPONSE)


@dataclass(frozen=True, slots=True)
class Observation:
    state: ResponseState
    confidence: float
    observer_version: str
    reason: str = ""

    def __post_init__(self):
        object.__setattr__(self, "state", ResponseState(self.state))
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must lie in [0, 1]")
        if not self.observer_version:
            raise ValueError("observer_version is required")


@dataclass(frozen=True, slots=True)
class Patch:
    doc_id: str
    field: str
    original_hash: str
    start_char: int
    end_char: int
    replacement: str = "[REDACTED]"
    operation: Operation = Operation.MASK_SPAN

    def __post_init__(self):
        object.__setattr__(self, "operation", Operation(self.operation))


@dataclass(frozen=True, slots=True)
class RemainingSpan:
    doc_id: str
    field: str
    start_char: int
    end_char: int
    text: str


@dataclass(frozen=True, slots=True)
class Citation:
    doc_id: str
    field: str
    start_char: int
    end_char: int
    quote: str


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    addresses_query: bool | None = None
    supported: bool | None = None
    citations: tuple[Citation, ...] = ()
    verifier_version: str = "unconfigured-v1"
    reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "status", VerificationStatus(self.status))
        object.__setattr__(self, "citations", tuple(self.citations))
        if any(value is not None and (type(value) is not int or value < 0)
               for value in (self.input_tokens, self.output_tokens)):
            raise ValueError("verification token counts must be nonnegative or unknown")


@dataclass(frozen=True, slots=True)
class DefenseRequest:
    request_id: str
    query: str
    documents: tuple[Document, ...]
    initial_response: GenerationResult
    generation_fingerprint: str
    query_policy: QueryPolicy = QueryPolicy.UNKNOWN

    def __post_init__(self):
        object.__setattr__(self, "documents", tuple(self.documents))
        object.__setattr__(self, "query_policy", QueryPolicy(self.query_policy))
        if not self.request_id or not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("request_id and a nonempty query are required")
        if not self.generation_fingerprint:
            raise ValueError("generation_fingerprint is required")
        if not all(type(d) is Document for d in self.documents):
            raise TypeError("documents must contain only Document instances")
        ids = [d.doc_id for d in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")


@dataclass(frozen=True, slots=True)
class UsageSummary:
    generation_attempts: int = 0
    refinement_attempts: int = 0
    grounding_verifications: int = 0
    observer_calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    partial_probes: int = 0
    pair_probes: int = 0
    generation_latency_ms: float = 0.0
    grounding_input_tokens: int | None = 0
    grounding_output_tokens: int | None = 0
    grounding_latency_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class TraceEvent:
    sequence: int
    phase: str
    reason: str
    patches: tuple[Patch, ...] = ()
    observation: Observation | None = None
    execution_status: ExecutionStatus | None = None
    cache_hit: bool = False
    fresh: bool = False
    replicate_id: str | None = None
    request_fingerprint: str | None = None
    details: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DefenseResult:
    status: DefenseStatus
    final_response: GenerationResult
    intervention_applied: bool
    patches: tuple[Patch, ...]
    candidate_documents: tuple[str, ...]
    verification_status: VerificationStatus
    trace: tuple[TraceEvent, ...]
    usage: UsageSummary
    limitations: tuple[str, ...]
    candidate_patches: tuple[tuple[Patch, ...], ...] = ()
    best_candidate_patches: tuple[Patch, ...] = ()
    verification: VerificationResult | None = None
    transformation_sensitive: bool | None = None
    search_truncated: bool = False
    changed_characters: int = 0
    original_request_fingerprint: str = ""
    mode: ExecutionMode = ExecutionMode.MOCK
    config: dict[str, Any] = dc_field(default_factory=dict)
    versions: dict[str, str] = dc_field(default_factory=dict)
    gold_correctness: None = None
    grounding_passed: bool = False
    search_complete: bool = False
    unexplored: tuple[dict[str, Any], ...] = ()
    termination_reason: str = ""
    candidate_details: tuple[dict[str, Any], ...] = ()
