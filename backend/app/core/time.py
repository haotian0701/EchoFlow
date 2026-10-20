from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
