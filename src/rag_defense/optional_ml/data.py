"""Explicit copied files only. Metadata never enters observer features or review UI."""
from collections import Counter
import csv
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
import random
import re
import unicodedata

from ..fingerprint import digest
from ..types import ExecutionStatus, Readiness, ResponseState
from ..workspace import local_path

FIELDS = ("query", "response", "sample_id", "query_id", "execution_status", "label", "label_status", "split", "data_kind", "purpose")
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class ObserverRecord:
    sample_id: str
    query_id: str
    query: str
    response: str
    execution_status: str = "OK"
    label: str = ""
    label_status: str = "unreviewed"
    split: str = ""
    data_kind: str = "real"
    purpose: str = "observer_development"

    def __post_init__(self):
        if not all(isinstance(getattr(self, k), str) for k in FIELDS):
            raise ValueError("record fields must be strings")
        if not self.sample_id or not self.query_id or not self.query.strip():
            raise ValueError("sample_id, query_id, query are required")
        ExecutionStatus(self.execution_status)
        if self.label and self.label not in {s.value for s in ResponseState}:
            raise ValueError("invalid response-state label")
        if self.label_status not in ("unreviewed", "suggested", "human_verified"):
            raise ValueError("invalid label_status")
        if self.label_status == "human_verified" and not self.label:
            raise ValueError("human_verified requires a label")
        if self.split not in ("", *SPLITS) or self.data_kind not in ("real", "synthetic", "replay"):
            raise ValueError("invalid split or data kind")
        if self.purpose not in ("observer_development", "final_defense_evaluation"):
            raise ValueError("invalid data purpose")


def read_rows(path, max_records=2000):
    path = local_path(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream) if path.suffix.lower() == ".csv" else (json.loads(line) for line in stream if line.strip())
        result = []
        for row in rows:
            if not isinstance(row, dict) or len(result) >= max_records:
                raise ValueError("expected objects within the configured record limit")
            result.append(row)
        return result


def load_records(path, max_records=2000):
    result = [ObserverRecord(**row) for row in read_rows(path, max_records)]
    if len({r.sample_id for r in result}) != len(result):
        raise ValueError("duplicate sample_id")
    return result


def write_records(records, path):
    path = local_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        if path.suffix.lower() == ".csv":
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(asdict(r) for r in records)
        else:
            for record in records:
                stream.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def import_data(input_path, output_path, *, max_records=2000):
    source, output = local_path(input_path), local_path(output_path)
    metadata_path = local_path(str(output) + ".metadata.jsonl")
    if output.exists() or metadata_path.exists() or source == output:
        raise ValueError("choose unused output paths")
    records, metadata = [], []
    for row in read_rows(source, max_records):
        # Import never infers labels from clean/attack flags and never certifies review.
        required = {key: row[key] for key in ("sample_id", "query_id", "query", "response", "execution_status")}
        label = row.get("label", "")
        record = ObserverRecord(**required, label=label, label_status="suggested" if label else "unreviewed",
            split=row.get("split", ""), data_kind=row.get("data_kind", "real"),
            purpose=row.get("purpose", "observer_development"))
        records.append(record)
        metadata.append({"sample_id": record.sample_id, "metadata": {k: v for k, v in row.items() if k not in FIELDS}})
    if len({r.sample_id for r in records}) != len(records):
        raise ValueError("duplicate sample_id")
    write_records(records, output)
    metadata_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in metadata), encoding="utf-8")
    return {"readiness": Readiness.NEEDS_LABELS.value, "records": len(records), "review_file": str(output),
            "metadata_file": str(metadata_path), "labels_inferred": False}


def review_labels(input_path, output_path, *, reader=input, writer=print):
    from dataclasses import replace
    records = load_records(input_path)
    for index, record in enumerate(records):
        if record.label_status == "human_verified":
            continue
        writer(f"[{record.sample_id}]\nquery: {record.query}\nresponse: {record.response}\nexecution_status: {record.execution_status}")
        while True:
            choice = reader("ANSWER / REFUSAL / OTHER / UNCERTAIN / skip / quit: ").strip()
            if choice.lower() in ("skip", "quit") or choice.upper() in {s.value for s in ResponseState}:
                break
            writer("Enter one response-state label, skip, or quit.")
        if choice.lower() == "quit":
            break
        if choice.lower() != "skip":
            records[index] = replace(record, label=choice.upper(), label_status="human_verified")
    write_records(records, output_path)
    return validate_records(records)


