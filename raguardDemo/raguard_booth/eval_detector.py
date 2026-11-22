"""부스 탐지기 오프라인 점검 — LLM 없이 탐지만 돌려서 청크 단위 TPR/FPR을 잰다.

등록 질의 전체(350)에 대해 baseline(정상 문서만)과 expanded(+공격 298) 두 조건의
Top-5에 부스 ON 경로의 탐지기를 적용한다.

  --detector c1        legacy_calib_heldout (논문 C1 이식본, 기본값)
  --detector improved  부스 기존 의미 탐지 (improved 계열, 0.35)
  --c1-chunks PATH     태호 C1 청크 판정(chunks_legacy_calib_heldout.jsonl)과 일치율 비교
                       (이식이 제대로 됐는지 확인용. A1→expanded, A0→baseline으로 대응)

비교 기준 (논문 C1): A1 TPR 99.31%, FPR 5.74% / A0 FPR 5.03%

사용법 (raguard_booth 폴더에서)
  python eval_detector.py --c1-chunks ..\\..\\results\\c1_taeho\\C1_detect\\chunks_legacy_calib_heldout.jsonl
  python eval_detector.py --qids test328,test12
출력: detector_eval_<detector>.csv (청크별), detector_eval_<detector>_summary.json
"""
import argparse, csv, json, sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from booth_runtime import Chunk, DEFAULT_EMBED_MODEL, _TransferContext, _detect_and_clean  # noqa: E402
from transfer_index import TransferIndex, load_questions  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--transfer-dir", type=Path, default=ROOT.parent / "rag_transfer")
    ap.add_argument("--detector", choices=("c1", "improved"), default="c1")
    ap.add_argument("--qids", default="", help="쉼표로 구분한 qid (기본: 전체)")
    ap.add_argument("--c1-chunks", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or ROOT / f"detector_eval_{args.detector}.csv"

    qids = [q["qid"] for q in load_questions(args.transfer_dir)]
    if args.qids:
        want = {q.strip() for q in args.qids.split(",") if q.strip()}
        qids = [q for q in qids if q in want]
    print(f"질의 {len(qids)}개, 탐지기 {args.detector}, 임베딩 모델 로드 중...")
    ctx = _TransferContext(DEFAULT_EMBED_MODEL)
    guard = None
    if args.detector == "c1":
        from legacy_guard import LegacyCalibGuard
        guard = LegacyCalibGuard(ctx.embed)
        print(f"  heldout redirect 프로토타입 {guard.n_redirect}개, 임곗값 {guard.condition}")

    rows = []
    t0 = perf_counter()
    for mode in ("baseline", "expanded"):
        index = TransferIndex(args.transfer_dir, expanded=(mode == "expanded"))
        for i, qid in enumerate(qids, 1):
            ret = index.search(qid)
            hits = ret["top5"]
            texts = index.read_documents(hits)
            if guard is not None:
                verdicts = [guard.evaluate(ret["question"], texts.get(h["doc_id"], "")) for h in hits]
                flags = [v["flag"] for v in verdicts]
                scores = [v["score"] for v in verdicts]
            else:
                cands = [Chunk(h["rank"], f"검색 문서 {h['rank']}", f"rank-{h['rank']}", 1,
                               texts.get(h["doc_id"], ""), "rag_transfer") for h in hits]
                qvec = ctx.embed([ret["question"]])[0]
                _, dets, _, _ = _detect_and_clean(ctx, cands, qvec, drop_detected=True)
                hit_loc = {d["location"]: d["score"] for d in dets}
                flags = [c.location in hit_loc for c in cands]
                scores = [hit_loc.get(c.location, float("nan")) for c in cands]
            for h, f, s in zip(hits, flags, scores):
                rows.append({"mode": mode, "qid": qid, "rank": h["rank"], "doc_id": h["doc_id"],
                             "additional": h["additional"], "detected": f, "score": round(s, 4)})
            if i % 50 == 0:
                print(f"  {mode} {i}/{len(qids)}  ({perf_counter() - t0:.0f}s)")

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def rate(sel):
        n = len(sel)
        k = sum(r["detected"] for r in sel)
        return {"k": k, "n": n, "rate": round(k / n, 4) if n else None}

    summary = {"detector": args.detector}
    for mode in ("baseline", "expanded"):
        m = [r for r in rows if r["mode"] == mode]
        summary[mode] = {
            "attack_chunks_TPR": rate([r for r in m if r["additional"]]),
            "clean_chunks_FPR": rate([r for r in m if not r["additional"]]),
            "queries_with_clean_flag": len({r["qid"] for r in m if r["detected"] and not r["additional"]}),
        }

    if args.c1_chunks:
        ref = {}
        for line in args.c1_chunks.open(encoding="utf-8"):
            r = json.loads(line)
            ref[("expanded" if r["run"] == "A1" else "baseline", r["qid"], r["chunk_id"])] = bool(r["flag"])
        agree = {"baseline": [0, 0], "expanded": [0, 0]}
        diffs = []
        for r in rows:
            key = (r["mode"], r["qid"], r["doc_id"])
            if key in ref:
                agree[r["mode"]][1] += 1
                if ref[key] == r["detected"]:
                    agree[r["mode"]][0] += 1
                elif len(diffs) < 20:
                    diffs.append({"mode": r["mode"], "qid": r["qid"], "doc_id": r["doc_id"],
                                  "booth": r["detected"], "c1": ref[key]})
        summary["agreement_with_C1"] = {
            m: {"same": a, "compared": n, "rate": round(a / n, 4) if n else None} for m, (a, n) in agree.items()
        }
        summary["disagreement_examples"] = diffs

    summary["reference_C1"] = {"A1_TPR": 0.9931, "A1_FPR": 0.0574, "A0_FPR": 0.0503}
    summary["seconds"] = round(perf_counter() - t0, 1)
    out.with_name(out.stem + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "disagreement_examples"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
