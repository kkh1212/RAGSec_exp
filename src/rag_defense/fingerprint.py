from dataclasses import asdict, is_dataclass
from enum import Enum
import hashlib
import json
from .types import Document, DefenseRequest, RemainingSpan
from .version import CONTRACT_VERSION


def to_jsonable(value):
    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(v) for v in value]
    return value


def digest(value) -> str:
    encoded = json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_generation_fingerprint(*, model: str, endpoint: str, prompt: str,
                                settings: dict, renderer_version: str) -> str:
    """Pass all effective settings, including defaults/seed; never pass credentials."""
    return digest(dict(model=model, endpoint=endpoint, prompt=prompt, settings=settings,
                       renderer_version=renderer_version, contract=CONTRACT_VERSION))


def generation_input(query: str, documents: tuple[Document, ...], fingerprint: str,
                     generator_version: str, *, fresh: bool, replicate_id: str | None):
    return dict(query=query, documents=to_jsonable(documents), generation_fingerprint=fingerprint,
                generator_version=generator_version, contract_version=CONTRACT_VERSION,
                fresh=fresh, replicate_id=replicate_id)


def generation_key(query: str, documents: tuple[Document, ...], fingerprint: str,
                   generator_version: str, *, fresh: bool = False, replicate_id: str | None = None):
    return digest(generation_input(query, documents, fingerprint, generator_version,
                                   fresh=fresh, replicate_id=replicate_id))


def request_key(request: DefenseRequest):
    return digest(dict(request=to_jsonable(request), contract_version=CONTRACT_VERSION))


def observation_key(query: str, response: str, version: str):
    return digest(dict(query=query, response=response, observer_version=version, contract=CONTRACT_VERSION))


def verification_key(query: str, response: str, evidence: tuple[RemainingSpan, ...], version: str):
    return digest(dict(query=query, response=response, evidence=evidence,
                       verifier_version=version, contract=CONTRACT_VERSION))
