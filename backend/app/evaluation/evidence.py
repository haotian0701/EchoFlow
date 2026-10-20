import unicodedata
import uuid
from typing import Literal

from app.evaluation.contracts import Dimension, JudgeIssue, Severity
from app.models.articles import Article
from app.models.scripts import ScriptVersion


def _normalized_with_map(value: str) -> tuple[str, list[int]]:
    normalized: list[str] = []
    positions: list[int] = []
    in_space = False
    for index, character in enumerate(unicodedata.normalize("NFKC", value)):
        if character.isspace():
            if normalized and not in_space:
                normalized.append(" ")
                positions.append(index)
            in_space = True
        else:
            normalized.append(character.casefold())
            positions.append(index)
            in_space = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        positions.pop()
    return "".join(normalized), positions


def locate_quote(text: str, quote: str | None) -> tuple[int | None, int | None]:
    if not quote or not quote.strip():
        return None, None
    direct = text.find(quote)
    if direct >= 0:
        return direct, direct + len(quote)
    normalized_text, positions = _normalized_with_map(text)
    normalized_quote, _ = _normalized_with_map(quote)
    start = normalized_text.find(normalized_quote)
    if start < 0 or not positions:
        return None, None
    end_index = min(start + len(normalized_quote) - 1, len(positions) - 1)
    return positions[start], positions[end_index] + 1


def _issue(
    *,
    dimension: Dimension,
    severity: Severity,
    title: str,
    explanation: str,
    script_text: str,
    article_text: str,
    script_quote: str | None = None,
    article_quote: str | None = None,
    evidence_not_applicable: bool = False,
    origin: Literal["rule", "judge"] = "rule",
) -> dict:
    script_start, script_end = locate_quote(script_text, script_quote)
    evidence_start, evidence_end = locate_quote(article_text, article_quote)
    if evidence_not_applicable:
        evidence_status = "not_applicable"
    elif article_quote and evidence_start is not None:
        evidence_status = "verified"
    else:
        evidence_status = "insufficient"
    return {
        "id": str(uuid.uuid4()),
        "origin": origin,
        "dimension": dimension,
        "severity": severity,
        "title": title,
        "explanation": explanation,
        "script_quote": script_quote,
        "script_start": script_start,
        "script_end": script_end,
        "article_evidence_quote": article_quote,
        "evidence_start": evidence_start,
        "evidence_end": evidence_end,
        "evidence_status": evidence_status,
    }


def _judge_issue(article: Article, script: ScriptVersion, issue: JudgeIssue) -> dict:
    return _issue(
        dimension=issue.dimension,
        severity=issue.severity,
        title=issue.title,
        explanation=issue.explanation,
        script_text=script.text,
        article_text=article.original_text,
        script_quote=issue.script_quote,
        article_quote=issue.article_evidence_quote,
        evidence_not_applicable=issue.dimension == "audio_readiness",
        origin="judge",
    )
