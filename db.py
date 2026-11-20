from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import Depends
from psycopg import Connection
from psycopg_pool import ConnectionPool

from config import get_settings

_pool: ConnectionPool | None = None


def open_pool() -> ConnectionPool:
    global _pool
    settings = get_settings()
    _pool = ConnectionPool(
        conninfo=settings.database_dsn,
        min_size=1,
        max_size=settings.db_pool_max,
        open=False,
        kwargs={"autocommit": False, "application_name": "dental-hospital-crm"},
    )
    _pool.open(wait=True, timeout=30)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    if _pool is None:
        raise RuntimeError("Database pool is not open.")
    return _pool


def get_db() -> Generator[Connection[Any], None, None]:
    with get_pool().connection() as connection:
        yield connection


@contextmanager
def transaction(connection: Connection[Any], actor_id: str | None = None):
    with connection.transaction():
        if actor_id:
            connection.execute(
                "SELECT set_config('app.user_id', %s, true)",
                (str(actor_id),),
            )
        yield connection


def audit_view(connection: Connection[Any], actor_id: str, entity_type: str, entity_id: str, request_id: str | None = None) -> None:
    with transaction(connection, actor_id):
        connection.execute(
            "SELECT record_audit_view(%s, %s, %s)",
            (entity_type, str(entity_id), request_id),
        )


DbDep = Connection[Any]


def db_dependency(connection: Connection[Any] = Depends(get_db)) -> Connection[Any]:
    return connection
