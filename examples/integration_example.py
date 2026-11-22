"""Accept callables supplied later by the owner; never import an external RAG project."""
from rag_defense import DefenseConfig, DefenseEngine, DefenseRequest, ExecutionMode
from rag_defense.adapters.callable import CallableGenerator, CallableObserver, CallableVerifier


def repair_with_callables(request: DefenseRequest, *, generate, observe, verify,
                          generator_version: str, observer_version: str, verifier_version: str,
                          mode: ExecutionMode = ExecutionMode.EXTERNAL,
                          config: DefenseConfig | None = None):
    """generate(query, documents, *, fresh, replicate_id) returns GenerationResult.

    observe(query, response) returns Observation.
    verify(query, response, remaining_spans) returns VerificationResult.
    The caller fixes the model/prompt/settings/renderer and disables SDK retries.
    A future external run requires config.allow_live=True; the default blocks it.
    """
    engine = DefenseEngine(
        generator=CallableGenerator(generate, fingerprint=request.generation_fingerprint,
                                    version=generator_version, mode=mode),
        observer=CallableObserver(observe, version=observer_version, mode=mode),
        verifier=CallableVerifier(verify, version=verifier_version, mode=mode),
        config=config or DefenseConfig())
    return engine.repair(request)


def main():
    from rag_defense.adapters.mock import build_scenario
    s = build_scenario("success")
    result = repair_with_callables(s.request, generate=s.generator.generate,
        observe=s.observer.observe, verify=s.verifier.verify,
        generator_version=s.generator.version, observer_version=s.observer.version,
        verifier_version=s.verifier.version, mode=ExecutionMode.MOCK)
    print("Mock callable integration:", result.status.value)
    print(result.final_response.text)


if __name__ == "__main__":
    main()
