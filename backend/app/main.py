import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.articles import router as articles_router
from app.audio import router as audio_router
from app.config import get_settings
from app.database import engine
from app.errors import (
    ApiError,
    api_error_handler,
    error_response,
    validation_error_handler,
)
from app.jobs import router as jobs_router
from app.logging_config import configure_logging, request_id_context
from app.metrics import router as metrics_router
from app.readiness import router as readiness_router
from app.reviews import router as reviews_router

configure_logging()

app = FastAPI(title="EchoFlow API", version="0.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origin.split(",")],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID", "Idempotency-Key", "Range"],
    expose_headers=[
        "Content-Range",
        "Accept-Ranges",
        "Content-Disposition",
        "X-Request-ID",
    ],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid.uuid4())
    token = request_id_context.set(request.state.request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_context.reset(token)
    logging.getLogger("app.http").info(
        "request_completed",
        extra={
            "request_id": request.state.request_id,
            "status_code": response.status_code,
        },
    )
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    return error_response(
        request, 500, "INTERNAL_ERROR", "An unexpected error occurred"
    )


@app.get("/health")
async def health():
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return JSONResponse(
        {
            "status": "ok",
            "providers": {
                "llm": get_settings().llm_provider,
                "tts": get_settings().tts_provider,
            },
        }
    )


app.include_router(articles_router)
app.include_router(jobs_router)
app.include_router(reviews_router)
app.include_router(audio_router)
app.include_router(metrics_router)
app.include_router(readiness_router)
