"""SYNTHETIC FIXTURES ONLY. Explicit finite context tables, not attack detectors.

Fixture-specific unit indices never enter DefenseRequest or the production engine.
"""
from dataclasses import dataclass
from itertools import combinations
from ..fingerprint import generation_key, observation_key, verification_key, make_generation_fingerprint
from ..patch import apply_patches, remaining_spans, full_document_patches, deletion_patches
from ..segment import segment_document, units_to_patches
from ..types import (Document, DefenseRequest, GenerationResult, ExecutionMode, ExecutionStatus,
                     Observation, ResponseState, QueryPolicy, Citation, VerificationResult,
                     VerificationStatus, TokenUsage)

MOCK_FINGERPRINT = make_generation_fingerprint(model="synthetic-table", endpoint="offline://fixture",
    prompt="fixed synthetic context table v1", settings={"sampling": "scripted"}, renderer_version="mock-v1")
QUERY = "시험 자료에서 별 A의 분류는 무엇인가?"
ANSWER = "별 A는 청색 항성입니다."
REFUSAL = "제공된 조건에서는 응답을 제공하지 않겠습니다."
UNCERTAIN = "분류에 관해서 더 살펴봐야 할 수도 있습니다."
OTHER = "시험 자료라는 표현을 다시 적었습니다."
QUOTE = "별 A는 청색 항성이다."


def generated(text, status=ExecutionStatus.OK):
    return GenerationResult(text, status, MOCK_FINGERPRINT, TokenUsage(provider_attempts=0))


class MockGenerator:
    mode = ExecutionMode.MOCK
    version = "mock-generator-v1"

    def __init__(self, table, fingerprint=MOCK_FINGERPRINT):
        self.fingerprint, self.table, self.calls, self.counts = fingerprint, dict(table), [], {}

    def key(self, query, documents):
        return generation_key(query, documents, self.fingerprint, self.version)

    def generate(self, query, documents, *, fresh, replicate_id):
        key = self.key(query, documents)
        self.calls.append(dict(query=query, documents=documents, fresh=fresh, replicate_id=replicate_id, key=key))
        values = self.table.get(key)
        if values is None:
            return GenerationResult("", ExecutionStatus.API_ERROR, self.fingerprint, TokenUsage(provider_attempts=0))
        index = self.counts.get(key, 0)
        self.counts[key] = index + 1
        # Fixture scripts explicitly repeat their last state; replay never does this.
        return values[min(index, len(values) - 1)]


class MockObserver:
    configured = True
    mode = ExecutionMode.MOCK
    version = "mock-observer-v1"

    def __init__(self, table=None):
        self.table = dict(table or {})
        self.calls = []

    def observe(self, query, response):
        self.calls.append((query, response))
        return self.table.get(observation_key(query, response, self.version),
            Observation(ResponseState.UNCERTAIN, 0.0, self.version))


class MockVerifier:
    configured = True
    mode = ExecutionMode.MOCK
    version = "mock-verifier-v1"

    def __init__(self, table=None):
        self.table = dict(table or {})
        self.calls = []

    def verify(self, query, response, evidence):
        self.calls.append((query, response, evidence))
        return self.table.get(verification_key(query, response, evidence, self.version),
            VerificationResult(VerificationStatus.UNCERTAIN, verifier_version=self.version,
                               reason="synthetic_table_miss"))


@dataclass(frozen=True)
class MockScenario:
    name: str
    request: DefenseRequest
    generator: MockGenerator
    observer: MockObserver
    verifier: MockVerifier


SCENARIOS = ("success", "title", "interaction", "grounding_failure", "verifier_uncertain",
             "retry", "unstable_baseline", "uncertain_baseline", "evidence_loss", "deletion_sensitive", "clean")


