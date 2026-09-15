from typing import Protocol
from .types import (Document, ExecutionMode, GenerationResult, Observation,
                    RemainingSpan, VerificationResult)


class Generator(Protocol):
    fingerprint: str
    version: str
    mode: ExecutionMode

    def generate(self, query: str, documents: tuple[Document, ...], *,
                 fresh: bool, replicate_id: str | None) -> GenerationResult: ...


class ResponseObserver(Protocol):
    configured: bool
    version: str
    mode: ExecutionMode

    def observe(self, query: str, response: str) -> Observation: ...


class GroundingVerifier(Protocol):
    configured: bool
    version: str
    mode: ExecutionMode

    def verify(self, query: str, response: str,
               evidence: tuple[RemainingSpan, ...]) -> VerificationResult: ...
