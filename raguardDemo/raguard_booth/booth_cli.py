"""Terminal interface for paired RAGuard booth comparisons."""

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import urlopen

from booth_cases import EXAMPLES, preview_compare, simulate_case

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"
DEFENSE_CORPUS = ROOT / "fixtures" / "defense"
V3_PROFILE = ROOT / "profiles" / "v3"
EXPERIMENT_PROFILE = "experiment-c"
EXPERIMENT_DATA = ROOT / "experiment_c_data"
TRANSFER_PROFILE = "transfer"
TRANSFER_DATA = ROOT.parent / "rag_transfer"
WORKSPACE_EXPERIMENT_DATA = (
    ROOT.parent / "muterag_acd_experiment_bundle_20260603" / "experiment_C" / "data" / "experiments_v3"
)
PROFILE_LABELS = {EXPERIMENT_PROFILE: "실험 C", "v3": "V3", TRANSFER_PROFILE: "전달 인덱스"}
SEARCH_MODE_LABELS = {"baseline": "기준", "expanded": "확장"}
MODES = {"live": "실시간 실행", "preview": "미리보기 · 실측 없음", "simulation": "모의 사례 · 실측 없음"}
STATUSES = {
    "ok": "응답 반환",
    "refused": "모델 응답 거절",
    "insufficient": "근거 부족",
    "error": "실행 오류",
}
EDIT_ACTIONS = {
    "sanitize": "문장 정제",
    "sanitized": "문장 정제",
    "drop": "청크 제외",
    "dropped": "청크 제외",
    "keep": "청크 유지",
}
OLLAMA_DURATION_LABELS = {
    "load_seconds": "모델 로딩",
    "prompt_seconds": "입력 처리",
    "generation_seconds": "출력 생성",
}
OLLAMA_TOKEN_LABELS = {"prompt_tokens": "입력 토큰", "generated_tokens": "출력 토큰"}


class BoothError(Exception):
    """An actionable terminal setup or input error."""


def _experiment_examples(path: Path) -> list[dict]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))["questions"]
        if not isinstance(rows, list) or not rows:
            raise ValueError
        examples = []
        ids = set()
        for row in rows:
            qid = row["qid"].strip()
            question = row["question"].strip()
            if not qid or not question or qid in ids:
                raise ValueError
            ids.add(qid)
            examples.append({"id": qid, "question": question, "kind": EXPERIMENT_PROFILE})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise BoothError(f"실험 C 예시 질문을 읽을 수 없습니다. {path} 파일을 확인하세요.") from None
    return examples


def _model_setting(name: str, default: str) -> str:
    return os.getenv(name, "").strip() or default


def _check_ollama(url: str, model: str) -> None:
    try:
        with urlopen(f"{url.rstrip('/')}/api/tags", timeout=5) as response:
            models = json.load(response)["models"]
        available = any(model in (entry.get("name"), entry.get("model")) for entry in models)
    except (OSError, KeyError, ValueError, TypeError, AttributeError):
        raise BoothError("Ollama 연결 실패. Ollama 서비스를 시작하고 BOOTH_OLLAMA_URL을 확인하세요.") from None
    if not available:
        raise BoothError(f"Ollama 모델이 필요합니다. Ollama 서버에서 ollama pull {model} 을 실행하세요.")


