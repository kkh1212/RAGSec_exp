"""One-document probes and bounded restoration; no attack or answer labels."""
from dataclasses import dataclass, replace
from itertools import combinations, zip_longest, islice
from .budget import BudgetExceeded
from .patch import full_document_patches, changed_characters, canonicalize, apply_patches, original_hash
from .fingerprint import generation_key, request_key, digest
from .segment import segment_document, units_to_patches
from .types import Patch, ResponseState, Operation


@dataclass(frozen=True, slots=True)
class Candidate:
    doc_id: str
    document_index: int
    full_patches: tuple[Patch, ...]
    best_patches: tuple[Patch, ...]
    original_fingerprint: str = ""
    generation_fingerprint: str = ""
    observed_state: ResponseState = ResponseState.ANSWER
    phase: str = "document_probe"
    transformation_sensitive: bool | None = None


def is_answer(probe):
    return probe.observation is not None and probe.observation.state == ResponseState.ANSWER


def find_candidates(runner) -> list[Candidate]:
    candidates = []
    for index, doc in enumerate(runner.request.documents):
        patches = full_document_patches(doc)
        if not patches:
            continue
        try:
            trial = runner.run(patches, "document_probe", reserve=runner.config.final_generation_reserve)
        except BudgetExceeded:
            runner.record("document_search", "final_calls_reserved", details={"next_document_index": index})
            break
        if is_answer(trial):
            candidates.append(Candidate(doc.doc_id, index, patches, patches))
            runner.record("candidate", "confident_answer_after_document_mask", patches=patches,
                details={"changed_characters": changed_characters(patches), "document_index": index})
    candidates.sort(key=lambda c: (changed_characters(c.full_patches), c.document_index))
    return candidates


