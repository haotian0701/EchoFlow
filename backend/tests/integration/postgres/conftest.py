"""Each PostgreSQL test owns a randomly named database, never the source DB."""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import get_session
from app.main import app


@pytest_asyncio.fixture
async def postgres_database():
    if not os.getenv("TEST_POSTGRES_URL"):
        pytest.skip("Set TEST_POSTGRES_URL to enable disposable PostgreSQL tests")
    source = make_url(os.environ["TEST_POSTGRES_URL"])
    database_name = f"echoflow_test_{uuid.uuid4().hex}"
    admin = create_async_engine(source, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        target = source.set(database=database_name)
        engine = create_async_engine(target)
        env = {
            **os.environ,
            "DATABASE_URL": target.render_as_string(hide_password=False),
        }
        backend = Path(__file__).resolve().parents[3]

        async def migrate(revision):
            def command():
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", revision],
                    cwd=backend,
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                )

            await asyncio.to_thread(command)

        yield engine, migrate
    finally:
        if engine:
            await engine.dispose()
        async with admin.connect() as connection:
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
            )
        await admin.dispose()


@pytest_asyncio.fixture
async def postgres_sessions(postgres_database):
    engine, migrate = postgres_database
    await migrate("head")
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def postgres_client(postgres_sessions):
    async def override():
        async with postgres_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override
    try:
        async with AsyncClient(
            transport=ASGITransport(app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
