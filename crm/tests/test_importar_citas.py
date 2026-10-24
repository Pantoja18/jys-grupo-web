"""Importación de whatsapp-bot/datos/citas.jsonl.enc (formato de n8n) y create_admin."""

import base64
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Appointment, AuditLog, Consent, Contact, ImportedCita, Lead, User
from app.security.crypto import get_keyring
from app.security.passwords import verify_password
from app.services.citas_import import CitasFileError, import_citas, parse_citas_key

CITAS_KEY_HEX = secrets.token_hex(32)
CITAS_KEY = bytes.fromhex(CITAS_KEY_HEX)
REPO = Path(__file__).resolve().parents[2]


def n8n_line(record: dict, key: bytes = CITAS_KEY) -> str:
    """Igual que el nodo "Guardar cita cifrada" de n8n."""
    iv = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(record, ensure_ascii=False).encode(), b"jys-citas-v1")
    return "v1." + base64.b64encode(iv + ct).decode()


def record(phone="51900000000", registrado="2026-10-24T15:00:00.000Z", consent="otorgado", **cita):
    base = {"nombre": "Ana Torres Vega", "area": "Procesos de alimentos", "resumen": "Pensión de alimentos para su hija",
            "tipo_cliente": "persona_natural", "modalidad": "virtual", "preferencia_horaria": "lunes por la tarde",
            "correo": "ana@example.com", "urgente": False, "contacto_humano": False, "estado": "completa"}
    return {"registrado": registrado, "telefono": phone, "linea": "100", "origen": "flujo",
            "consentimiento": {"estado": consent, "version": "2026-10", "fecha": "2026-10-24T14:55:00.000Z"},
            **base, **cita}


def write(path: Path, *lines: str) -> Path:
    path.write_text("".join(line + "\n" for line in lines), encoding="ascii")
    return path


def run(db, path):
    with Session(db.app_engine) as s:
        result = import_citas(s, path, CITAS_KEY, get_keyring())
        s.commit()
    return result


def counts(db):
    with Session(db.app_engine) as s:
        return {m.__tablename__: s.scalar(select(func.count()).select_from(m))
                for m in (Contact, Consent, Lead, Appointment, ImportedCita)}


def test_importa_cifrado_y_no_duplica(db, tmp_path):
    f = write(tmp_path / "citas.jsonl.enc",
              # Dos versiones de la misma solicitud (la de la IA incompleta y luego la completa del flujo).
              n8n_line(record(registrado="2026-10-24T15:00:00.000Z", origen="ia", estado="incompleta",
                              modalidad="por_definir", preferencia_horaria="", correo="")),
              n8n_line(record(registrado="2026-10-24T15:04:00.000Z")),
              # Otra persona.
              n8n_line(record(phone="51911111111", nombre="Luis Rojas", area="Sucesión intestada",
                              modalidad="presencial", urgente=True)),
              # Sin consentimiento otorgado: no se importa.
              n8n_line(record(phone="51922222222", consent="pendiente")),
              # Cifrada con otra clave y basura: inválidas.
              n8n_line(record(phone="51933333333"), key=secrets.token_bytes(32)),
              "texto que no es una cita")

    r = run(db, f)
    assert r.as_dict() == {"nuevas": 2, "actualizadas": 1, "repetidas": 0, "sin_consentimiento": 1, "invalidas": 2}
    first = counts(db)
    assert first == {"contacts": 2, "consents": 2, "leads": 2, "appointments": 2, "imported_citas": 3}

    # Segunda importación del mismo archivo: no duplica nada.
    r2 = run(db, f)
    assert (r2.nuevas, r2.actualizadas, r2.repetidas) == (0, 0, 3)
    assert counts(db) == first

    kr = get_keyring()
    with Session(db.app_engine) as s:
        ana = s.scalar(select(Contact).where(Contact.phone_hash == kr.phone_hash("+51 900 000 000")))
        assert (ana.full_name, ana.email, ana.consent_status) == ("Ana Torres Vega", "ana@example.com", "otorgado")
        appt = s.scalar(select(Appointment).where(Appointment.contact_id == ana.id))
        assert (appt.modality, appt.preferred_slot, appt.status, appt.source) == ("virtual", "lunes por la tarde", "solicitada", "valeria")
        assert (appt.lead.area, appt.lead.summary, appt.lead.stage) == ("Procesos de alimentos", "Pensión de alimentos para su hija", "cita_solicitada")
        consent = s.scalar(select(Consent).where(Consent.contact_id == ana.id))
        assert (consent.policy_version, consent.channel) == ("2026-10", "whatsapp_import")
        assert "Ley N.° 29733" in consent.text_shown
        luis = s.scalar(select(Contact).where(Contact.phone_hash == kr.phone_hash("51911111111")))
        assert s.scalar(select(Lead.urgent).where(Lead.contact_id == luis.id)) is True

    # En la BD no hay datos personales en claro.
    with db.admin_engine.connect() as conn:
        dump = " ".join(str(v) for row in conn.execute(text(
            "SELECT c.phone, c.full_name, c.email, l.summary, a.preferred_slot FROM contacts c "
            "JOIN leads l ON l.contact_id = c.id JOIN appointments a ON a.lead_id = l.id")) for v in row)
        audit_details = conn.execute(text("SELECT details::text FROM audit_log")).scalars().all()
    for secret in ["Ana Torres", "ana@example.com", "900000000", "Pensión"]:
        assert secret not in dump
        assert all(secret not in d for d in audit_details)


