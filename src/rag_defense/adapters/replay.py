"""Exact, complete JSONL recordings only; missing interventions are never inferred."""
import json
from ..fingerprint import (generation_key, observation_key, verification_key, request_key)
from ..types import (Document, DefenseRequest, GenerationResult, TokenUsage, Observation,
                     Citation, RemainingSpan, VerificationResult, VerificationStatus,
                     ResponseState, ExecutionStatus, ExecutionMode)
from ..version import CONTRACT_VERSION
from ..workspace import local_path


def parse_generation(data):
    data = dict(data)
    data["usage"] = TokenUsage(**data.get("usage", {}))
    return GenerationResult(**data)


def parse_request(data):
    data = dict(data)
    data["documents"] = tuple(Document(**d) for d in data["documents"])
    data["initial_response"] = parse_generation(data["initial_response"])
    # Unknown fields, including evaluation labels, fail the dataclass constructor.
    return DefenseRequest(**data)


def parse_verification(data):
    data = dict(data)
    data["citations"] = tuple(Citation(**c) for c in data.get("citations", ()))
    return VerificationResult(**data)


class ReplayGenerator:
    mode = ExecutionMode.REPLAY

    def __init__(self, records, *, fingerprint, version):
        self.fingerprint, self.version = fingerprint, version
        self.records, self.used_fresh = dict(records), set()

    def generate(self, query, documents, *, fresh, replicate_id):
        key = generation_key(query, documents, self.fingerprint, self.version,
                             fresh=fresh, replicate_id=replicate_id)
        if key not in self.records or (fresh and key in self.used_fresh):
            return GenerationResult("", ExecutionStatus.REPLAY_MISS, self.fingerprint, TokenUsage(provider_attempts=0))
        if fresh:
            self.used_fresh.add(key)
        return self.records[key]


class ReplayObserver:
    configured = True
    mode = ExecutionMode.REPLAY

    def __init__(self, records, *, version):
        self.records, self.version = dict(records), version

    def observe(self, query, response):
        return self.records.get(observation_key(query, response, self.version),
            Observation(ResponseState.UNCERTAIN, 0.0, self.version))


class ReplayVerifier:
    configured = True
    mode = ExecutionMode.REPLAY

    def __init__(self, records, *, version):
        self.records, self.version = dict(records), version

    def verify(self, query, response, evidence):
        return self.records.get(verification_key(query, response, evidence, self.version),
            VerificationResult(VerificationStatus.UNCERTAIN, verifier_version=self.version, reason="replay_miss"))


def load_replay(path):
    path = local_path(path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or rows[0].get("kind") != "request":
        raise ValueError("first JSONL record must be a request manifest")
    manifest = rows[0]
    if manifest.get("schema_version") != CONTRACT_VERSION:
        raise ValueError("replay schema version mismatch")
    request = parse_request(manifest["request"])
    if request_key(request) != manifest.get("request_fingerprint"):
        raise ValueError("request fingerprint mismatch")
    generations, observations, verifications = {}, {}, {}
    generator_version, observer_version, verifier_version = (
        manifest["generator_version"], manifest["observer_version"], manifest["verifier_version"])

    def insert(table, key, value):
        if key in table:
            raise ValueError("duplicate replay key: distinct repetitions need distinct replicate_id values")
        table[key] = value

    for row in rows[1:]:
        kind = row.get("kind")
        if kind == "generation":
            data = row["input"]
            expected = {"query", "documents", "generation_fingerprint", "generator_version",
                        "contract_version", "fresh", "replicate_id"}
            if set(data) != expected or data["contract_version"] != CONTRACT_VERSION:
                raise ValueError("invalid generation input schema")
            if (data["generator_version"] != generator_version or
                    data["generation_fingerprint"] != request.generation_fingerprint):
                raise ValueError("mixed generation configurations in recording")
            docs = tuple(Document(**d) for d in data["documents"])
            key = generation_key(data["query"], docs, data["generation_fingerprint"], generator_version,
                                 fresh=data["fresh"], replicate_id=data["replicate_id"])
            if key != row["request_fingerprint"]:
                raise ValueError("generation input fingerprint mismatch")
            if data["fresh"] and not data["replicate_id"]:
                raise ValueError("fresh records require a replicate_id")
            insert(generations, key, parse_generation(row["result"]))
        elif kind == "observation":
            result = Observation(**row["result"])
            key = observation_key(row["query"], row["response"], observer_version)
            if row["key"] != key or result.observer_version != observer_version:
                raise ValueError("observation fingerprint or version mismatch")
            insert(observations, key, result)
        elif kind == "verification":
            evidence = tuple(RemainingSpan(**s) for s in row["evidence"])
            result = parse_verification(row["result"])
            key = verification_key(row["query"], row["response"], evidence, verifier_version)
            if row["key"] != key or result.verifier_version != verifier_version:
                raise ValueError("verification fingerprint or version mismatch")
            insert(verifications, key, result)
        else:
            raise ValueError("unknown or repeated replay manifest record")
    return (request, ReplayGenerator(generations, fingerprint=request.generation_fingerprint, version=generator_version),
            ReplayObserver(observations, version=observer_version), ReplayVerifier(verifications, version=verifier_version))
