from typing import Any


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
