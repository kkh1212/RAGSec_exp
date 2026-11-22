"""Metadata-only disk reports: no query, document text, response text, or quotes."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from .fingerprint import to_jsonable, digest
from .workspace import local_path


def report_dict(result):
    verification = None
    if result.verification:
        v = result.verification
        verification = dict(status=v.status.value, addresses_query=v.addresses_query, supported=v.supported,
            verifier_version=v.verifier_version, reason_hash=digest(v.reason),
            input_tokens=v.input_tokens, output_tokens=v.output_tokens,
            citations=[dict(doc_id=c.doc_id, field=c.field, start_char=c.start_char, end_char=c.end_char,
                            quote_hash=digest(c.quote)) for c in v.citations])
    return to_jsonable(dict(status=result.status, mode=result.mode, versions=result.versions, config=result.config,
        original_request_fingerprint=result.original_request_fingerprint,
        final_response=dict(text_hash=digest(result.final_response.text),
            execution_status=result.final_response.execution_status,
            generation_fingerprint=result.final_response.generation_fingerprint),
        intervention_applied=result.intervention_applied, patches=result.patches,
        candidate_documents=result.candidate_documents, candidate_patches=result.candidate_patches,
        best_candidate_patches=result.best_candidate_patches, verification_status=result.verification_status,
        verification=verification, trace=result.trace, usage=result.usage, limitations=result.limitations,
        transformation_sensitive=result.transformation_sensitive, search_truncated=result.search_truncated,
        changed_characters=result.changed_characters, grounding_verified=result.verification_status.value == "PASS",
        grounding_passed=result.grounding_passed, search_complete=result.search_complete,
        unexplored=result.unexplored, termination_reason=result.termination_reason,
        candidate_details=result.candidate_details, gold_correctness=None))


def save_run(result, output_dir="runs"):
    directory = local_path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-{uuid4().hex[:12]}.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(report_dict(result), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path
