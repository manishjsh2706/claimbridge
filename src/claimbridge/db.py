"""
Database session management - ClaimBridge
=========================================

One engine per process, created lazily on first use, so importing the app does
not require a reachable database (tests and scripts that never touch Postgres
stay independent of it).

    from src.claimbridge.db import session_scope
    with session_scope() as session:
        ...                               # commits on success, rolls back on error

    # FastAPI
    def endpoint(session: Session = Depends(get_session)): ...

READ / WRITE SPLIT (replica-ready)
    with read_session_scope() as session:  # read-only queries that tolerate lag
        ...
`DATABASE_READ_URL` points at a read replica. When it is not set (today, one
Postgres) reads use the primary, so the code path is exercised now and a
replica is a configuration change, not a code change.

Which reads may go to the replica: lists and reports where a second of
replication lag is harmless (review queue, audit trail). Anything read right
after a write in the same flow -- approve then publish, submit then read the
claim -- stays on the primary ("read-your-writes").
"""

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None
_read_engine: Optional[Engine] = None
_ReadSessionLocal: Optional[sessionmaker] = None


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        # pool_pre_ping: a connection dropped by Postgres (restart, idle
        # timeout) is detected and replaced instead of failing the request.
        _engine = create_engine(database_url(), pool_pre_ping=True, pool_size=5, max_overflow=10)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _read_sessionmaker() -> sessionmaker:
    global _read_engine, _ReadSessionLocal
    if _ReadSessionLocal is None:
        url = os.getenv("DATABASE_READ_URL")
        if not url:
            get_engine()
            _ReadSessionLocal = _SessionLocal
        else:
            _read_engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)
            _ReadSessionLocal = sessionmaker(bind=_read_engine, expire_on_commit=False)
    return _ReadSessionLocal


@contextmanager
def read_session_scope() -> Iterator[Session]:
    """Read-only session: replica when DATABASE_READ_URL is set, else primary. Never commits."""
    session = _read_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def uses_read_replica() -> bool:
    return bool(os.getenv("DATABASE_READ_URL"))


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed on success."""
    with session_scope() as session:
        yield session


def is_ready() -> bool:
    """True if the database answers a trivial query. Used by /health."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def dispose_engine() -> None:
    global _engine, _SessionLocal, _read_engine, _ReadSessionLocal
    for e in (_engine, _read_engine):
        if e is not None:
            e.dispose()
    _engine, _SessionLocal, _read_engine, _ReadSessionLocal = None, None, None, None
