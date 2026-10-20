from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


def error_response(
    request: Request, status: int, code: str, message: str, details: Any = None
):
    return JSONResponse(
        status_code=status,
        content={
            "code": code,
            "message": message,
            "details": details if details is not None else {},
            "request_id": request.state.request_id,
        },
    )


async def api_error_handler(request: Request, exc: ApiError):
    response = error_response(request, exc.status, exc.code, exc.message, exc.details)
    if exc.status == 416 and "size" in exc.details:
        response.headers["Content-Range"] = f"bytes */{exc.details['size']}"
    return response


async def validation_error_handler(request: Request, exc: RequestValidationError):
    issues = [
        {"field": ".".join(map(str, issue["loc"])), "message": issue["msg"]}
        for issue in exc.errors()
    ]
    return error_response(
        request, 422, "VALIDATION_ERROR", "Invalid request", {"issues": issues}
    )
