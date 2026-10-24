"""Conexión a PostgreSQL con el rol de la aplicación (jys_app)."""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(url) -> Engine:
    # hide_parameters: los valores de las consultas (datos cifrados o hashes) no aparecen en errores ni logs.
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().app_database_url())


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Sesión con commit al terminar y rollback si hay un error."""
    session = make_sessionmaker(engine or get_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