def normalized(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def leakage_groups(records):
    """Connected components bind query_id, normalized duplicates and near duplicates.

    Near-question comparison retains numbers: different numbered questions are not
    automatically merged. Similar long query/response records are also audited.
    """
    parent = list(range(len(records)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    def join(a, b):
        parent[root(b)] = root(a)
    warnings = []
    normalized_pairs = [(normalized(r.query), normalized(r.response)) for r in records]
    id_queries = {}
    for i, row in enumerate(records):
        q, response = normalized_pairs[i]
        if row.query_id in id_queries and id_queries[row.query_id] != q:
            raise ValueError("one query_id must identify one normalized question")
        id_queries[row.query_id] = q
        for j in range(i):
            other, other_response = normalized_pairs[j]
            same = row.query_id == records[j].query_id or q == other
            near = (min(len(q), len(other)) >= 24 and re.findall(r"\d+", q) == re.findall(r"\d+", other)
                    and SequenceMatcher(None, q, other, autojunk=False).ratio() >= 0.95)
            near_record = (min(len(q + response), len(other + other_response)) >= 100
                and SequenceMatcher(None, q, other, autojunk=False).ratio() >= 0.9
                and SequenceMatcher(None, response, other_response, autojunk=False).ratio() >= 0.98)
            if same or near or near_record:
                join(j, i)
                if row.query_id != records[j].query_id:
                    warnings.append({"sample_ids": [records[j].sample_id, row.sample_id], "reason": "duplicate_or_near_duplicate_grouped"})
    groups = {}
    for i in range(len(records)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values()), warnings


def split_records(records, *, seed=13, validation_fraction=0.2, test_fraction=0.2):
    if not 0 < validation_fraction < 1 or not 0 < test_fraction < 1 or validation_fraction + test_fraction >= 1:
        raise ValueError("train/validation/test fractions must all be positive")
    groups, warnings = leakage_groups(records)
    for group in groups:
        splits = {records[i].split for i in group if records[i].split}
        purposes = {records[i].purpose for i in group}
        if len(splits) > 1 or len(purposes) > 1:
            raise ValueError("query/near-duplicate leakage across split or data purpose")
    if len(groups) < 3:
        raise ValueError("at least three independent query groups are required")
    explicit = [bool(r.split) for r in records]
    if any(explicit) and not all(explicit):
        raise ValueError("supply all splits or none; partial assignments are ambiguous")
    if all(explicit):
        output = {name: [i for i, r in enumerate(records) if r.split == name] for name in SPLITS}
    else:
        groups.sort(key=lambda group: min(records[i].sample_id for i in group))
        random.Random(seed).shuffle(groups)
        validation_count = max(1, round(len(groups) * validation_fraction))
        test_count = max(1, round(len(groups) * test_fraction))
        if validation_count + test_count >= len(groups):
            raise ValueError("too few groups for requested split fractions")
        assignments = {"validation": groups[:validation_count], "test": groups[validation_count:validation_count + test_count],
                       "train": groups[validation_count + test_count:]}
        output = {name: [i for g in assignments[name] for i in g] for name in SPLITS}
    if any(not indices for indices in output.values()):
        raise ValueError("empty split")
    return output, warnings


def validate_records(records):
    groups, warnings = leakage_groups(records)
    errors = []
    for group in groups:
        if len({records[i].split for i in group if records[i].split}) > 1:
            errors.append("query_or_near_duplicate_crosses_split")
        if len({records[i].purpose for i in group}) > 1:
            errors.append("development_overlaps_final_evaluation")
    trainable = [r for r in records if r.label_status == "human_verified" and r.execution_status == "OK"
                 and r.data_kind == "real" and r.purpose == "observer_development" and r.label in ("ANSWER", "REFUSAL", "OTHER")]
    return {"readiness": Readiness.NEEDS_LABELS.value, "records": len(records), "independent_groups": len(groups),
            "human_verified_trainable": len(trainable), "class_counts": dict(Counter(r.label for r in trainable)),
            "execution_statuses": dict(Counter(r.execution_status for r in records)),
            "label_statuses": dict(Counter(r.label_status for r in records)), "errors": sorted(set(errors)),
            "leakage_warnings": warnings, "valid": not errors,
            "note": "Labels alone do not establish trained or validated observer readiness."}


def dataset_hash(records):
    return digest([asdict(r) for r in records])
