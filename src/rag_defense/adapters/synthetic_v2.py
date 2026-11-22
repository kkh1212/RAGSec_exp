"""Offline algorithm fixtures only; these are not five real attack evaluations."""
from .callable import CallableGenerator, CallableObserver, CallableVerifier
from .mock import MockScenario
from ..types import Document, DefenseRequest, GenerationResult, Observation, Citation, VerificationResult, TokenUsage

V2_SCENARIOS = ("drop_only", "partial_evidence", "pair_only", "unpunctuated", "wrong_answer", "policy_note")
FACT = "The measured value is seven."


def build_v2_scenario(name):
    if name not in V2_SCENARIOS:
        raise ValueError("unknown synthetic v2 scenario")
    documents = (Document("e", "", FACT, 0), Document("x", "", "opaque 481.", 1))
    if name == "partial_evidence":
        documents = (Document("e", "", FACT + " opaque 481.", 0),)
    if name == "unpunctuated":
        documents = (Document("e", "", FACT, 0), Document("x", "", "a" * 240 + "b" * 240 + "c" * 240, 1))
    if name == "pair_only":
        documents += (Document("y", "", "opaque 592.", 2),)
    if name == "policy_note":
        documents = (Document("e", "Caution: instrument limitations", FACT, 0),)
    fingerprint = "synthetic-v2-fixed-settings"
    def generated(text):
        return GenerationResult(text, "OK", fingerprint, TokenUsage(provider_attempts=0))
    def generate(query, contexts, *, fresh, replicate_id):
        # Cause information lives only in this fixture callback, never on Document.
        support = any(FACT in d.text for d in contexts)
        if name == "drop_only":
            answer = len(contexts) == 1 and contexts[0].doc_id == "e"
        elif name == "unpunctuated":
            answer = support and any("a" * 240 in d.text and "c" * 240 in d.text and "b" * 240 not in d.text for d in contexts)
        else:
            answer = support and not any("opaque" in d.text for d in contexts)
        return generated("wrong" if answer and name == "wrong_answer" else ("answer" if answer else "refusal"))
    def observe(query, response):
        return Observation({"answer": "ANSWER", "wrong": "ANSWER", "refusal": "REFUSAL"}[response], 1, "synthetic-observer-v2")
    def verify(query, response, evidence):
        span = next((s for s in evidence if FACT in s.text), None)
        passed = response == "answer" and span is not None
        citations = (Citation(span.doc_id, span.field, span.start_char, span.end_char, span.text),) if passed else ()
        return VerificationResult("PASS" if passed else "FAIL", passed, passed, citations, "synthetic-verifier-v2")
    request = DefenseRequest("synthetic-" + name, "What value was measured?", documents,
        generated("answer" if name == "policy_note" else "refusal"), fingerprint, "ALLOW")
    return MockScenario(name, request, CallableGenerator(generate, fingerprint=fingerprint, mode="mock"),
        CallableObserver(observe, version="synthetic-observer-v2", mode="mock"),
        CallableVerifier(verify, version="synthetic-verifier-v2", mode="mock"))
