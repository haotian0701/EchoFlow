import re

PROMPT_VERSION = "script-generation-v1"


WORD_TARGETS = {
    "en": {"short": 300, "medium": 750, "long": 1350},
    "de": {"short": 260, "medium": 650, "long": 1170},
}


WORDS_PER_MINUTE = {"en": 150, "de": 130}


def estimated_duration_seconds(text: str, language: str) -> int:
    words = len(re.findall(r"\S+", text))
    return max(1, round(words / WORDS_PER_MINUTE[language] * 60))
