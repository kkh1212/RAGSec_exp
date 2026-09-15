from dataclasses import dataclass
from time import perf_counter
from .budget import Budget
from .fingerprint import generation_key, digest
from .observer import confident
from .patch import apply_patches, canonicalize
from .adapters.execution import ProviderExecutionError
from .types import (DefenseRequest, ExecutionStatus, GenerationResult, Observation,
                    Patch, TraceEvent)


class ExperimentMismatch(RuntimeError):
    pass


class ObserverFailure(RuntimeError):
    pass


class ExecutionHalted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProbeResult:
    generation: GenerationResult
    observation: Observation | None


class ProbeRunner:
    def __init__(self, request: DefenseRequest, generator, observer, config, budget: Budget,
                 trace: list[TraceEvent], cache=None):
        self.request, self.generator, self.observer = request, generator, observer
        self.config, self.budget, self.trace = config, budget, trace
        # Per-repair by default. Optional explicit shared cache still uses complete keys.
        self.cache = {} if cache is None else cache
        self.generator_version = generator.version
        self.observer_version = observer.version
        self.serial = 0

    def record(self, phase, reason, **kwargs):
        self.trace.append(TraceEvent(len(self.trace), phase, reason, **kwargs))

    def observe(self, generation: GenerationResult) -> Observation | None:
        if generation.execution_status != ExecutionStatus.OK:
            return None
        self.budget.observer_calls += 1
        try:
            if self.observer.version != self.observer_version:
                raise ValueError("observer configuration changed")
            observation = self.observer.observe(self.request.query, generation.text)
            if not isinstance(observation, Observation) or observation.observer_version != self.observer_version:
                raise ValueError("observer version or type mismatch")
            return confident(observation, self.config.min_observer_confidence)
        except Exception as exc:
            # Log the exception class only: messages can include credentials or payloads.
            self.record("observer", "observer_error", details={"exception_type": type(exc).__name__})
            raise ObserverFailure("observer failed") from None

    def run(self, patches: tuple[Patch, ...], phase: str, *, fresh=False,
            reserve=0, refinement=False, replicate_id: str | None = None) -> ProbeResult:
        patches = canonicalize(self.request.documents, patches)
        documents = apply_patches(self.request.documents, patches)
        limit = self.config.max_changed_documents if self.config.search_version == "v2" else self.config.max_masked_documents
        if len({p.doc_id for p in patches}) > limit:
            raise ValueError("masked document limit exceeded")
        self.serial += 1
        if fresh and replicate_id is None:
            replicate_id = f"{phase}:{self.serial}"
        for retry in range(self.config.max_execution_retries + 1):
            call_fresh = fresh or retry > 0
            call_id = replicate_id if retry == 0 else f"{phase}:{self.serial}:retry-{retry}"
            key = generation_key(self.request.query, documents, self.request.generation_fingerprint,
                self.generator_version, fresh=call_fresh, replicate_id=call_id)
            if (self.generator.fingerprint != self.request.generation_fingerprint or
                    self.generator.version != self.generator_version):
                self.record(phase, "generation_configuration_changed", patches=patches,
                            execution_status=ExecutionStatus.CONFIG_MISMATCH)
                raise ExperimentMismatch("generator configuration changed")
            cache_hit = not call_fresh and key in self.cache
            if cache_hit:
                self.budget.cache_hits += 1
                generation = self.cache[key]
                measured_latency = 0.0
            else:
                self.budget.consume_generation(reserve, refinement, phase)
                started = perf_counter()
                try:
                    generation = self.generator.generate(self.request.query, documents,
                        fresh=call_fresh, replicate_id=call_id)
                    if not isinstance(generation, GenerationResult):
                        raise TypeError("generator result type mismatch")
                except Exception as exc:
                    status = exc.status if isinstance(exc, ProviderExecutionError) else (
                        ExecutionStatus.TIMEOUT if isinstance(exc, TimeoutError) else ExecutionStatus.OTHER_API_ERROR)
                    generation = GenerationResult("", status, self.request.generation_fingerprint,
                        latency_ms=(perf_counter() - started) * 1000)
                    self.record(phase, "adapter_exception", patches=patches,
                        details={"exception_type": type(exc).__name__})
                measured_latency = (perf_counter() - started) * 1000
                self.budget.add_tokens(generation.usage)
                self.budget.generation_latency_ms += measured_latency
            if (generation.generation_fingerprint != self.request.generation_fingerprint or
                    self.generator.fingerprint != self.request.generation_fingerprint or
                    self.generator.version != self.generator_version):
                self.record(phase, "generation_fingerprint_mismatch", patches=patches,
                    execution_status=ExecutionStatus.CONFIG_MISMATCH, cache_hit=cache_hit,
                    fresh=call_fresh, replicate_id=call_id, request_fingerprint=key)
                raise ExperimentMismatch("generation fingerprint mismatch")
            if not cache_hit and not call_fresh and generation.execution_status == ExecutionStatus.OK:
                self.cache[key] = generation
            try:
                observation = self.observe(generation)
            except ObserverFailure:
                self.record(phase, "generated_but_observer_failed", patches=patches,
                    execution_status=generation.execution_status, cache_hit=cache_hit,
                    fresh=call_fresh, replicate_id=call_id, request_fingerprint=key,
                    details={"response_hash": digest(generation.text), "retry_index": retry})
                raise
            self.record(phase, "cache_hit" if cache_hit else "generation_attempt", patches=patches,
                observation=observation, execution_status=generation.execution_status,
                cache_hit=cache_hit, fresh=call_fresh, replicate_id=call_id, request_fingerprint=key,
                details={"response_hash": digest(generation.text), "retry_index": retry,
                         "latency_ms": measured_latency,
                         "adapter_reported_latency_ms": generation.latency_ms,
                         "input_tokens": 0 if cache_hit else generation.usage.input_tokens,
                         "output_tokens": 0 if cache_hit else generation.usage.output_tokens})
            if generation.execution_status in (ExecutionStatus.PROVIDER_BLOCK, ExecutionStatus.AUTH_ERROR, ExecutionStatus.RATE_LIMIT):
                raise ExecutionHalted(generation.execution_status.value)
            if generation.execution_status not in (ExecutionStatus.API_ERROR, ExecutionStatus.OTHER_API_ERROR, ExecutionStatus.TIMEOUT):
                break
        return ProbeResult(generation, observation)
