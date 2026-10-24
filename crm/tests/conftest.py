"""Fixtures de pytest.

Las pruebas con base de datos usan una BD propia cuyo nombre termina en "_test" (se borra y se vuelve a
crear en cada ejecución) y un rol de aplicación de prueba (jys_test_app) con los mismos permisos que
jys_app. La URL del servidor sale de:
  - TEST_DATABASE_URL (p. ej. postgresql+psycopg://usuario:clave@127.0.0.1:5432/jys_test), o
  - la conexión de migraciones (CRM_DB_ADMIN_* en el servicio crm-tools), cambiando la BD a "jys_test".
Sin ninguna de las dos, esas pruebas se omiten. Nunca se usa la BD real "jys".
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.security.crypto import reset_keyring

ROOT = Path(__file__).resolve().parent.parent
APP_ROLE = "jys_test_app"
TABLES = ["imported_citas", "appointments", "leads", "consents", "contacts", "audit_log", "users"]

# Claves de prueba, aleatorias en cada ejecución (nunca las del .env).
TEST_KEYS = {
    "CRM_DATA_KEY_V1": base64.b64encode(secrets.token_bytes(32)).decode(),
    "CRM_HMAC_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    "CRM_ACTIVE_KEY_VERSION": "1",
}


@pytest.fixture(autouse=True)
def crm_keys(monkeypatch):
    for name in list(os.environ):
        if name.startswith("CRM_DATA_KEY_V"):
            monkeypatch.delenv(name)
    for name, value in TEST_KEYS.items():
        monkeypatch.setenv(name, value)
    reset_keyring()
    yield
    reset_keyring()


def _admin_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        url = make_url(url)
    else:
        try:
            url = get_settings().migrations_url().set(database="jys_test")
        except RuntimeError:
            return None
    if not (url.database or "").endswith("_test"):
        raise pytest.UsageError("La BD de pruebas debe terminar en '_test' (se borra en cada ejecución).")
    return url


def create_database(admin_url, name: str):
    """Crea (o recrea) una BD de pruebas vacía y devuelve su URL de dueño."""
    assert name.endswith("_test")
    maint = create_engine(admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with maint.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()
    return admin_url.set(database=name)


def run_migrations(url, revision: str = "head", downgrade: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["url"] = url
    os.environ["CRM_DB_APP_ROLE"] = APP_ROLE
    try:
        (command.downgrade if downgrade else command.upgrade)(cfg, revision)
    finally:
        os.environ.pop("CRM_DB_APP_ROLE", None)


@pytest.fixture(scope="session")
def pg():
    admin_url = _admin_url()
    if admin_url is None:
        pytest.skip("Sin PostgreSQL de pruebas: defina TEST_DATABASE_URL o ejecute en el servicio crm-tools.")
    password = secrets.token_hex(16)
    maint = create_engine(admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with maint.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE})
        conn.execute(text(f"{'ALTER' if exists else 'CREATE'} ROLE {APP_ROLE} LOGIN PASSWORD '{password}'"))
    maint.dispose()
    admin_url = create_database(admin_url, admin_url.database)
    run_migrations(admin_url)
    app_url = admin_url.set(username=APP_ROLE, password=password)
    ns = SimpleNamespace(
        admin_url=admin_url,
        app_url=app_url,
        admin_engine=create_engine(admin_url, hide_parameters=True),
        app_engine=create_engine(app_url, hide_parameters=True),
    )
    yield ns
    ns.admin_engine.dispose()
    ns.app_engine.dispose()


@pytest.fixture
def db(pg):
    """BD vacía para cada prueba (como dueño y superusuario se saltan los triggers solo para limpiar)."""
    with pg.admin_engine.begin() as conn:
        conn.execute(text("SET LOCAL session_replication_role = replica"))
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))
    return pg


@pytest.fixture
def app_env(db, monkeypatch):
    """Configura app.db para que los scripts usen la BD de pruebas con el rol de la aplicación."""
    from app import db as app_db

    monkeypatch.setenv("DATABASE_URL", db.app_url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    app_db.get_engine.cache_clear()
    yield db
    app_db.get_engine().dispose()
    get_settings.cache_clear()
    app_db.get_engine.cache_clear()
