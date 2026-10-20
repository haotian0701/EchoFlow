import re

from app.core.script_rules import WORD_TARGETS
from app.evaluation.contracts import Severity
from app.evaluation.evidence import _issue
from app.models.articles import Article
from app.models.scripts import ScriptVersion


def _number_tokens(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r"(?<!\w)[+-]?\d[\d.,]*(?:\s?%)?", text):
        raw = match.group(0)
        key = re.sub(r"[\s,]", "", raw).casefold()
        result.setdefault(key, raw)
    return result


def run_rules(article: Article, script: ScriptVersion) -> list[dict]:
    issues: list[dict] = []
    text = script.text
    article_text = article.original_text
    word_count = len(re.findall(r"\S+", text))
    target = WORD_TARGETS[script.target_language][script.generation_config["length"]]
    deviation = abs(word_count - target) / target

    if len(text) < 100 or len(text) > 30000:
        issues.append(
            _issue(
                dimension="audio_readiness",
                severity="critical",
                title="Script length is outside supported limits",
                explanation="The script must contain between 100 and 30,000 characters.",
                script_text=text,
                article_text=article_text,
                evidence_not_applicable=True,
            )
        )
    if deviation > 0.20:
        severity: Severity = "critical" if deviation > 0.40 else "warning"
        issues.append(
            _issue(
                dimension="content_coverage",
                severity=severity,
                title="Script misses the configured length target",
                explanation=(
                    f"The script has {word_count} words; the configured target is about {target}. "
                    "Confirm that the source length justifies the difference."
                ),
                script_text=text,
                article_text=article_text,
                evidence_not_applicable=True,
            )
        )

    for sentence_match in re.finditer(r"[^.!?\n]+(?:[.!?]|$)", text):
        sentence = sentence_match.group(0).strip()
        words = len(re.findall(r"\S+", sentence))
        if words > 45:
            issues.append(
                _issue(
                    dimension="audio_readiness",
                    severity="critical" if words > 70 else "warning",
                    title="Sentence may be difficult to narrate",
                    explanation=f"This sentence contains {words} words; consider splitting it.",
                    script_text=text,
                    article_text=article_text,
                    script_quote=sentence,
                    evidence_not_applicable=True,
                )
            )

    format_match = re.search(
        r"(?:https?://\S+|^\s{0,3}(?:[-*#]|\d+[.)])\s+|`{1,3})", text, re.MULTILINE
    )
    if format_match:
        issues.append(
            _issue(
                dimension="audio_readiness",
                severity="warning",
                title="Written formatting may sound unnatural",
                explanation="Remove URLs, Markdown, or list markers before narration.",
                script_text=text,
                article_text=article_text,
                script_quote=format_match.group(0).strip(),
                evidence_not_applicable=True,
            )
        )

    article_numbers = _number_tokens(article_text)
    script_numbers = _number_tokens(text)
    for key, raw in script_numbers.items():
        if key not in article_numbers:
            issues.append(
                _issue(
                    dimension="factual_consistency",
                    severity="warning",
                    title="Number is not found verbatim in the source",
                    explanation=(
                        "Check whether this number is supported or is a valid language or unit conversion."
                    ),
                    script_text=text,
                    article_text=article_text,
                    script_quote=raw,
                )
            )
    for key, raw in article_numbers.items():
        if key not in script_numbers:
            issues.append(
                _issue(
                    dimension="content_coverage",
                    severity="info",
                    title="Source number is not present in the script",
                    explanation="Review whether this figure is material enough to include.",
                    script_text=text,
                    article_text=article_text,
                    article_quote=raw,
                )
            )
    return issues
