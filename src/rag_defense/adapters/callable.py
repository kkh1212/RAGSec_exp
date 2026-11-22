"""Callbacks must make at most one external request and perform no hidden retries."""
from ..types import ExecutionMode, GenerationResult, Observation, VerificationResult


class CallableGenerator:
    def __init__(self, callback, *, fingerprint: str, version: str = "callable-generator-v1",
                 mode: ExecutionMode = ExecutionMode.EXTERNAL):
        self.callback, self.fingerprint, self.version = callback, fingerprint, version
        self.mode = ExecutionMode(mode)

    def generate(self, query, documents, *, fresh, replicate_id):
        result = self.callback(query, documents, fresh=fresh, replicate_id=replicate_id)
        if not isinstance(result, GenerationResult):
            raise TypeError("generator callback must return GenerationResult")
        return result


class CallableObserver:
    def __init__(self, callback, *, version: str = "callable-observer-v1",
                 mode: ExecutionMode = ExecutionMode.EXTERNAL):
        self.callback, self.version, self.mode = callback, version, ExecutionMode(mode)
        self.configured = callback is not None
        self.readiness = "CONNECTED_CALLBACK" if self.configured else "UNCONFIGURED"

    def observe(self, query, response):
        if not self.configured:
            raise RuntimeError("OBSERVER_NOT_CONFIGURED")
        result = self.callback(query, response)
        if not isinstance(result, Observation):
            raise TypeError("observer callback must return Observation")
        return result


class CallableVerifier:
    def __init__(self, callback, *, version: str = "callable-verifier-v1",
                 mode: ExecutionMode = ExecutionMode.EXTERNAL):
        self.callback, self.version, self.mode = callback, version, ExecutionMode(mode)
        self.configured = callback is not None
        self.readiness = ("MOCK" if self.mode == ExecutionMode.MOCK else "CONNECTED_CALLBACK") if self.configured else "UNCONFIGURED"

    def verify(self, query, response, evidence):
        if not self.configured:
            return VerificationResult("NOT_CONFIGURED", verifier_version=self.version)
        result = self.callback(query, response, evidence)
        if not isinstance(result, VerificationResult):
            raise TypeError("verifier callback must return VerificationResult")
        return result