def test_una_solicitud_nueva_dias_despues_es_otra_cita(db, tmp_path):
    first = n8n_line(record(registrado="2026-10-01T15:00:00Z"))
    f = write(tmp_path / "c.enc", first)
    run(db, f)
    # n8n agrega líneas al final del archivo: la primera queda igual y se salta.
    write(f, first, n8n_line(record(registrado="2026-10-20T10:00:00Z", area="Tenencia")))
    r = run(db, f)
    assert (r.nuevas, r.actualizadas, r.repetidas) == (1, 0, 1)
    assert counts(db)["appointments"] == 2 and counts(db)["contacts"] == 1


def test_errores_de_clave_y_archivo(tmp_path, db):
    with pytest.raises(CitasFileError):
        parse_citas_key("1234")
    with pytest.raises(CitasFileError):
        run(db, tmp_path / "no-existe.enc")


def test_script_importar_citas(app_env, tmp_path, monkeypatch, capsys):
    from scripts import importar_citas

    f = write(tmp_path / "citas.jsonl.enc", n8n_line(record()))
    monkeypatch.setenv("CITAS_FILE_KEY", CITAS_KEY_HEX)
    assert importar_citas.main(["--archivo", str(f)]) == 0
    assert importar_citas.main(["--archivo", str(f)]) == 0
    out = capsys.readouterr().out
    assert "nuevas: 1" in out and "repetidas: 1" in out
    assert "Ana" not in out and "900000000" not in out
    monkeypatch.setenv("CITAS_FILE_KEY", "corta")
    assert importar_citas.main(["--archivo", str(f)]) == 2


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js no está instalado")
def test_importa_una_linea_escrita_por_el_nodo_de_n8n(db, tmp_path):
    """Compatibilidad real: la línea la escribe el código del flujo (whatsapp-bot/pruebas/nodos_n8n.js)."""
    runner = REPO / "whatsapp-bot" / "pruebas" / "nodos_n8n.js"
    if not runner.exists():
        pytest.skip("No está whatsapp-bot/ en este entorno")
    f = tmp_path / "citas.jsonl.enc"
    cita = {k: v for k, v in record().items() if k not in ("registrado", "telefono", "linea", "origen", "consentimiento")}
    req = {"node": "Guardar cita cifrada",
           "input": {"from": "51944444444", "phoneNumberId": "100", "citaToSave": cita, "consentStatus": "otorgado",
                     "policyVersion": "2026-10", "reply": "ok", "waMessage": {"type": "text", "text": {"body": "ok"}}},
           "env": {"CITAS_FILE_KEY": CITAS_KEY_HEX, "CITAS_FILE_PATH": str(f)},
           "store": {"consents": {"51944444444": {"estado": "otorgado", "version": "2026-10", "fecha": "2026-10-24T12:00:00.000Z"}}}}
    proc = subprocess.run(["node", str(runner)], input=json.dumps(req), capture_output=True, text=True, encoding="utf-8")
    assert json.loads(proc.stdout)["output"][0]["json"]["saved"] is True, proc.stdout + proc.stderr
    r = run(db, f)
    assert r.nuevas == 1 and r.invalidas == 0
    with Session(db.app_engine) as s:
        c = s.scalar(select(Contact).where(Contact.phone_hash == get_keyring().phone_hash("51944444444")))
        assert c.full_name == "Ana Torres Vega"


# ---------- create_admin ----------

def test_create_admin(app_env, monkeypatch, capsys):
    from scripts import create_admin

    answers = iter(["Admin@Example.com", "Administradora de Prueba"])
    passwords = iter(["clave-de-prueba-larga", "clave-de-prueba-larga"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda prompt="": next(passwords))
    assert create_admin.main() == 0
    out = capsys.readouterr().out
    assert "clave-de-prueba-larga" not in out
    with Session(app_env.app_engine) as s:
        user = s.scalar(select(User))
        assert (user.email, user.role, user.is_active) == ("admin@example.com", "admin", True)
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(user.password_hash, "clave-de-prueba-larga")
        assert s.scalar(select(AuditLog.action).where(AuditLog.entity == "user")) == "create_user"
        # Correo repetido o contraseña corta: no se crea.
        with pytest.raises(ValueError, match="Ya existe"):
            create_admin.create_user(s, "admin@example.com", "Otra", "otra-clave-larga")
        with pytest.raises(ValueError, match="12"):
            create_admin.create_user(s, "otra@example.com", "Otra", "corta")


def test_create_admin_contrasenas_distintas(app_env, monkeypatch):
    from scripts import create_admin

    answers = iter(["a@example.com", "A"])
    passwords = iter(["clave-de-prueba-larga", "otra-clave-distinta"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(create_admin.getpass, "getpass", lambda prompt="": next(passwords))
    assert create_admin.main() == 1
