import uuid

from app.evaluation.evidence import locate_quote
from app.evaluation.rules import run_rules
from app.models.articles import Article
from app.models.scripts import ScriptVersion


def records(script_text: str, article_text: str | None = None):
    article = Article(
        title="Report",
        source="Newsroom",
        original_text=article_text or ("Revenue rose by 12 percent in Berlin. " * 15),
        language="en",
        import_type="manual",
        content_hash="a" * 64,
    )
    script = ScriptVersion(
        article_id=uuid.uuid4(),
        version=1,
        text=script_text,
        source="generated",
        target_language="en",
        generation_config={
            "target_language": "en",
            "length": "short",
            "style": "news",
            "audience": "general",
        },
        provider="mock",
        requested_model="mock/script-v1",
        actual_model="mock/script-v1",
        prompt_version="script-generation-v1",
        estimated_duration_seconds=120,
    )
    return article, script


def test_rules_find_length_format_numbers_and_evidence():
    long_sentence = " ".join(["word"] * 75) + " 99 https://example.com"
    article, script = records(long_sentence)
    issues = run_rules(article, script)
    titles = {issue["title"] for issue in issues}
    assert "Sentence may be difficult to narrate" in titles
    assert "Written formatting may sound unnatural" in titles
    assert "Number is not found verbatim in the source" in titles
    assert any(issue["severity"] == "critical" for issue in issues)
    assert locate_quote("One\n\nTwo", "One Two") == (0, 8)
