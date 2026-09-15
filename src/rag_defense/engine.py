"""Request-scoped refusal repair. No retrieval, label access, or live SDK imports."""
from dataclasses import asdict
from time import perf_counter
from .budget import Budget, BudgetExceeded
from .config import DefenseConfig
from .fingerprint import digest, request_key, verification_key
from .observer import UnconfiguredObserver
from .patch import changed_characters, deletion_patches, remaining_spans
from .probe import ProbeRunner, ExperimentMismatch, ObserverFailure, ExecutionHalted
from .search import find_candidates, refine_candidate, is_answer, SearchPlan, refine_v2
from .segment import SEGMENT_VERSION
from .types import (DefenseRequest, DefenseResult, DefenseStatus, ExecutionMode,
                    ExecutionStatus, QueryPolicy, ResponseState, VerificationResult,
                    VerificationStatus)
from .verify import UnconfiguredVerifier, validate_citations
from .version import __version__, CONTRACT_VERSION

LIMITATIONS = (
    "A candidate is a request-specific refusal-relieving edit, not a permanent malicious-document label.",
    "Bounded document/span and at-most-two-document search is not a global minimum or exhaustive attack search.",
    "Masking and deletion alter length and positions; the replacement is not assumed neutral.",
    "Fresh controls do not establish content-only causality or remove sampling uncertainty.",
    "Grounding and exact citation checks do not establish gold-answer correctness.",
    "Mock and replay outcomes are not real-model attack mitigation measurements.",
)


class DefenseEngine:
    def __init__(self, *, generator, observer=None, verifier=None,
                 config: DefenseConfig | None = None, cache=None):
        self.generator = generator
        self.observer = observer if observer is not None else UnconfiguredObserver()
        self.verifier = verifier if verifier is not None else UnconfiguredVerifier()
        self.config = config if config is not None else DefenseConfig()
        self.cache = cache

    def repair(self, request: DefenseRequest) -> DefenseResult:
        if type(request) is not DefenseRequest:
            raise TypeError("repair accepts only the label-free DefenseRequest contract")
        return _Session(self, request).execute()