def refine_candidate(runner, candidate: Candidate) -> Candidate:
    doc = runner.request.documents[candidate.document_index]
    units = segment_document(doc, runner.config.max_segment_chars)
    active = tuple(range(len(units)))
    best = candidate.best_patches
    granularity = 2
    while active:
        count = min(granularity, len(active))
        groups = [active[i * len(active) // count:(i + 1) * len(active) // count] for i in range(count)]
        accepted = False
        for group in groups:
            remaining = tuple(i for i in active if i not in group)
            patches = units_to_patches(runner.request.documents, tuple(units[i] for i in remaining))
            try:
                trial = runner.run(patches, "sentence_restore", refinement=True,
                                   reserve=runner.config.final_generation_reserve)
            except BudgetExceeded:
                runner.record("refinement", "refinement_limit_or_final_calls_reserved", patches=best)
                return Candidate(candidate.doc_id, candidate.document_index, candidate.full_patches, best)
            if is_answer(trial):
                active, best, accepted = remaining, patches, True
                runner.record("restoration", "accepted_confident_answer", patches=patches,
                    details={"restored_units": list(group), "changed_characters": changed_characters(patches)})
                # Re-test groups in their new context. No monotonicity inference.
                granularity = max(2, count - 1)
                break
            runner.record("restoration", "rejected_without_confident_answer", patches=patches,
                          details={"restored_units": list(group)})
        if accepted:
            continue
        if count == len(active):
            break
        granularity = min(len(active), count * 2)
    return Candidate(candidate.doc_id, candidate.document_index, candidate.full_patches, best)


def _partial_schedule(doc, max_chars, drop):
    """Breadth-first contiguous ranges; a failed parent never prunes its children."""
    units = segment_document(doc, max_chars)
    hashes = {field: original_hash(getattr(doc, field)) for field in ("title", "text")}
    levels = [tuple(u for u in units if u.field == field) for field in ("title", "text")]
    pending = [group for group in levels if group]
    while pending:
        following = []
        for group in pending:
            a, b = group[0], group[-1]
            for operation in ((Operation.MASK_SPAN, Operation.DROP_SPAN) if drop else (Operation.MASK_SPAN,)):
                yield (Patch(doc.doc_id, a.field, hashes[a.field], a.start_char,
                             b.end_char, "" if operation == Operation.DROP_SPAN else "[REDACTED]", operation),)
            if len(group) > 1:
                mid = len(group) // 2
                following.extend((group[:mid], group[mid:]))
        pending = following


class SearchPlan:
    """Explicit finite scope and cursor, retained even if validation stops the search."""
    def __init__(self, runner):
        self.runner = runner
        docs, config = runner.request.documents, runner.config
        operations = (Operation.MASK_DOCUMENT, Operation.DROP_DOCUMENT) if config.enable_drop else (Operation.MASK_DOCUMENT,)
        document = [full_document_patches(doc, op) for doc in docs for op in operations]
        partial = []
        schedules = [_partial_schedule(d, config.max_segment_chars, config.enable_drop) for d in docs]
        for turn in zip_longest(*schedules):
            partial.extend(p for p in turn if p)
        self.pair_total = len(docs) * (len(docs) - 1) // 2 * len(operations)
        pair_patches = {(i, op): full_document_patches(doc, op) for i, doc in enumerate(docs) for op in operations}
        pair_iterator = (tuple(p for index in indices for p in pair_patches[index, op])
                         for indices in combinations(range(len(docs)), 2) for op in operations)
        # Bound schedule materialization as well as calls. The remaining fixed
        # lexicographic range is explicitly reported instead of allocating O(n²).
        pairs = list(islice(pair_iterator, max(20, config.max_additional_generation_attempts)))
        self.pair_operations = [op.value for op in operations]
        self.items = {"document_probe": document, "partial_probe": partial, "pair_probe": pairs}
        self.tested = {name: set() for name in self.items}
        # An edit rendering exactly the original is not an intervention candidate.
        self.seen = {self.key(()): None}
        self.enabled = {"document_probe": True, "partial_probe": config.enable_partial,
                        "pair_probe": config.enable_pairs and config.max_changed_documents == 2}

    def key(self, patches):
        r = self.runner
        return generation_key(r.request.query, apply_patches(r.request.documents, patches),
                              r.request.generation_fingerprint, r.generator_version)

    def candidates(self, phase):
        if not self.enabled[phase]:
            return
        r = self.runner
        for index, patches in enumerate(self.items[phase]):
            if index in self.tested[phase]:
                continue
            key = self.key(patches)
            if key in self.seen or not patches:
                self.tested[phase].add(index)
                r.record(phase, "duplicate_actual_input_skipped", patches=patches)
                continue
            try:
                trial = r.run(patches, phase, reserve=r.config.final_generation_reserve)
            except BudgetExceeded:
                r.record(phase, "stage_limit_or_final_calls_reserved", details={"next_schedule_index": index})
                break
            self.tested[phase].add(index)
            self.seen[key] = trial
            if is_answer(trial):
                first = next(i for i, d in enumerate(r.request.documents) if d.doc_id == patches[0].doc_id)
                candidate = Candidate(patches[0].doc_id, first, patches, patches, request_key(r.request),
                                      r.request.generation_fingerprint, ResponseState.ANSWER, phase)
                r.record("candidate", "request_specific_refusal_relieving_edit", patches=patches,
                         details={"phase": phase, "changed_characters": changed_characters(patches),
                                  "original_fingerprint": candidate.original_fingerprint,
                                  "generation_fingerprint": candidate.generation_fingerprint})
                yield candidate

    def alternate(self, candidate):
        patches = candidate.best_patches
        docs = self.runner.request.documents
        ids = {p.doc_id for p in patches}
        whole_mask = tuple(p for d in docs if d.doc_id in ids for p in full_document_patches(d, Operation.MASK_DOCUMENT))
        if patches == whole_mask or all(p.operation == Operation.DROP_DOCUMENT for p in patches):
            ids = {p.doc_id for p in patches}
            op = Operation.MASK_DOCUMENT if patches[0].operation == Operation.DROP_DOCUMENT else Operation.DROP_DOCUMENT
            alternate = tuple(p for d in docs if d.doc_id in ids for p in full_document_patches(d, op))
        else:
            alternate = tuple(replace(p, replacement="[REDACTED]" if p.operation == Operation.DROP_SPAN else "",
                operation=Operation.MASK_SPAN if p.operation == Operation.DROP_SPAN else Operation.DROP_SPAN) for p in patches)
        return alternate

    def sensitivity(self, candidate):
        trial = self.seen.get(self.key(self.alternate(candidate)))
        if trial and trial.observation and trial.observation.state != ResponseState.UNCERTAIN:
            return not is_answer(trial)
        return candidate.transformation_sensitive if candidate.best_patches == candidate.full_patches else None

    def unexplored(self):
        result = []
        for phase, items in self.items.items():
            pending = [i for i in range(len(items)) if i not in self.tested[phase]]
            if pending:
                result.append({"phase": phase, "reason": "disabled" if not self.enabled[phase] else "not_tested",
                    "scheduled": len(items), "tested": len(self.tested[phase]), "untested_count": len(pending),
                    "ranges": [{"schedule_index": i, "patches": [
                        {"doc_id": p.doc_id, "field": p.field, "operation": p.operation.value,
                         "start_char": p.start_char, "end_char": p.end_char} for p in items[i]]} for i in pending]})
        if self.pair_total > len(self.items["pair_probe"]):
            result.append({"phase": "pair_probe", "reason": "schedule_materialization_bound",
                "untested_count": self.pair_total - len(self.items["pair_probe"]),
                "schedule_index_range": [len(self.items["pair_probe"]), self.pair_total],
                "range_end_exclusive": True, "order": "lexicographic_document_index_pairs_then_operations",
                "document_ids": [d.doc_id for d in self.runner.request.documents], "operations": self.pair_operations})
        return result


def refine_v2(runner, candidate):
    """Restore originals, retaining the best observed operation; no monotonic pruning."""
    best = candidate.best_patches
    def trial(patches, reason):
        try:
            outcome = runner.run(patches, reason, refinement=True, reserve=runner.config.final_generation_reserve)
        except BudgetExceeded:
            runner.record("refinement", "refinement_limit_or_final_calls_reserved", patches=best)
            return None
        accepted = is_answer(outcome)
        runner.record("restoration", "accepted_confident_answer" if accepted else "rejected_without_confident_answer", patches=patches)
        return accepted

    # Restore entire unnecessary documents before any representation conversion.
    ids = tuple(dict.fromkeys(p.doc_id for p in best))
    if len(ids) > 1:
        for doc_id in ids:
            proposed = tuple(p for p in best if p.doc_id != doc_id)
            accepted = trial(proposed, "document_restore")
            if accepted is None:
                return replace(candidate, best_patches=best)
            if accepted:
                best = proposed
    if any(p.operation == Operation.DROP_DOCUMENT for p in best):
        # Empty fields in a retained slot are not a removed document. Probe before adoption.
        proposed = tuple(q for p in best for q in (
            tuple(replace(q, replacement="", operation=Operation.DROP_SPAN)
                  for q in full_document_patches(next(d for d in runner.request.documents if d.doc_id == p.doc_id)))
            if p.operation == Operation.DROP_DOCUMENT else (p,)))
        accepted = trial(proposed, "drop_representation_probe")
        if accepted is not True:
            return replace(candidate, best_patches=best)
        best = proposed

    units = []
    for p in best:
        doc = next(d for d in runner.request.documents if d.doc_id == p.doc_id)
        for u in segment_document(doc, runner.config.max_segment_chars):
            start, end = max(p.start_char, u.start_char), min(p.end_char, u.end_char)
            if p.field == u.field and start < end:
                units.append(replace(p, start_char=start, end_char=end))
    active = tuple(range(len(units)))
    granularity = 2
    while active:
        count = min(granularity, len(active))
        accepted_any = False
        for i in range(count):
            group = active[i * len(active) // count:(i + 1) * len(active) // count]
            remaining = tuple(j for j in active if j not in group)
            proposed = canonicalize(runner.request.documents, tuple(units[j] for j in remaining))
            accepted = trial(proposed, "sentence_restore")
            if accepted is None:
                return replace(candidate, best_patches=best)
            if accepted:
                active, best, accepted_any = remaining, proposed, True
                granularity = max(2, count - 1)
                break
        if accepted_any:
            continue
        if count == len(active):
            break
        granularity = min(len(active), count * 2)
    return replace(candidate, best_patches=best)
