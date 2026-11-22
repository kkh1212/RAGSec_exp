"""Evidence-backed preview responses and fixed, illustrative booth scenarios."""

import unicodedata
from pathlib import Path
from typing import Any


CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"

EXAMPLES = [
    {
        "id": "travel",
        "label": "출장비 정산",
        "question": "출장 교통비는 언제까지 어떤 서류로 정산하나요?",
        "kind": "live",
    },
    {
        "id": "leave",
        "label": "연차 신청",
        "question": "연차 신청 절차를 알려 줘",
        "kind": "live",
    },
    {
        "id": "laptop",
        "label": "노트북 대여",
        "question": "업무용 노트북은 얼마나 빌릴 수 있고 어디서 받나요?",
        "kind": "live",
    },
    {
        "id": "meeting",
        "label": "회의실 예약",
        "question": "회의실은 한 번에 몇 시간까지 예약할 수 있나요?",
        "kind": "live",
    },
    {
        "id": "recovery",
        "label": "정상 응답 복구",
        "question": "직무 교육비는 연간 얼마까지 지원되나요?",
        "kind": "simulation",
    },
    {
        "id": "false_positive",
        "label": "오탐",
        "question": "방문객은 임시 출입증을 어디에서 받나요?",
        "kind": "simulation",
    },
    {
        "id": "miss",
        "label": "미탐",
        "question": "업무용 택배는 어디서 받을 수 있나요?",
        "kind": "simulation",
    },
    {
        "id": "defense-check",
        "label": "탐지·정제 확인",
        "question": "사내 휴대용 모니터의 대여 기간은 며칠인가요?",
        "kind": "defense",
    },
]

EXAMPLES_BY_ID = {example["id"]: example for example in EXAMPLES}

PREVIEW_RESPONSES = {
    "travel": (
        "TRV-01_교통비.md",
        "복귀일 다음 영업일부터 5영업일 이내에 정산해야 합니다. "
        "포털의 ‘출장비 정산’ 메뉴에 교통수단별 영수증과 승인된 출장 신청 번호를 제출하세요.",
    ),
    "leave": (
        "HR-02_연차휴가.md",
        "휴가 시작일 3영업일 전까지 사내 포털의 ‘근태 → 휴가 신청’에서 신청하세요. "
        "팀장이 승인하면 일정이 확정됩니다.",
    ),
    "laptop": (
        "IT-03_노트북대여.md",
        "수령일부터 최대 7일 동안 빌릴 수 있습니다. "
        "자산 포털의 ‘공용 장비 대여’에서 신청한 뒤 본관 2층 IT 서비스 데스크에서 수령하세요.",
    ),
    "meeting": (
        "FAC-04_회의실.md",
        "한 번에 최대 2시간까지 예약할 수 있습니다. "
        "예약은 포털의 ‘시설 예약 → 회의실’에서 30분 단위로 할 수 있습니다.",
    ),
}

Side = dict[str, Any]


def _source(filename: str) -> dict[str, str]:
    lines = (CORPUS_ROOT / filename).read_text(encoding="utf-8").splitlines()
    section = next(index for index, line in enumerate(lines) if line.startswith("## "))
    excerpt = next(index for index in range(section + 1, len(lines)) if lines[index].strip())
    return {
        "title": lines[0].removeprefix("# "),
        "location": f"corpus/{filename} · {excerpt + 1}행",
        "text": lines[excerpt],
    }


def _side(answer: str, status: str, *, source: dict[str, str] | None = None) -> Side:
    return {
        "answer": answer,
        "status": status,
        "seconds": None,
        "sources": [source] if source else [],
        "timings": [],
    }


def _comparison(question: str, mode: str, off: Side, on: Side) -> dict[str, Any]:
    return {
        "question": question,
        "mode": mode,
        "off": off,
        "on": on,
        "overhead": {"seconds": None, "percent": None},
    }


def _recovery() -> tuple[Side, Side]:
    source = _source("EDU-06_교육비.md")
    omitted_sentence = "[주제와 무관한 위험 문장 생략]"
    off = _side("모델이 답변 제공을 거절했다고 가정한 사례입니다.", "refused")
    on = _side(
        "사전 승인된 직무 교육은 직원 1인당 연간 최대 30만 원까지 지원받을 수 있습니다.",
        "ok",
        source=source,
    )
    location = f"{source['location']} · 모의 문맥"
    on["detections"] = [{
        "title": source["title"],
        "location": location,
        "score": None,
        "reason": "교육비 규정의 주제와 관련이 낮은 문장이 섞였다고 가정한 사례입니다.",
    }]
    on["sanitization"] = [{
        "title": source["title"],
        "location": location,
        "before": f"{source['text']}\n{omitted_sentence}",
        "after": source["text"],
        "removed": [omitted_sentence],
        "action": "sanitized",
    }]
    return off, on


def _false_positive() -> tuple[Side, Side]:
    source = _source("VIS-05_방문객.md")
    off = _side(
        "방문객은 도착 후 본관 1층 안내 데스크에서 임시 출입증을 받습니다.",
        "ok",
        source=source,
    )
    on = _side("정제 과정에서 출입증 수령 근거가 삭제되어 답변에 필요한 문맥이 부족합니다.", "insufficient")
    on["detections"] = [{
        "title": source["title"],
        "location": source["location"],
        "score": None,
        "reason": "탐지기가 정상적인 출입 안내 문단을 정제 대상으로 잘못 분류했다고 가정한 사례입니다.",
    }]
    on["sanitization"] = [{
        "title": source["title"],
        "location": source["location"],
        "before": source["text"],
        "after": "",
        "removed": [source["text"]],
        "action": "dropped",
    }]
    return off, on


def _miss() -> tuple[Side, Side]:
    off = _side("모델이 답변 제공을 거절했다고 가정한 사례입니다.", "refused")
    on = _side(
        "탐지기가 정제 후보를 찾지 못했고 모델도 답변 제공을 거절했다고 가정한 사례입니다.",
        "refused",
    )
    on["detections"] = []
    on["sanitization"] = []
    return off, on


SIMULATIONS = {
    "recovery": _recovery,
    "false_positive": _false_positive,
    "miss": _miss,
}


def simulate_case(example_id: str) -> dict[str, Any]:
    off, on = SIMULATIONS[example_id]()
    question = EXAMPLES_BY_ID[example_id]["question"]
    return _comparison(question, "simulation", off, on)


def _question_key(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return "".join(character for character in normalized if character.isalnum())


def preview_compare(question: str) -> dict[str, Any]:
    question_key = _question_key(question)
    for example_id, (filename, answer) in PREVIEW_RESPONSES.items():
        if question_key == _question_key(EXAMPLES_BY_ID[example_id]["question"]):
            return _comparison(
                question,
                "preview",
                _side(answer, "ok", source=_source(filename)),
                _side(answer, "ok", source=_source(filename)),
            )

    message = "미리보기에서는 예시 질문의 응답만 제공합니다. 예시를 선택하거나 실제 실행 모드에서 질문해 주세요."
    return _comparison(question, "preview", _side(message, "insufficient"), _side(message, "insufficient"))
