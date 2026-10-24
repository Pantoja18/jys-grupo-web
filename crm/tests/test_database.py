"""Esquema, permisos y datos cifrados en PostgreSQL (necesitan la BD de pruebas)."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.models import AuditLog, Base, Consent, Contact, Role
from app.security import audit
from app.security.crypto import get_keyring, reset_keyring
from tests.conftest import create_database, run_migrations

TRIGGER_MSG = "audit_log solo admite inserciones"


def _fails(engine, sql, match):
    with pytest.raises(DBAPIError) as e:
        with engine.begin() as conn:
            conn.execute(text(sql))
    assert match in str(e.value.orig), str(e.value.orig)


# ---------- audit_log: solo inserción ----------

def test_audit_log_admite_insertar(db):
    with Session(db.app_engine) as s:
        audit.record(s, "login", actor="user", ip="127.0.0.1", details={"ok": True})
        s.commit()
        assert s.scalar(select(AuditLog.action)) == "login"


def test_audit_log_rechaza_update_y_delete_del_rol_de_la_app(db):
    with Session(db.app_engine) as s:
        audit.record(s, "login", actor="user")
        s.commit()
    _fails(db.app_engine, "UPDATE audit_log SET action = 'otro'", "permission denied")
    _fails(db.app_engine, "DELETE FROM audit_log", "permission denied")
    _fails(db.app_engine, "TRUNCATE audit_log", "permission denied")
    # Tampoco puede apagar el trigger (no es dueño de la tabla).
    _fails(db.app_engine, "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete", "must be owner")


def test_audit_log_rechaza_update_y_delete_incluso_del_dueno(db):
    with db.admin_engine.begin() as conn:
        conn.execute(text("INSERT INTO audit_log (actor, action) VALUES ('script', 'prueba')"))
    _fails(db.admin_engine, "UPDATE audit_log SET action = 'otro'", TRIGGER_MSG)
    _fails(db.admin_engine, "DELETE FROM audit_log", TRIGGER_MSG)
    _fails(db.admin_engine, "TRUNCATE audit_log", TRIGGER_MSG)
    with db.admin_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM audit_log")) == 1


# ---------- Datos cifrados ----------

def test_los_campos_personales_se_guardan_cifrados(db):
    kr = get_keyring()
    with Session(db.app_engine) as s:
        c = Contact(phone_hash=kr.phone_hash("51900000000"), phone="51900000000",
                    full_name="Ana Torres Vega", email="ana@example.com")
        s.add(c)
        s.commit()
        cid = c.id
    with db.admin_engine.connect() as conn:
        row = conn.execute(text("SELECT phone, full_name, email, phone_hash, key_version FROM contacts")).one()
    for raw, clear in [(row.phone, "51900000000"), (row.full_name, "Ana Torres"), (row.email, "ana@example")]:
        assert isinstance(raw, bytes) and clear.encode() not in raw
    assert row.phone_hash == kr.phone_hash("+51 900 000 000")
    assert row.key_version == 1
    with Session(db.app_engine) as s:
        found = s.scalar(select(Contact).where(Contact.phone_hash == kr.phone_hash("900000000")))
        assert found.id == cid and found.full_name == "Ana Torres Vega" and found.email == "ana@example.com"


def test_al_actualizar_una_fila_con_clave_vieja_se_vuelve_a_cifrar(db, monkeypatch):
    import secrets
    with Session(db.app_engine) as s:
        s.add(Contact(phone_hash=get_keyring().phone_hash("51900000000"), phone="51900000000", full_name="Ana"))
        s.commit()
    monkeypatch.setenv("CRM_DATA_KEY_V2", secrets.token_bytes(32).hex())
    monkeypatch.setenv("CRM_ACTIVE_KEY_VERSION", "2")
    reset_keyring()
    with Session(db.app_engine) as s:
        c = s.scalar(select(Contact))
        assert c.full_name == "Ana"  # se lee con la clave 1
        c.status = "cliente"
        s.commit()
    with db.admin_engine.connect() as conn:
        row = conn.execute(text("SELECT phone, full_name, key_version FROM contacts")).one()
    assert row.key_version == 2
    assert all(int.from_bytes(bytes(v)[:2], "big") == 2 for v in (row.phone, row.full_name))


def test_no_se_puede_borrar_un_contacto_con_consentimiento(db):
    from datetime import datetime, timezone
    with Session(db.app_engine) as s:
        c = Contact(phone_hash=get_keyring().phone_hash("51900000000"), phone="51900000000")
        s.add(c)
        s.flush()
        s.add(Consent(contact_id=c.id, decision="otorgado", policy_version="2026-10", policy_url="u", text_shown="t",
                      channel="whatsapp_buttons", decided_at=datetime.now(timezone.utc)))
        s.commit()
    with pytest.raises(IntegrityError):
        with db.admin_engine.begin() as conn:
            conn.execute(text("DELETE FROM contacts"))
    # La app no puede modificar ni borrar consentimientos (son la prueba).
    _fails(db.app_engine, "UPDATE consents SET decision = 'rechazado'", "permission denied")
    _fails(db.app_engine, "DELETE FROM consents", "permission denied")


def test_restricciones_de_valores(db):
    with pytest.raises(IntegrityError):
        with Session(db.app_engine) as s:
            s.add(Contact(phone_hash="x" * 64, phone="51900000000", status="desconocido"))
            s.commit()


# ---------- Migración ----------

def test_roles_iniciales(db):
    with Session(db.app_engine) as s:
        assert sorted(s.scalars(select(Role.name))) == ["abogado", "admin", "asistente"]
    _fails(db.app_engine, "INSERT INTO roles VALUES ('otro', 'x')", "permission denied")


def test_la_migracion_coincide_con_los_modelos(pg):
    with pg.admin_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff


def test_downgrade_y_upgrade(pg):
    url = create_database(pg.admin_url, "jys_mig_test")
    run_migrations(url)
    run_migrations(url, "base", downgrade=True)
    eng = create_engine(url)
    with eng.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' "
                                "AND table_name <> 'alembic_version'")) == 0
    eng.dispose()
    run_migrations(url)
