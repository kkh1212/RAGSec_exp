import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import faiss
import numpy as np


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def read_json(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def read_jsonl(name):
    with (DATA / name).open(encoding="utf-8") as source:
        yield from map(json.loads, source)


def search(qid, expanded):
    index = faiss.read_index(str(DATA / "clean.index"))
    clean_count = index.ntotal
    ids = read_json("clean_ids.json")
    if len(ids) != clean_count or len(set(ids)) != clean_count:
        raise ValueError("clean_ids.json must have one unique ID per clean vector.")

    queries = list(read_jsonl("queries.jsonl"))
    row = next((i for i, query in enumerate(queries) if query["qid"] == qid), None)
    if row is None:
        raise ValueError(f"Unknown question ID: {qid}. See data/queries.jsonl.")
    query_vecs = np.load(DATA / "query_vecs.npy", allow_pickle=False)
    if query_vecs.shape != (len(queries), index.d):
        raise ValueError("query_vecs.npy does not match queries.jsonl or the index dimension.")

    if expanded:
        extra_ids = read_json("additional_ids.json")
        extra_vecs = np.load(DATA / "addtional_vecs.npy", allow_pickle=False)
        if extra_vecs.shape != (len(extra_ids), index.d) or not np.isfinite(extra_vecs).all():
            raise ValueError("addtional_vecs.npy does not match the ID map or index dimension.")
        if len(set(extra_ids)) != len(extra_ids) or set(ids).intersection(extra_ids):
            raise ValueError("Additional document IDs must be unique and distinct from clean IDs.")
        index.add(np.ascontiguousarray(extra_vecs, dtype=np.float32))
        ids.extend(extra_ids)
    if index.ntotal != len(ids):
        raise ValueError("Index row count does not match the document ID map.")

    query_vec = np.ascontiguousarray(query_vecs[row:row + 1], dtype=np.float32)
    scores, positions = index.search(query_vec, 5)
    hits = [
        {"rank": rank, "doc_id": ids[position], "score": float(score),
         "additional": bool(position >= clean_count)}
        for rank, (score, position) in enumerate(zip(scores[0], positions[0]), 1)
        if position >= 0
    ]
    return {
        "qid": qid, "question": queries[row]["text"], "top_k": 5,
        "mode": "expanded" if expanded else "baseline",
        "vector_count": index.ntotal, "top5": hits,
    }


def generate(result, args):
    context_ids = [hit["doc_id"] for hit in result["top5"]]
    documents = {doc["_id"]: doc["text"] for doc in read_jsonl("clean_docs.jsonl")
                 if doc["_id"] in context_ids}
    missing = [doc_id for doc_id in context_ids if doc_id not in documents]
    if missing:
        raise ValueError(f"Missing clean text for: {', '.join(missing)}")
    template = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    prompt = template.format(question=result["question"],
                             context="\n".join(documents[doc_id] for doc_id in context_ids))
    payload = {
        "model": args.model, "prompt": prompt, "stream": False, "think": False,
        "options": {"temperature": 0, "top_p": 0.9, "repeat_penalty": 1.05,
                    "num_thread": 4, "num_batch": 32, "num_ctx": 4096, "num_predict": 1024},
    }
    endpoint = args.ollama_url.rstrip("/") + "/api/generate"
    request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=180) as response:
        completion = json.load(response)
    text = completion["response"]
    reason = completion.get("done_reason")
    answer = text.rsplit("</think>", 1)[-1].strip() if "</think>" in text or reason != "length" else None
    return {"model": completion["model"], "context_doc_ids": context_ids,
            "done_reason": reason, "answer": answer}


def main():
    parser = argparse.ArgumentParser(description="Portable NQ demo retrieval and baseline RAG.")
    parser.add_argument("--mode", choices=("baseline", "expanded"), default="baseline")
    parser.add_argument("--qid", default="test12", help="Question ID from data/queries.jsonl.")
    parser.add_argument("--generate", action="store_true", help="Generate an Ollama answer in baseline mode.")
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "qwen3:4b"))
    args = parser.parse_args()
    if args.mode == "expanded" and args.generate:
        parser.error("Expanded mode provides vector search and IDs. Generation uses baseline documents.")
    try:
        result = search(args.qid, args.mode == "expanded")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if args.generate:
            print(json.dumps(generate(result, args), ensure_ascii=False, indent=2))
    except HTTPError as error:
        parser.exit(1, f"Ollama HTTP {error.code}: {error.read().decode('utf-8', errors='replace')}\n")
    except (OSError, URLError, ValueError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