class _Session:
    def __init__(self, engine, request):
        self.engine, self.request, self.config = engine, request, engine.config
        self.budget, self.trace = Budget(self.config), []
        self.runner = ProbeRunner(request, engine.generator, engine.observer, self.config,
                                  self.budget, self.trace, engine.cache)
        self.candidates = []
        self.plan = None
        self.termination_reason = ""
        self.refined_count = 0
        self.final_inputs = set()
        self.failed_verification_inputs = set()
        self.verifier = engine.verifier.for_request() if hasattr(engine.verifier, "for_request") else engine.verifier
        self.verification = None
        self.transformation_sensitive = None
        self.verifier_version = self.verifier.version
        modes = [ExecutionMode(a.mode) for a in (engine.generator, engine.observer, engine.verifier)]
        self.mode = ExecutionMode.EXTERNAL if ExecutionMode.EXTERNAL in modes else (
            ExecutionMode.REPLAY if ExecutionMode.REPLAY in modes else ExecutionMode.MOCK)

    def finish(self, status, response=None, patches=()):
        success = status in (DefenseStatus.PATCH_APPLIED, DefenseStatus.RETRY_RECOVERY)
        if not success:
            response, patches = self.request.initial_response, ()
        self.runner.record("finish", status.value, patches=patches, details={
            "generation_attempts": self.budget.generation_attempts,
            "grounding_verifications": self.budget.grounding_verifications,
            "changed_characters": changed_characters(patches), "search_truncated": self.budget.truncated})
        best = min(self.candidates, key=lambda c: (changed_characters(c.best_patches), c.document_index), default=None)
        unexplored = self.plan.unexplored() if self.plan else []
        if self.budget.truncated:
            unexplored.append({"phase": "refinement_or_validation", "reason": "bounded_budget",
                               "remaining_generation_attempts": self.budget.remaining})
        return DefenseResult(status=status, final_response=response, intervention_applied=bool(patches),
            patches=patches, candidate_documents=tuple(dict.fromkeys(p.doc_id for c in self.candidates for p in c.full_patches)),
            verification_status=self.verification.status if self.verification else VerificationStatus.NOT_RUN,
            trace=tuple(self.trace), usage=self.budget.summary(), limitations=LIMITATIONS,
            candidate_patches=tuple(c.best_patches for c in self.candidates),
            best_candidate_patches=best.best_patches if best else (), verification=self.verification,
            transformation_sensitive=self.transformation_sensitive, search_truncated=self.budget.truncated,
            changed_characters=changed_characters(patches), original_request_fingerprint=request_key(self.request),
            mode=self.mode, config=asdict(self.config), versions={"package": __version__,
                "contract": CONTRACT_VERSION, "segmentation": SEGMENT_VERSION,
                "generator": self.runner.generator_version, "observer": self.runner.observer_version,
                "verifier": self.verifier_version}, grounding_passed=success and self.verification is not None
                and self.verification.status == VerificationStatus.PASS,
            search_complete=self.plan is not None and not unexplored, unexplored=tuple(unexplored),
            termination_reason=self.termination_reason or status.value,
            candidate_details=tuple({"original_fingerprint": c.original_fingerprint or request_key(self.request),
                "generation_fingerprint": c.generation_fingerprint or self.request.generation_fingerprint,
                "documents": list(dict.fromkeys(p.doc_id for p in c.full_patches)),
                "operations": [p.operation.value for p in c.full_patches], "phase": c.phase,
                "observed_state": c.observed_state.value, "changed_characters": changed_characters(c.best_patches),
                "transformation_sensitive": c.transformation_sensitive} for c in self.candidates))

    def verify(self, response, patches):
        verifier = self.verifier
        if not verifier.configured:
            self.verification = VerificationResult(VerificationStatus.NOT_CONFIGURED,
                                                   verifier_version=self.verifier_version)
        else:
            evidence = remaining_spans(self.request.documents, patches)
            signature = verification_key(self.request.query, response.text, evidence, self.verifier_version)
            if self.config.search_version == "v2" and signature in self.failed_verification_inputs:
                self.runner.record("grounding", "identical_rejected_verifier_input_skipped", patches=patches)
                return False
            self.budget.consume_verification()
            started = perf_counter()
            try:
                if verifier.version != self.verifier_version:
                    raise ValueError("verifier configuration changed")
                result = verifier.verify(self.request.query, response.text, evidence)
                if not isinstance(result, VerificationResult) or result.verifier_version != self.verifier_version:
                    raise ValueError("verifier version or result type mismatch")
                self.verification = validate_citations(result, evidence)
                if self.verification.status in (VerificationStatus.FAIL, VerificationStatus.UNCERTAIN):
                    self.failed_verification_inputs.add(signature)
            except Exception as exc:
                self.verification = VerificationResult(VerificationStatus.ERROR,
                    verifier_version=self.verifier_version, reason="verifier_exception")
                self.runner.record("grounding", "adapter_exception", details={"exception_type": type(exc).__name__})
            self.budget.grounding_latency_ms += (perf_counter() - started) * 1000
            for field in ("input_tokens", "output_tokens"):
                measured = getattr(self.verification, field)
                if measured is None and verifier.mode == ExecutionMode.MOCK:
                    measured = 0
                current = getattr(self.budget, "grounding_" + field)
                setattr(self.budget, "grounding_" + field, current + measured if current is not None and measured is not None else None)
            self.runner.record("grounding", "verification_completed", patches=patches, details={
                "verification_fingerprint": verification_key(self.request.query, response.text, evidence, self.verifier_version),
                "response_hash": digest(response.text), "status": self.verification.status.value,
                "addresses_query": self.verification.addresses_query, "supported": self.verification.supported,
                "verifier_version": self.verification.verifier_version,
                "execution_status": self.verification.reason if self.verification.reason in {s.value for s in ExecutionStatus} else None,
                "reason_hash": digest(self.verification.reason),
                "citations": [{"doc_id": c.doc_id, "field": c.field, "start_char": c.start_char,
                    "end_char": c.end_char, "quote_hash": digest(c.quote)} for c in self.verification.citations]})
        return self.verification.status == VerificationStatus.PASS

    def execute(self):
        r = self.request
        self.runner.record("start", "request_received", request_fingerprint=request_key(r),
                           details={"mode": self.mode.value, "query_policy": r.query_policy.value})
        if r.initial_response.execution_status != ExecutionStatus.OK:
            self.runner.record("initial", "initial_execution_error", execution_status=r.initial_response.execution_status)
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        if r.query_policy != QueryPolicy.ALLOW:
            return self.finish(DefenseStatus.OUT_OF_SCOPE)
        if not self.engine.observer.configured:
            return self.finish(DefenseStatus.OBSERVER_NOT_CONFIGURED)
        readiness = getattr(self.engine.observer, "readiness", None)
        if readiness in ("TRAINED_PROVISIONAL", "NEEDS_LABELS", "INCOMPATIBLE_ARTIFACT"):
            self.runner.record("guard", "observer_not_validated", details={"readiness": readiness})
            return self.finish(DefenseStatus.OBSERVER_NOT_CONFIGURED)
        if self.mode == ExecutionMode.EXTERNAL and not self.config.allow_live:
            self.runner.record("guard", "live_adapters_disabled")
            return self.finish(DefenseStatus.OUT_OF_SCOPE)
        if (r.initial_response.generation_fingerprint != r.generation_fingerprint or
                self.engine.generator.fingerprint != r.generation_fingerprint):
            self.runner.record("initial", "generation_fingerprint_mismatch", execution_status=ExecutionStatus.CONFIG_MISMATCH)
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        try:
            return self.search_and_validate()
        except BudgetExceeded:
            return self.finish(DefenseStatus.BUDGET_EXHAUSTED)
        except (ExperimentMismatch, ObserverFailure):
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        except ExecutionHalted as exc:
            self.termination_reason = str(exc)
            return self.finish(DefenseStatus.EXECUTION_ERROR)

    def search_and_validate(self):
        if self.config.search_version == "v2":
            return self.search_v2()
        return self.search_v1()

    def search_v1(self):
        initial = self.runner.observe(self.request.initial_response)
        self.runner.record("initial", "initial_observation", observation=initial, execution_status=ExecutionStatus.OK)
        if initial.state == ResponseState.ANSWER:
            return self.finish(DefenseStatus.NO_REPAIR_NEEDED, self.request.initial_response)
        if initial.state == ResponseState.OTHER:
            return self.finish(DefenseStatus.OUT_OF_SCOPE)

        # The one original retry doubles as limited confirmation for UNCERTAIN.
        retry = self.runner.run((), "original_retry", fresh=True)
        if retry.observation is None:
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        if is_answer(retry):
            if self.verify(retry.generation, ()):
                return self.finish(DefenseStatus.RETRY_RECOVERY, retry.generation)
            return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)
        if retry.observation.state != ResponseState.REFUSAL:
            return self.finish(DefenseStatus.UNSTABLE_BASELINE)

        self.candidates = find_candidates(self.runner)
        if not self.candidates:
            return self.finish(DefenseStatus.BUDGET_EXHAUSTED if self.budget.truncated else DefenseStatus.NO_REPAIR_FOUND)
        for index, candidate in enumerate(self.candidates[:self.config.max_candidates_to_refine]):
            self.runner.record("selection", "fewest_original_characters_then_retrieval_order",
                               patches=candidate.full_patches, details={"candidate_index": index})
            self.candidates[index] = refine_candidate(self.runner, candidate)
            if not self.candidates[index].best_patches:
                self.runner.record("control", "original_answer_during_restoration")
                return self.finish(DefenseStatus.UNSTABLE_BASELINE)

        if not self.engine.verifier.configured:
            self.verify(self.request.initial_response, ())
            return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)

        # The smallest search candidate and the verified candidate stay separate.
        options = []
        for candidate in self.candidates:
            options.append((candidate.document_index, candidate.best_patches))
            if candidate.full_patches != candidate.best_patches:
                options.append((candidate.document_index, candidate.full_patches))
        # Smallest first; full-document fallbacks remain available within the shared budget.
        options.sort(key=lambda item: (changed_characters(item[1]), item[0]))
        for doc_index, patches in options:
            required = self.config.final_generation_reserve
            if self.budget.remaining < required:
                self.budget.truncated = True
                raise BudgetExceeded("cannot complete fresh final controls")
            if self.budget.grounding_verifications >= self.config.max_grounding_verifications:
                self.budget.truncated = True
                raise BudgetExceeded("no grounding verification remains")
            self.transformation_sensitive = None
            self.runner.record("final_selection", "validate_candidate", patches=patches,
                               details={"document_index": doc_index})
            baseline = self.runner.run((), "final_original", fresh=True, reserve=required - 1)
            if baseline.observation is None:
                return self.finish(DefenseStatus.EXECUTION_ERROR)
            if baseline.observation.state != ResponseState.REFUSAL:
                self.runner.record("control", "original_refusal_not_reproduced", observation=baseline.observation)
                return self.finish(DefenseStatus.UNSTABLE_BASELINE)
            patched = self.runner.run(patches, "final_patch", fresh=True, reserve=int(self.config.deletion_control))
            if not is_answer(patched):
                self.runner.record("final_rejection", "patched_answer_not_reproduced", patches=patches)
                continue
            if self.config.deletion_control:
                deleted = self.runner.run(deletion_patches(patches), "deletion_control", fresh=True)
                if deleted.observation is not None and deleted.observation.state != ResponseState.UNCERTAIN:
                    self.transformation_sensitive = not is_answer(deleted)
                self.runner.record("control", "transformation_comparison", patches=patches, details={
                    "transformation_sensitive": self.transformation_sensitive,
                    "both_answer": is_answer(deleted),
                    "deletion_execution_status": deleted.generation.execution_status.value})
                # A configured control must execute successfully. Semantic disagreement is
                # recorded by default; optional strict mode also requires deletion ANSWER.
                if deleted.observation is None or (self.config.require_deletion_consistency and not is_answer(deleted)):
                    self.runner.record("final_rejection", "deletion_control_incomplete_or_strict_mismatch", patches=patches)
                    continue
            if self.verify(patched.generation, patches):
                return self.finish(DefenseStatus.PATCH_APPLIED, patched.generation, patches)
        return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)

    def search_v2(self):
        initial = self.runner.observe(self.request.initial_response)
        self.runner.record("initial", "initial_observation", observation=initial, execution_status=ExecutionStatus.OK)
        if initial.state == ResponseState.ANSWER:
            return self.finish(DefenseStatus.NO_REPAIR_NEEDED, self.request.initial_response)
        if initial.state == ResponseState.OTHER:
            return self.finish(DefenseStatus.OUT_OF_SCOPE)
        self.plan = SearchPlan(self.runner)
        retry = self.runner.run((), "original_retry", fresh=True)
        if retry.observation is None:
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        if is_answer(retry):
            if self.verify(retry.generation, ()):
                return self.finish(DefenseStatus.RETRY_RECOVERY, retry.generation)
            # An ungrounded retry does not establish a stable refusal baseline.
            return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)
        if retry.observation.state != ResponseState.REFUSAL:
            return self.finish(DefenseStatus.UNSTABLE_BASELINE)
        documents = list(self.plan.candidates("document_probe"))
        documents.sort(key=lambda c: (changed_characters(c.best_patches), c.document_index))
        from dataclasses import replace
        documents = [replace(c, transformation_sensitive=self.plan.sensitivity(c)) for c in documents]
        self.candidates.extend(documents)
        # One document-stage validation first. On failure reserve the next opportunity
        # for independent spans/pairs rather than spending it on the same full edit.
        deferred = documents[1:]
        if documents:
            result = self.try_v2_candidate(documents[0])
            if result is not None:
                return result
        for phase in ("partial_probe", "pair_probe"):
            self.runner.record("search_resume", "continue_after_no_verified_candidate", details={"phase": phase})
            for candidate in self.plan.candidates(phase):
                candidate = replace(candidate, transformation_sensitive=self.plan.sensitivity(candidate))
                self.candidates.append(candidate)
                result = self.try_v2_candidate(candidate)
                if result is not None:
                    return result
        for candidate in deferred:
            result = self.try_v2_candidate(candidate)
            if result is not None:
                return result
        # A larger known ANSWER is a distinct fallback, still requiring fresh validation.
        for candidate in tuple(self.candidates):
            if candidate.full_patches != candidate.best_patches:
                result = self.try_v2_candidate(replace(candidate, best_patches=candidate.full_patches), refine=False)
                if result is not None:
                    return result
        self.termination_reason = "no_verified_candidate_within_scope" if self.candidates else "no_answer_candidate_within_scope"
        return self.finish(DefenseStatus.NO_VERIFIED_REPAIR if self.candidates else (
            DefenseStatus.BUDGET_EXHAUSTED if self.budget.truncated else DefenseStatus.NO_REPAIR_FOUND))

    def try_v2_candidate(self, candidate, *, refine=True):
        if not self.verifier.configured:
            self.verify(self.request.initial_response, ())
            self.termination_reason = "verifier_not_configured"
            return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)
        if self.budget.grounding_verifications >= self.config.max_grounding_verifications:
            self.termination_reason = "grounding_verification_budget_exhausted"
            self.budget.truncated = True
            return self.finish(DefenseStatus.BUDGET_EXHAUSTED)
        if refine and self.refined_count < self.config.max_candidates_to_refine:
            self.refined_count += 1
            refined = refine_v2(self.runner, candidate)
            self.candidates[self.candidates.index(candidate)] = refined
            candidate = refined
        patches = candidate.best_patches
        if not patches:
            return self.finish(DefenseStatus.UNSTABLE_BASELINE)
        key = self.plan.key(patches)
        if key in self.final_inputs:
            return None
        known = self.plan.seen.get(key)
        if known and verification_key(self.request.query, known.generation.text,
                remaining_spans(self.request.documents, patches), self.verifier_version) in self.failed_verification_inputs:
            self.runner.record("final_selection", "identical_rejected_verifier_input_skipped", patches=patches)
            return None
        self.final_inputs.add(key)
        alternate = self.plan.alternate(candidate)
        alternate_key = self.plan.key(alternate)
        if self.config.enable_drop and alternate_key not in self.plan.seen:
            # Auxiliary sensitivity observation never becomes an approval requirement.
            try:
                outcome = self.runner.run(alternate, "transformation_probe", reserve=self.config.final_generation_reserve)
                self.plan.seen[alternate_key] = outcome
            except BudgetExceeded:
                self.runner.record("control", "transformation_comparison_not_tested")
        if self.budget.remaining < 2:
            self.termination_reason = "fresh_generation_budget_exhausted"
            self.budget.truncated = True
            return self.finish(DefenseStatus.BUDGET_EXHAUSTED)
        self.runner.record("final_selection", "validate_candidate", patches=patches)
        baseline = self.runner.run((), "final_original", fresh=True, reserve=1)
        if baseline.observation is None:
            return self.finish(DefenseStatus.EXECUTION_ERROR)
        if baseline.observation.state != ResponseState.REFUSAL:
            return self.finish(DefenseStatus.UNSTABLE_BASELINE)
        patched = self.runner.run(patches, "final_patch", fresh=True)
        if not is_answer(patched):
            self.runner.record("final_rejection", "patched_answer_not_reproduced", patches=patches)
            return None
        self.transformation_sensitive = self.plan.sensitivity(candidate)
        self.runner.record("control", "transformation_comparison", patches=patches,
                           details={"transformation_sensitive": self.transformation_sensitive})
        if self.verify(patched.generation, patches):
            self.termination_reason = "fresh_grounded_candidate_returned"
            return self.finish(DefenseStatus.PATCH_APPLIED, patched.generation, patches)
        self.runner.record("final_rejection", "grounding_not_passed_resume_search", patches=patches)
        if getattr(self.verifier, "halted", False):
            self.termination_reason = "verifier_operational_error_no_retry"
            return self.finish(DefenseStatus.NO_VERIFIED_REPAIR)
        if self.budget.grounding_verifications >= self.config.max_grounding_verifications:
            self.termination_reason = "grounding_verification_budget_exhausted"
            self.budget.truncated = True
            return self.finish(DefenseStatus.BUDGET_EXHAUSTED)
        return None
