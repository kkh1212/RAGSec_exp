"""Explicitly export only the built-in synthetic scenario; no external log access."""
import json
import argparse
from rag_defense.adapters.mock import build_scenario
from rag_defense.adapters.callable import CallableGenerator, CallableObserver, CallableVerifier
from rag_defense.engine import DefenseEngine
from rag_defense.config import DefenseConfig
from rag_defense.fingerprint import (to_jsonable, request_key, generation_key, generation_input,
                                    observation_key, verification_key)
from rag_defense.version import CONTRACT_VERSION
from rag_defense.workspace import local_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export the explicit synthetic v1 fixture")
    parser.add_argument("--output", default="tests/fixtures/synthetic_success.jsonl")
    args = parser.parse_args(argv)
    path = local_path(args.output)
    s = build_scenario()
    rows = [dict(kind="request", schema_version=CONTRACT_VERSION, request=s.request,
        request_fingerprint=request_key(s.request), generator_version=s.generator.version,
        observer_version=s.observer.version, verifier_version=s.verifier.version)]
    seen_observations, seen_generations, seen_verifications = set(), set(), set()

    def generate(query, documents, *, fresh, replicate_id):
        result = s.generator.generate(query, documents, fresh=fresh, replicate_id=replicate_id)
        key = generation_key(query, documents, s.generator.fingerprint, s.generator.version,
                             fresh=fresh, replicate_id=replicate_id)
        if key not in seen_generations:
            rows.append(dict(kind="generation", input=generation_input(query, documents, s.generator.fingerprint,
                s.generator.version, fresh=fresh, replicate_id=replicate_id), request_fingerprint=key, result=result))
            seen_generations.add(key)
        return result

    def observe(query, response):
        result = s.observer.observe(query, response)
        key = observation_key(query, response, s.observer.version)
        if key not in seen_observations:
            rows.append(dict(kind="observation", query=query, response=response, result=result, key=key))
            seen_observations.add(key)
        return result

    def verify(query, response, evidence):
        result = s.verifier.verify(query, response, evidence)
        key = verification_key(query, response, evidence, s.verifier.version)
        if key not in seen_verifications:
            rows.append(dict(kind="verification", query=query, response=response, evidence=evidence, result=result, key=key))
            seen_verifications.add(key)
        return result

    result = DefenseEngine(
        generator=CallableGenerator(generate, fingerprint=s.generator.fingerprint, version=s.generator.version, mode="mock"),
        observer=CallableObserver(observe, version=s.observer.version, mode="mock"),
        verifier=CallableVerifier(verify, version=s.verifier.version, mode="mock"),
        config=DefenseConfig(search_version="v1", max_additional_generation_attempts=20)).repair(s.request)
    assert result.status.value == "PATCH_APPLIED"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(to_jsonable(row), ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Synthetic recording: {path} ({len(rows)} records)")


if __name__ == "__main__":
    main()
