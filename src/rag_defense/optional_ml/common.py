"""Pure-Python data validation and group split; optional imports stay in functions."""
from dataclasses import dataclass
import hashlib
import json
import os
import random
from ..fingerprint import digest
from ..workspace import local_path, project_root

FEATURE_VERSION = "separate-query-response-concat-absdiff-product-v1"
LABELS = ("ANSWER", "OTHER", "REFUSAL")


@dataclass(frozen=True, slots=True)
class LabeledResponse:
    query_id: str
    query: str
    response: str
    label: str
    human_reviewed: bool
    data_kind: str

    def __post_init__(self):
        if self.human_reviewed is not True or self.data_kind != "human_reviewed_real":
            raise ValueError("training accepts only explicitly human-reviewed real response labels")
        if self.label not in LABELS:
            raise ValueError("training labels must be ANSWER, REFUSAL, or OTHER")
        if not all(isinstance(v, str) and v.strip() for v in (self.query_id, self.query, self.response)):
            raise ValueError("query_id, query and a nonempty response are required")


def load_labels(path, max_records=2000):
    records = []
    with local_path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            if len(records) >= max_records:
                raise ValueError("label record limit exceeded")
            records.append(LabeledResponse(**json.loads(line)))
    if not records:
        raise ValueError("no real reviewed labels supplied; no model has been trained")
    return records


def split_by_query(records, validation_fraction=0.2, seed=13):
    """Seeded group shuffle. A query_id never appears on both sides."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must lie strictly between 0 and 1")
    query_to_id, id_to_query = {}, {}
    for r in records:
        if r.query in query_to_id and query_to_id[r.query] != r.query_id:
            raise ValueError("identical questions with different query_id values risk split leakage")
        if r.query_id in id_to_query and id_to_query[r.query_id] != r.query:
            raise ValueError("one query_id must identify one question")
        query_to_id[r.query], id_to_query[r.query_id] = r.query_id, r.query
    groups = sorted(id_to_query)
    if len(groups) < 2:
        raise ValueError("at least two query groups are required")
    random.Random(seed).shuffle(groups)
    count = max(1, min(len(groups) - 1, round(len(groups) * validation_fraction)))
    validation_groups = set(groups[:count])
    training = [i for i, r in enumerate(records) if r.query_id not in validation_groups]
    validation = [i for i, r in enumerate(records) if r.query_id in validation_groups]
    return training, validation


def file_hash(path):
    path = local_path(path)
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def model_tree_hash(path):
    path = local_path(path)
    if not path.is_dir():
        raise ValueError("embedding_model_dir must be a model already copied into this project")
    files = sorted(local_path(p) for p in path.rglob("*") if local_path(p).is_file())
    if not files:
        raise ValueError("local embedding model directory is empty")
    return digest([(str(p.relative_to(path)), file_hash(p)) for p in files])


def configure_runtime():
    root = project_root()
    cache, temporary = root / ".cache", root / ".tmp"
    cache.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    settings = {"TMPDIR": temporary, "TMP": temporary, "TEMP": temporary,
        "XDG_CACHE_HOME": cache, "HF_HOME": cache / "huggingface",
        "SENTENCE_TRANSFORMERS_HOME": cache / "huggingface/sentence_transformers",
        "TORCH_HOME": cache / "torch", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false"}
    for key, value in settings.items():
        os.environ[key] = str(value)


def load_embedding(path, *, max_seq_length=256):
    path = local_path(path)
    if not path.is_dir():
        raise ValueError("a project-local embedding model is required; downloads are disabled")
    configure_runtime()
    from sentence_transformers import SentenceTransformer
    import torch
    torch.set_num_threads(1)
    if torch.get_num_interop_threads() != 1:
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            raise ValueError("initialize the optional observer before starting other torch workloads") from None
    model = SentenceTransformer(str(path), device="cpu", local_files_only=True,
        trust_remote_code=False, token=False, cache_folder=str(project_root() / ".cache/huggingface"))
    if max_seq_length > model.max_seq_length:
        raise ValueError("requested token limit exceeds the local encoder's supported limit")
    model.max_seq_length = max_seq_length
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    return model


def encode_pairs(model, pairs, batch_size=16):
    for query, response in pairs:
        reason = input_support(model, query, response, model.max_seq_length)
        if reason:
            raise ValueError(reason)
    import numpy as np
    kwargs = dict(batch_size=batch_size, normalize_embeddings=True,
                  convert_to_numpy=True, show_progress_bar=False)
    queries = model.encode([q for q, _ in pairs], **kwargs)
    responses = model.encode([r for _, r in pairs], **kwargs)
    # Separately encode question and response, so neither disappears through concatenation truncation.
    return np.concatenate([queries, responses, np.abs(queries - responses), queries * responses], axis=1)


def input_support(model, query, response, max_tokens):
    """Count special tokens before encoding; no silent tokenizer truncation."""
    if not isinstance(query, str) or not isinstance(response, str) or not query.strip() or not response.strip():
        return "unsupported_empty_or_nontext_input"
    try:
        for name, text in (("query", query), ("response", response)):
            ids = model.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
            if not isinstance(ids, list) or not all(type(i) is int for i in ids):
                return "unsupported_tokenizer_output"
            if len(ids) > max_tokens:
                return name + "_token_limit_exceeded"
    except Exception:
        return "unsupported_tokenizer_input"
    return ""


def probabilities(features, coefficients, intercept):
    """Softmax multinomial LogisticRegression inference, without pickle loading."""
    import numpy as np
    logits = np.asarray(features) @ np.asarray(coefficients).T + np.asarray(intercept)
    logits -= logits.max(axis=1, keepdims=True)
    values = np.exp(logits)
    return values / values.sum(axis=1, keepdims=True)
