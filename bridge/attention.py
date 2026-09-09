"""Deterministic, explainable attention inference for completed Codex turns."""

from __future__ import annotations

import re
from typing import Any, Dict, List


MAX_ANALYSIS_TEXT = 8_000
MAX_QUESTION_TEXT = 1_000

_FENCED_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_DIRECT_PATTERNS = (
    re.compile(
        r"请(?:你)?(?:选择|确认|提供|补充|描述|回复|回答|输入|决定|说明|授权|告诉我)"
    ),
    re.compile(r"需要你(?:选择|确认|提供|补充|回复|回答|决定|说明|授权)"),
    re.compile(
        r"(?:回复|选择|选|输入)\s*[`'\"“‘]?\s*(?:[12AB]|继续|是|否|确认|按此(?:实现|执行))(?:\b|[。！!，,；;])",
        re.I,
    ),
    re.compile(
        r"(?:是否|能否|可否|可以|可不可以)[^。！？!?\n]{0,16}(?:继续|同意|确认|允许|采用|按此|开始|执行)"
    ),
    re.compile(r"你(?:希望|倾向|选择|决定)(?:哪|使用|采用|先|要)", re.I),
    re.compile(
        r"\bplease\s+(?:choose|select|confirm|provide|reply|answer|describe|tell|enter)\b",
        re.I,
    ),
    re.compile(r"\b(?:which option|what should I|would you like me to)\b", re.I),
)
_CONFIRMATION = re.compile(
    r"确认|是否继续|能否继续|可否继续|同意|允许|按此实现|开始执行|\bconfirm\b|\bcontinue\b",
    re.I,
)
_CHOICE = re.compile(
    r"选择|选项|哪(?:一个|项|种)|(?:^|\s)[12AB]\s*[/／]\s*[12AB](?:\s*[/／]\s*[12AB])*|\bchoose\b|\bselect\b|\boption\b",
    re.I,
)
_CLOSED_WITHOUT_ACTION = re.compile(
    r"(?:已完成|已经完成|无需操作|不需要回复|无需回复|仅供参考|没有后续操作)"
)
_COMPLETED_ACTION_PREFIX = re.compile(
    r"(?:已|已经|我已|你已|刚刚)\s*$", re.I
)
_QUESTION = re.compile(r"[？?]")
_RHETORICAL_ONLY = re.compile(
    r"^(?:为什么|为何|怎么会|难道|何尝|what is|why is|why does|how does)", re.I
)


def _clean(text: str) -> str:
    value = str(text or "")[:MAX_ANALYSIS_TEXT]
    value = _FENCED_BLOCK.sub("\n", value)
    value = _INLINE_CODE.sub("", value)
    value = _MARKDOWN_LINK.sub(r"\1", value)
    kept = []
    for line in value.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _paragraphs(text: str) -> List[str]:
    values = []
    for paragraph in re.split(r"\n\s*\n", text):
        normalized = " ".join(part.strip() for part in paragraph.splitlines()).strip()
        normalized = re.sub(r"^[#*\-\d.、)）\s]+", "", normalized)
        if normalized:
            values.append(normalized)
    return values


def _last_direct_match(paragraphs: List[str]) -> tuple[int, re.Match[str]] | None:
    result = None
    for index, paragraph in enumerate(paragraphs):
        for pattern in _DIRECT_PATTERNS:
            for match in pattern.finditer(paragraph):
                # "已选择 1" reports a completed action; it is not an instruction
                # asking the user to choose again.  Keep looking because a later
                # sentence in the same answer may still contain a real request.
                prefix = paragraph[max(0, match.start() - 6) : match.start()]
                if _COMPLETED_ACTION_PREFIX.search(prefix):
                    continue
                result = (index, match)
    return result


def _question_excerpt(paragraph: str, match_start: int = 0) -> str:
    if len(paragraph) <= MAX_QUESTION_TEXT:
        return paragraph
    start = max(0, match_start - 120)
    return paragraph[start : start + MAX_QUESTION_TEXT].strip()


def _extract_options(text: str) -> List[str]:
    values: List[str] = []
    for sequence in re.findall(
        r"(?<![\w])(?:[12AB]\s*[/／]\s*)+[12AB](?![\w])", text, re.I
    ):
        for token in re.findall(r"[12AB]", sequence, re.I):
            value = token.upper()
            if value not in values:
                values.append(value)
    for match in re.finditer(r"(?<![\w])([12AB])\s*(?=[/／、.．:：)）]|$)", text, re.I):
        value = match.group(1).upper()
        if value not in values:
            values.append(value)
    return values[:4]


def classify_agent_message(
    text: str,
    *,
    message_id: str = "",
    phase: str | None = "final_answer",
    turn_status: str | None = "completed",
) -> Dict[str, Any]:
    """Return an attention projection without invoking another model.

    Only a final answer from a settled turn may become an inferred blocker.
    Callers can pass ``phase=None`` for legacy records whose turn is known to
    have completed.
    """

    normalized_phase = str(phase or "")
    normalized_status = str(turn_status or "")
    if normalized_phase and normalized_phase != "final_answer":
        return {"type": "none", "evidence": ["not_final_answer"]}
    if normalized_status and normalized_status not in {"completed", "interrupted"}:
        return {"type": "none", "evidence": ["turn_not_settled"]}

    cleaned = _clean(text)
    paragraphs = _paragraphs(cleaned)
    if not paragraphs:
        return {"type": "none", "evidence": ["empty_final_answer"]}

    direct = _last_direct_match(paragraphs)
    if direct is not None:
        index, match = direct
        tail_after_request = " ".join(paragraphs[index:])
        closure = _CLOSED_WITHOUT_ACTION.search(tail_after_request)
        if closure is None or closure.start() <= match.start():
            question = _question_excerpt(paragraphs[index], match.start())
            response_mode = "free_text"
            if _CHOICE.search(question):
                response_mode = "choice"
            elif _CONFIRMATION.search(question):
                response_mode = "confirmation"
            result: Dict[str, Any] = {
                "type": "required_inferred",
                "responseMode": response_mode,
                "question": question,
                "options": _extract_options(question),
                "evidence": ["direct_request", "final_answer", "no_user_reply"],
            }
            if message_id:
                result["messageId"] = str(message_id)
            return result

    last_actionable = paragraphs[-1]
    if _QUESTION.search(last_actionable) and not _CLOSED_WITHOUT_ACTION.search(last_actionable):
        sentence = re.split(r"(?<=[。！？!?])\s*", last_actionable)[-2:]
        excerpt = " ".join(value for value in sentence if value).strip() or last_actionable
        if not _RHETORICAL_ONLY.match(excerpt):
            result = {
                "type": "possible_inferred",
                "responseMode": "free_text",
                "question": _question_excerpt(excerpt),
                "options": _extract_options(excerpt),
                "evidence": ["question_in_final_answer", "no_user_reply"],
            }
            if message_id:
                result["messageId"] = str(message_id)
            return result

    return {"type": "none", "evidence": ["no_direct_user_action"]}