def build_scenario(name="success") -> MockScenario:
    if name not in SCENARIOS:
        raise ValueError("unknown synthetic scenario")
    if name == "evidence_loss":
        return _evidence_loss_scenario()
    documents = (
        Document("d1", "관측표", QUOTE + "\n관측 기록 7.", 1, 0.9),
        Document("d2", "보조 메모", "첫째 기록.\n둘째 기록.\n셋째 기록.", 2, 0.8),
        Document("d3", "목록", "문서 목록의 끝입니다.", 3, 0.7),
    )
    units = segment_document(documents[1])
    # Labels exist ONLY in this synthetic fixture construction.
    necessary_masked_units = {0} if name == "title" else ({0, 3} if name == "interaction" else {3})
    # Exact structural-unit membership defines this fixture's finite truth table.
    table, verification_table = {}, {}

    def register(patches, text):
        context = apply_patches(documents, patches)
        key = generation_key(QUERY, context, MOCK_FINGERPRINT, MockGenerator.version)
        table[key] = (generated(text),)
        evidence = remaining_spans(documents, patches)
        status = VerificationStatus.FAIL if name == "grounding_failure" else (
            VerificationStatus.UNCERTAIN if name == "verifier_uncertain" else VerificationStatus.PASS)
        citation = Citation("d1", "text", 0, len(QUOTE), QUOTE)
        verification_table[verification_key(QUERY, ANSWER, evidence, MockVerifier.version)] = VerificationResult(
            status, status == VerificationStatus.PASS, status == VerificationStatus.PASS,
            (citation,) if status == VerificationStatus.PASS else (), MockVerifier.version, "synthetic_only")

    for count in range(len(units) + 1):
        for indices in combinations(range(len(units)), count):
            patches = units_to_patches(documents, tuple(units[i] for i in indices))
            answer = necessary_masked_units.issubset(indices)
            text = ANSWER if answer else REFUSAL
            register(patches, text)
            register(deletion_patches(patches), REFUSAL if name == "deletion_sensitive" else text)
    for doc in (documents[0], documents[2]):
        register(full_document_patches(doc), REFUSAL)
        register(deletion_patches(full_document_patches(doc)), REFUSAL)
    original_key = generation_key(QUERY, documents, MOCK_FINGERPRINT, MockGenerator.version)
    if name == "retry":
        table[original_key] = (generated(ANSWER),)
    elif name == "unstable_baseline":
        table[original_key] = (generated(REFUSAL), generated(ANSWER))
    elif name == "uncertain_baseline":
        table[original_key] = (generated(UNCERTAIN),)
    observer_table = {observation_key(QUERY, text, MockObserver.version): Observation(state, confidence, MockObserver.version)
        for text, state, confidence in ((ANSWER, ResponseState.ANSWER, 1.0), (REFUSAL, ResponseState.REFUSAL, 1.0),
            (UNCERTAIN, ResponseState.UNCERTAIN, 0.4), (OTHER, ResponseState.OTHER, 1.0))}
    initial = generated(ANSWER if name == "clean" else (UNCERTAIN if name == "uncertain_baseline" else REFUSAL))
    request = DefenseRequest(f"synthetic-{name}", QUERY, documents, initial, MOCK_FINGERPRINT, QueryPolicy.ALLOW)
    return MockScenario(name, request, MockGenerator(table), MockObserver(observer_table), MockVerifier(verification_table))


def _evidence_loss_scenario():
    documents = (Document("d1", "공유된 자료", QUOTE + "\n부록 기록.", 1),)
    # Here the useful evidence and the synthetic refusal switch share one document.
    # The original refuses; full masking removes the evidence and STILL refuses.
    table = {generation_key(QUERY, context, MOCK_FINGERPRINT, MockGenerator.version): (generated(REFUSAL),)
             for context in (documents, apply_patches(documents, full_document_patches(documents[0])))}
    observations = {observation_key(QUERY, REFUSAL, MockObserver.version):
                    Observation(ResponseState.REFUSAL, 1.0, MockObserver.version)}
    request = DefenseRequest("synthetic-evidence-loss", QUERY, documents, generated(REFUSAL),
                             MOCK_FINGERPRINT, QueryPolicy.ALLOW)
    return MockScenario("evidence_loss", request, MockGenerator(table), MockObserver(observations), MockVerifier())
