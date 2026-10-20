from typing import Literal

Language = Literal["en", "de"]


def trimmed(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


TargetLanguage = Literal["en", "de"]


ScriptLength = Literal["short", "medium", "long"]


ScriptStyle = Literal["news", "conversational", "professional"]


ScriptAudience = Literal["general", "business"]


JobStatus = Literal[
    "queued", "running", "retry_scheduled", "succeeded", "failed", "needs_attention"
]