class Booth:
    def __init__(self, mode: str, profile: str = EXPERIMENT_PROFILE):
        self.mode = mode
        self.profile = profile
        self.runtime = None
        self.corpus = CORPUS
        if profile == EXPERIMENT_PROFILE:
            data_dir = EXPERIMENT_DATA
            if not data_dir.exists() and WORKSPACE_EXPERIMENT_DATA.is_dir():
                data_dir = WORKSPACE_EXPERIMENT_DATA
            self.corpus = (data_dir / "experiment_docs").resolve()
            self.examples = _experiment_examples(data_dir / "questions" / "v3_questions.json")
        elif profile == "v3":
            self.corpus = V3_PROFILE / "normal"
            try:
                self.examples = json.loads((V3_PROFILE / "examples.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise BoothError("V3 예시를 읽을 수 없습니다. profiles/v3 폴더가 함께 복사됐는지 확인하세요.") from None
        else:
            self.examples = EXAMPLES
        self.example_ids = {example["id"]: example for example in self.examples}

    def _live_runtime(self, corpus: Path, replacement: Path | None = None):
        if self.runtime is not None and (self.runtime.corpus_path, self.runtime.replacement) == (corpus, replacement):
            return self.runtime
        if not corpus.is_dir():
            raise BoothError(f"코퍼스 폴더를 찾을 수 없습니다. {corpus} 경로를 확인하세요.")
        try:
            from booth_runtime import DEFAULT_MODEL, DEFAULT_OLLAMA_URL, build_runtime
        except ImportError:
            raise BoothError("라이브 패키지가 필요합니다. bash setup.sh live 를 실행하세요.") from None

        _check_ollama(
            _model_setting("BOOTH_OLLAMA_URL", DEFAULT_OLLAMA_URL),
            _model_setting("BOOTH_MODEL", DEFAULT_MODEL),
        )
        self.runtime = None
        runtime = build_runtime(corpus, replacement=replacement)
        try:
            runtime.warmup()
        except Exception as exc:
            raise BoothError(
                f"검색 인덱스 준비 실패 ({type(exc).__name__}). 임베딩 모델 다운로드와 {corpus} 폴더를 확인하세요."
            ) from None
        self.runtime = runtime
        return runtime

    def compare(self, question: str, example_id: str | None = None) -> dict:
        corpus = self.corpus
        replacement = None
        example = self.example_ids.get(example_id)
        if example_id:
            if example is None:
                raise BoothError("현재 프로필에 해당 예시가 없습니다. --list-examples 로 목록을 확인하세요.")
            question = example["question"]
            if example["kind"] == "simulation":
                return simulate_case(example_id)
            if example["kind"] == "defense":
                if self.mode == "preview":
                    raise BoothError("8번은 실제 방어 확인 사례입니다. bash run.sh live --example defense-check 로 실행하세요.")
                corpus = DEFENSE_CORPUS
            if example["kind"] == "v3-review":
                replacement = V3_PROFILE / "review" / example["filename"]
        if not question or len(question) > 500:
            raise BoothError("질문을 1~500자로 입력해 주세요.")
        if self.mode == "preview":
            if self.profile in PROFILE_LABELS:
                raise BoothError(f"비교를 보려면 bash run.sh live --profile {self.profile} 를 실행하세요.")
            return preview_compare(question)
        result = self._live_runtime(corpus, replacement).compare(question)
        if corpus == DEFENSE_CORPUS:
            result["example_id"] = example_id
        if self.profile == EXPERIMENT_PROFILE:
            result["profile"] = EXPERIMENT_PROFILE
            if example is not None:
                result["source_question_id"] = example["id"]
        elif self.profile == "v3":
            result["profile"] = "v3"
            result["corpus_condition"] = "무해한 문장 혼입" if replacement is not None else "정상 원본"
            if example is not None:
                result["source_question_id"] = example["qid"]
                result["reference_filename"] = example["filename"]
        return {"mode": "live", "question": question, **result}


class TransferBooth:
    def __init__(self, mode: str, root: Path, search_mode: str, search_only: bool, *, check_mapping: bool = False):
        self.mode = mode
        self.root = root
        self.search_mode = search_mode
        self.search_only = search_only
        self.check_mapping = check_mapping
        self.index = None
        self.runtime = None
        try:
            from transfer_index import load_questions

            self.examples = [
                {"id": row["qid"], "question": row["text"], "kind": TRANSFER_PROFILE}
                for row in load_questions(root)
            ]
        except Exception as exc:
            raise BoothError(
                f"전달 질문 목록을 준비할 수 없습니다 ({type(exc).__name__}). {root} 경로와 설치 상태를 확인하세요."
            ) from None
        self.example_ids = {example["id"]: example for example in self.examples}

    def _resolve_question(self, question: str, example_id: str | None) -> dict:
        if example_id is not None:
            if example_id in self.example_ids:
                return self.example_ids[example_id]
        else:
            matches = [example for example in self.examples if example["question"] == question]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise BoothError("이 질문에 해당하는 ID가 여러 개입니다. --example로 ID를 선택하세요.")
        raise BoothError(
            "전달 인덱스에는 등록 질문의 사전 계산 벡터가 있습니다. --list-examples로 ID를 확인한 뒤 "
            "--example로 선택하거나 등록된 질문과 정확히 일치하는 문장을 입력하세요."
        )

    def _index(self):
        if self.index is None:
            try:
                from transfer_index import TransferIndex

                self.index = TransferIndex(self.root, expanded=self.search_mode == "expanded")
            except Exception as exc:
                raise BoothError(
                    f"전달 검색 인덱스를 열 수 없습니다 ({type(exc).__name__}). {self.root} 경로와 검색 모드를 확인하세요."
                ) from None
        return self.index

    def _live_runtime(self, index):
        if self.runtime is not None:
            return self.runtime
        try:
            from booth_runtime import build_transfer_runtime
        except ImportError:
            raise BoothError("라이브 패키지가 필요합니다. bash setup.sh live 를 실행하세요.") from None
        try:
            self.runtime = build_transfer_runtime(index)
        except Exception as exc:
            raise BoothError(
                f"전달 실시간 실행을 준비할 수 없습니다 ({type(exc).__name__}). 모델과 전달 문서 설정을 확인하세요."
            ) from None
        return self.runtime

    def compare(self, question: str, example_id: str | None = None) -> dict:
        example = self._resolve_question(question, example_id)
        if self.mode == "preview" and not self.search_only:
            raise BoothError("전달 인덱스에서는 --search-only로 검색하거나 bash run.sh live 로 비교하세요.")
        index = self._index()
        runtime = None if self.search_only else self._live_runtime(index)
        try:
            if self.search_only:
                retrieval = index.search(example["id"])
                if self.check_mapping:
                    retrieval["mapping"] = index.check_mapping(retrieval["top5"])
                return retrieval
            result = runtime.compare(example["id"])
        except Exception as exc:
            raise BoothError(f"전달 실험을 실행할 수 없습니다 ({type(exc).__name__}).") from None
        return {
            **result,
            "mode": "live",
            "profile": TRANSFER_PROFILE,
            "question": example["question"],
            "source_question_id": example["id"],
        }


def _terminal_text(value: str) -> str:
    return "".join(
        char for char in str(value)
        if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159)
    )


def _write(label: str, value: str) -> None:
    print(f"  {label}  {_terminal_text(value)}")


def _sources(side: dict, all_sources: bool) -> None:
    sources = side["sources"]
    if not sources:
        return
    cited = [item for item in sources if item.get("cited")]
    numbered = any("number" in item for item in sources)
    if all_sources or not numbered:
        shown = sources
    elif cited:
        shown = cited
    else:
        shown = sources[:2]
    print("  문서 근거" if not numbered else "  문서 인용 및 검색 문맥")
    for item in shown:
        index = f"[{item['number']}] " if "number" in item else ""
        kind = " · 답변 인용" if item.get("cited") else ""
        _write("•", f"{index}{item['title']}{kind} · {item['location']}")
        if "text" in item:
            _write(" ", item["text"])
    if len(sources) > len(shown):
        print(f"    검색 문맥 {len(sources) - len(shown)}건 생략 · --all-sources 로 보기")


def _seconds(value: float | None) -> str:
    return f"{value:.3f}초" if value is not None else "미측정"


def _print_side(label: str, side: dict, *, secure: bool, all_sources: bool) -> None:
    print(f"\n[{label}]  {STATUSES[side['status']]}  |  총 {_seconds(side['seconds'])}")
    _write("답변", side["answer"])
    _sources(side, all_sources)
    ollama = side.get("ollama", {})
    details = [
        f"{name} {_seconds(ollama[key])}"
        for key, name in OLLAMA_DURATION_LABELS.items()
        if ollama.get(key) is not None
    ]
    details.extend(
        f"{name} {ollama[key]}개"
        for key, name in OLLAMA_TOKEN_LABELS.items()
        if ollama.get(key) is not None
    )
    if details:
        _write("Ollama 측정", " · ".join(details))
    if not secure:
        return
    for detection in side.get("detections", []):
        score = detection.get("score")
        score_label = f" · 점수 {score:.3f}" if score is not None else ""
        _write("탐지", f"{detection['title']} · {detection['location']}{score_label}")
        _write("근거", detection["reason"])
    if not side.get("detections"):
        _write("탐지", "기록된 후보 없음")
    for edit in side.get("sanitization", []):
        _write("정제", f"{EDIT_ACTIONS.get(edit['action'], edit['action'])} · {edit['location']}")
        if "before" in edit:
            _write("정제 전", edit["before"] or "내용 없음")
        if "after" in edit:
            _write("정제 후", edit["after"] or "남은 내용 없음")
        if edit.get("removed"):
            _write("제거 내용", "\n".join(edit["removed"]))
        if "removed_count" in edit:
            _write("제거 문장", f"{edit['removed_count']}건")
    if not side.get("sanitization"):
        _write("정제", "기록된 변경 없음")
    if side.get("timings"):
        timings = " · ".join(f"{item['label']} {_seconds(item['seconds'])}" for item in side["timings"])
        _write("단계별", timings)
    if side.get("security_seconds") is not None:
        _write("탐지·정제 자체 시간", _seconds(side["security_seconds"]))


def _without_transfer_text(result: dict) -> dict:
    filtered = result.copy()
    for side_name in ("off", "on"):
        side = result[side_name].copy()
        side["sources"] = [
            {key: value for key, value in item.items() if key != "text"}
            for item in side.get("sources", [])
        ]
        edits = []
        for edit in side.get("sanitization", []):
            metadata = {key: value for key, value in edit.items() if key not in {"before", "after", "removed"}}
            if "removed" in edit:
                metadata["removed_count"] = len(edit["removed"] or [])
            edits.append(metadata)
        if "sanitization" in side:
            side["sanitization"] = edits
        filtered[side_name] = side
    return filtered


def _print_retrieval(retrieval: dict) -> None:
    label = SEARCH_MODE_LABELS.get(retrieval["mode"], retrieval["mode"])
    _write("공통 검색", f"{label} · 검색 벡터 {retrieval['vector_count']:,}개 · k={retrieval['top_k']}")
    print("  Top-5 문서")
    for row in retrieval["top5"]:
        marker = " · 추가" if row["additional"] else ""
        _write(f"{row['rank']}.", f"{row['doc_id']} · 점수 {row['score']:.6f}{marker}")
    _write("Top-5 추가 문서", f"{retrieval['additional_count']}건")


def _print_mapping(mapping: dict) -> None:
    _write("본문 매핑", f"연결 {mapping['mapped_count']}건 · 누락 {mapping['missing_text_count']}건")
    for row in mapping["matches"]:
        marker = " · 추가" if row["additional"] else ""
        status = "연결 확인" if row["mapped"] else "누락"
        source = f" · {row['source_file']}" if row["source_file"] else ""
        _write(f"{row['rank']}.", f"{row['doc_id']}{marker} · {status}{source}")


def _print_result(
    result: dict, *, all_sources: bool, as_json: bool, search_only: bool = False, show_original_text: bool = False
) -> None:
    is_transfer = result.get("profile") == TRANSFER_PROFILE
    if is_transfer and not show_original_text:
        result = _without_transfer_text(result)
    retrieval = result if search_only else result.get("retrieval")
    if retrieval is not None:
        count = sum(row["additional"] for row in retrieval["top5"])
        retrieval = {**retrieval, "additional_count": count}
        if search_only and "mapping" in retrieval:
            retrieval.pop("question", None)
        result = retrieval if search_only else {**result, "retrieval": retrieval}
    if as_json:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return
    if search_only:
        mapping = result.get("mapping")
        if mapping is not None:
            print("\n전달 인덱스 · Top-5 본문 매핑 확인")
        else:
            print(f"\n전달 인덱스 · 검색만 실행  |  질문  {_terminal_text(result['question'])}")
        _write("질문 ID", result["qid"])
        _print_retrieval(retrieval)
        if mapping is not None:
            _print_mapping(mapping)
        return
    title = MODES[result["mode"]]
    if result.get("example_id") == "defense-check":
        title += " · 방어 확인용 문서"
    profile = result.get("profile")
    if profile in PROFILE_LABELS:
        title += f" · {PROFILE_LABELS[profile]}"
    if result.get("corpus_condition"):
        title += f" {result['corpus_condition']}"
    print(f"\n{title}  |  질문  {_terminal_text(result['question'])}")
    if result.get("source_question_id"):
        if result.get("reference_filename"):
            _write("이전 실험 기준", f"{result['source_question_id']} · {result['reference_filename']}")
        else:
            _write("질문 ID", result["source_question_id"])
    if result.get("preload_seconds") is not None:
        _write("Ollama 예열", f"{_seconds(result['preload_seconds'])} · 양쪽 총시간에서 제외")
    if result.get("execution_order"):
        _write("실행 순서", " → ".join(side.upper() for side in result["execution_order"]))
    if is_transfer:
        if retrieval is not None:
            _print_retrieval(retrieval)
        if result.get("missing_text_count"):
            _write("본문 누락", f"검색 문서 {result['missing_text_count']}건")
        if not show_original_text:
            _write("문서 표시", "근거와 정제 내역의 본문은 --show-original-text로 표시합니다.")
    _print_side("RAGuard OFF · 기본 RAG", result["off"], secure=False, all_sources=all_sources)
    _print_side("RAGuard ON · 의미 탐지·정제", result["on"], secure=True, all_sources=all_sources)
    extra = result["overhead"]["seconds"]
    percent = result["overhead"]["percent"]
    delta = f"{extra:+.3f}초" if extra is not None else "비교값 없음"
    fraction = f" ({percent:+.2f}%)" if percent is not None else ""
    print(f"\n  총시간 차이 (ON − OFF)  {delta}{fraction}\n")


def _print_examples(examples: list[dict], *, profile: str) -> None:
    if profile == TRANSFER_PROFILE:
        print(f"\n전달 질문 ID {len(examples)}개")
        for start in range(0, len(examples), 10):
            ids = [_terminal_text(example["id"]) for example in examples[start:start + 10]]
            print("  " + " · ".join(ids))
        return
    print("\n예시 질문")
    markers = {"simulation": " · 모의 사례", "defense": " · 실제 탐지·정제 확인", "v3-review": " · 무해한 문장 혼입"}
    for number, example in enumerate(examples, 1):
        text = example["label"] if profile == "v3" else example["question"]
        print(f"  {number}. {_terminal_text(text)}  [{_terminal_text(example['id'])}{markers.get(example['kind'], '')}]")


def _parse_args():
    parser = argparse.ArgumentParser(description="같은 질문에 대한 RAG 보안 OFF·ON 비교")
    parser.add_argument("--mode", choices=("live", "preview"), default="live")
    parser.add_argument(
        "--profile", choices=(EXPERIMENT_PROFILE, "booth", "v3", TRANSFER_PROFILE), default=EXPERIMENT_PROFILE,
        help="실험 C(기본값), 가상 부스, 기존 V3, 전달 인덱스 프로필 중 하나를 사용합니다.",
    )
    parser.add_argument("--transfer-dir", type=Path, help="전달 자료 위치를 지정합니다. 기본값은 인접 rag_transfer 폴더입니다.")
    parser.add_argument("--search-mode", choices=tuple(SEARCH_MODE_LABELS), help="전달 인덱스의 기준(기본값) 또는 확장 검색을 선택합니다.")
    parser.add_argument("--search-only", action="store_true", help="전달 인덱스의 검색 결과만 출력합니다.")
    parser.add_argument("--check-mapping", action="store_true", help="전달 Top-5의 본문 연결 여부와 파일명만 확인합니다.")
    parser.add_argument("--show-original-text", action="store_true", help="전달 문서의 근거와 정제 본문도 출력합니다.")
    parser.add_argument("--examples-only", action="store_true", help="등록된 예시 질문만 받습니다.")
    parser.add_argument("--list-examples", action="store_true", help="예시 번호와 ID를 출력합니다.")
    parser.add_argument("--all-sources", action="store_true", help="전체 검색 문맥을 출력합니다.")
    parser.add_argument("--json", action="store_true", help="한 건의 결과를 JSON으로 출력합니다.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--question", help="자유 질문을 한 건 실행합니다.")
    action.add_argument("--example", help="현재 프로필의 예시 ID로 한 건을 실행합니다.")
    args = parser.parse_args()
    if args.json and args.question is None and not args.example:
        parser.error("--json은 --question 또는 --example과 함께 사용하세요.")
    if args.examples_only and args.question is not None:
        parser.error("--examples-only에서는 --example로 질문을 선택하세요.")
    if args.profile != TRANSFER_PROFILE and any((
        args.transfer_dir, args.search_mode, args.search_only, args.check_mapping, args.show_original_text,
    )):
        parser.error("전달 인덱스 옵션은 --profile transfer 와 함께 사용하세요.")
    if args.check_mapping and args.show_original_text:
        parser.error("--check-mapping과 --show-original-text는 함께 사용할 수 없습니다.")
    args.search_only = args.search_only or args.check_mapping
    return args


def main() -> int:
    args = _parse_args()
    try:
        if args.profile == TRANSFER_PROFILE:
            booth = TransferBooth(
                args.mode, args.transfer_dir or TRANSFER_DATA, args.search_mode or "baseline", args.search_only,
                check_mapping=args.check_mapping,
            )
        else:
            booth = Booth(args.mode, args.profile)
    except BoothError as exc:
        print(f"실행 오류  {_terminal_text(str(exc))}", file=sys.stderr)
        return 1
    if args.list_examples:
        _print_examples(booth.examples, profile=args.profile)
        return 0
    if args.question is not None or args.example:
        try:
            result = booth.compare((args.question or "").strip(), args.example)
        except BoothError as exc:
            print(f"실행 오류  {_terminal_text(str(exc))}", file=sys.stderr)
            return 1
        _print_result(result, all_sources=args.all_sources, as_json=args.json, search_only=args.search_only,
                      show_original_text=args.show_original_text)
        if args.search_only:
            return int(bool(result.get("mapping", {}).get("missing_text_count")))
        return int(any(result[side]["status"] == "error" for side in ("off", "on")))

    label = PROFILE_LABELS.get(args.profile)
    mode_label = "검색만 실행" if args.search_only else MODES[args.mode]
    if args.check_mapping:
        mode_label = "본문 매핑 확인"
    print(f"RAGuard · {mode_label}" + (f" · {label}" if label else ""))
    if not args.search_only:
        print("OFF와 ON은 같은 모델과 지시문을 사용합니다. ON에서는 검색 문맥을 먼저 탐지·정제합니다.")
    _print_examples(booth.examples, profile=args.profile)
    if args.profile == TRANSFER_PROFILE:
        prompt = "질문 ID" if args.examples_only else "질문 ID 또는 등록된 원본 질문"
        choices = {qid: qid for qid in booth.example_ids}
    else:
        prompt = "예시 번호" if args.examples_only else "질문 또는 예시 번호"
        choices = {str(number): example["id"] for number, example in enumerate(booth.examples, 1)}
    while True:
        try:
            line = input(f"\n{prompt} (? 목록, q 종료) > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.casefold() in {"q", "quit", "exit"}:
            return 0
        if line == "?":
            _print_examples(booth.examples, profile=args.profile)
            continue
        if not line:
            continue
        example_id = choices.get(line)
        if args.examples_only and not example_id:
            print(f"목록에 있는 {prompt}를 입력해 주세요.")
            continue
        try:
            if args.mode == "live" and not args.search_only and booth.runtime is None and (
                not example_id or booth.example_ids[example_id]["kind"] != "simulation"
            ):
                if args.profile == TRANSFER_PROFILE:
                    message = "첫 실행 준비 중 · 전달 인덱스를 열어 비교합니다."
                else:
                    message = "첫 실행 준비 중 · 검색 인덱스를 만들고 Ollama 모델을 예열합니다."
                print(message, flush=True)
            result = booth.compare(line, example_id)
            _print_result(result, all_sources=args.all_sources, as_json=False, search_only=args.search_only,
                          show_original_text=args.show_original_text)
        except BoothError as exc:
            print(f"실행 오류  {_terminal_text(str(exc))}", file=sys.stderr)
        except KeyboardInterrupt:
            print()
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
