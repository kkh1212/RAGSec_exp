from dataclasses import replace
from .types import ExecutionMode, VerificationResult, VerificationStatus, RemainingSpan


class UnconfiguredVerifier:
    readiness = "UNCONFIGURED"
    configured = False
    version = "unconfigured-verifier-v1"
    mode = ExecutionMode.MOCK

    def verify(self, query, response, evidence):
        return VerificationResult(VerificationStatus.NOT_CONFIGURED, verifier_version=self.version)


def validate_citations(result: VerificationResult, evidence: tuple[RemainingSpan, ...]) -> VerificationResult:
    """One quote must fit entirely within ONE surviving original interval."""
    if result.status != VerificationStatus.PASS:
        return result
    if result.addresses_query is not True or result.supported is not True or not result.citations:
        return replace(result, status=VerificationStatus.FAIL, reason="missing_semantic_checks_or_citations")
    for citation in result.citations:
        if (not citation.quote or type(citation.start_char) is not int or
                type(citation.end_char) is not int or citation.start_char >= citation.end_char):
            return replace(result, status=VerificationStatus.FAIL, reason="invalid_citation_range")
        matched = any(
            (span.doc_id, span.field) == (citation.doc_id, citation.field)
            and span.start_char <= citation.start_char < citation.end_char <= span.end_char
            and span.text[citation.start_char - span.start_char:citation.end_char - span.start_char] == citation.quote
            for span in evidence)
        if not matched:
            return replace(result, status=VerificationStatus.FAIL, reason="citation_not_in_one_remaining_original_span")
    return result
