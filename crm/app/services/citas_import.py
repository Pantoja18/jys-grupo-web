"""Importa las solicitudes de cita que n8n guardó en whatsapp-bot/datos/citas.jsonl.enc.

Formato de cada línea (lo escribe el nodo "Guardar cita cifrada" del flujo de n8n):
    "v1." + base64(iv de 12 bytes | texto cifrado | etiqueta de 16 bytes), AES-256-GCM con CITAS_FILE_KEY,
    AAD "jys-citas-v1"; el JSON trae registrado, telefono, linea, origen, consentimiento{estado,version,fecha}
    y los campos del bloque <cita> (nombre, area, resumen, tipo_cliente, modalidad, preferencia_horaria,
    correo, urgente, contacto_humano, estado).

Reglas:
- Solo se importan líneas con consentimiento otorgado (si no, se cuentan y se descartan).
- Idempotente: cada línea importada se registra en imported_citas (sha256 de la línea cifrada); al volver
  a importar, se salta.
- n8n puede guardar varias versiones de la misma solicitud (p. ej. el bloque <cita> de la IA, luego
  completo). Una línea de un contacto que tiene una cita "solicitada" de Valeria pedida en las 24 h
  anteriores actualiza esa cita en lugar de crear otra.
- Todo dato personal se guarda cifrado (modelos con `Encrypted`); el resultado y los logs solo llevan conteos.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appointment, Consent, Contact, ImportedCita, Lead
from app.models.core import CLIENT_TYPES
from app.security import audit
from app.security.crypto import CryptoError, KeyRing

AAD = b"jys-citas-v1"
MERGE_WINDOW = timedelta(hours=24)
POLICY_URL = "https://construccionesyedificacionesjys.com/terminos.html#privacidad"
# Texto exacto que muestra n8n (whatsapp-bot/n8n_workflow_template.json, "Puerta de consentimiento").
CONSENT_TEXTS = {
    "2026-10": ("Para gestionar su cita necesitamos usar algunos datos personales (nombre, horario preferido y, "
                "opcionalmente, su correo), conforme a la Ley N.° 29733. Puede revisar nuestra política en "
                "construccionesyedificacionesjys.com/terminos.html#privacidad. ¿Nos autoriza?"),
}
UNKNOWN_TEXT = "(texto no disponible: consentimiento importado de datos/citas.jsonl.enc)"


class CitasFileError(Exception):
    """Archivo o clave inválidos (sin datos personales en el mensaje)."""


@dataclass
class ImportResult:
    nuevas: int = 0
    actualizadas: int = 0
    repetidas: int = 0
    sin_consentimiento: int = 0
    invalidas: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


def parse_citas_key(value: str | None) -> bytes:
    value = (value or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise CitasFileError("CITAS_FILE_KEY debe tener 64 caracteres hexadecimales (32 bytes).")
    return bytes.fromhex(value)


def decrypt_line(line: str, key: bytes) -> dict:
    if not line.startswith("v1."):
        raise ValueError("formato desconocido")
    raw = base64.b64decode(line[3:], validate=True)
    if len(raw) < 12 + 16:
        raise ValueError("línea incompleta")
    return json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], AAD).decode("utf-8"))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _text(value, limit: int | None = None) -> str | None:
    value = str(value).strip() if value is not None else ""
    if not value:
        return None
    return value[:limit] if limit else value


def read_lines(path: Path, key: bytes) -> tuple[list[tuple[str, dict]], int]:
    """Devuelve [(hash de la línea, registro)] ordenado por fecha de registro, y cuántas no se pudieron leer."""
    if not path.exists():
        raise CitasFileError(f"No existe el archivo {path}.")
    try:
        content = path.read_text(encoding="ascii", errors="replace")
    except PermissionError:
        # n8n crea el archivo con permisos 600 (dueño: usuario "node", uid 1000). crm-tools debe correr con ese uid.
        raise CitasFileError(
            f"Sin permiso para leer {path}: el archivo es del usuario de n8n (uid 1000, permisos 600). "
            "Use el servicio crm-tools de docker-compose.yml, que corre con ese uid (ver crm/README.md).") from None
    except OSError as e:
        raise CitasFileError(f"No se pudo leer {path} ({type(e).__name__}).") from None
    records, bad = [], 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = decrypt_line(line, key)
            if not isinstance(rec, dict) or not rec.get("telefono"):
                raise ValueError("registro sin teléfono")
        except (ValueError, InvalidTag, json.JSONDecodeError):
            bad += 1
            continue
        records.append((hashlib.sha256(line.encode("ascii")).hexdigest(), rec))
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    records.sort(key=lambda r: _parse_dt(r[1].get("registrado")) or epoch)
    return records, bad


def _upsert_contact(session: Session, keyring: KeyRing, rec: dict) -> Contact:
    phone_hash = keyring.phone_hash(str(rec["telefono"]))
    contact = session.scalar(select(Contact).where(Contact.phone_hash == phone_hash))
    if contact is None:
        contact = Contact(phone_hash=phone_hash, phone=str(rec["telefono"]), source="whatsapp",
                          line="inmobiliario" if rec.get("area") == "Inmobiliario" else "juridico")
        session.add(contact)
    if name := _text(rec.get("nombre"), 200):
        contact.full_name = name
    if email := _text(rec.get("correo"), 254):
        contact.email = email.lower()
    if rec.get("tipo_cliente") in CLIENT_TYPES:
        contact.client_type = rec["tipo_cliente"]
    contact.consent_status = "otorgado"
    requested = _parse_dt(rec.get("registrado"))
    if requested and (contact.last_interaction_at is None or requested > contact.last_interaction_at):
        contact.last_interaction_at = requested
    session.flush()
    return contact


def _ensure_consent(session: Session, contact: Contact, rec: dict) -> None:
    c = rec.get("consentimiento") or {}
    version = _text(c.get("version"), 20) or "sin-version"
    decided_at = _parse_dt(c.get("fecha")) or _parse_dt(rec.get("registrado")) or datetime.now(timezone.utc)
    exists = session.scalar(select(Consent.id).where(
        Consent.contact_id == contact.id, Consent.decision == "otorgado",
        Consent.policy_version == version, Consent.decided_at == decided_at))
    if exists is None:
        session.add(Consent(contact_id=contact.id, decision="otorgado", policy_version=version, policy_url=POLICY_URL,
                            text_shown=CONSENT_TEXTS.get(version, UNKNOWN_TEXT), channel="whatsapp_import",
                            decided_at=decided_at))


def _apply(lead: Lead, appt: Appointment, rec: dict) -> None:
    lead.area = _text(rec.get("area"), 60) or lead.area or "Otro"
    if summary := _text(rec.get("resumen"), 2000):
        lead.summary = summary
    lead.urgent = rec.get("urgente") is True or bool(lead.urgent)
    lead.human_requested = rec.get("contacto_humano") is True or bool(lead.human_requested)
    lead.stage = "cita_solicitada"
    if rec.get("modalidad") in ("presencial", "virtual"):
        appt.modality = rec["modalidad"]
    elif not appt.modality:
        appt.modality = "por_definir"
    if slot := _text(rec.get("preferencia_horaria"), 200):
        appt.preferred_slot = slot


def import_citas(session: Session, path: Path, citas_key: bytes, keyring: KeyRing) -> ImportResult:
    result = ImportResult()
    records, result.invalidas = read_lines(path, citas_key)
    for line_hash, rec in records:
        if session.get(ImportedCita, line_hash) is not None:
            result.repetidas += 1
            continue
        if (rec.get("consentimiento") or {}).get("estado") != "otorgado":
            result.sin_consentimiento += 1
            continue
        try:
            contact = _upsert_contact(session, keyring, rec)
        except CryptoError:  # teléfono no válido (antes de tocar la sesión)
            result.invalidas += 1
            continue
        _ensure_consent(session, contact, rec)
        requested = _parse_dt(rec.get("registrado")) or datetime.now(timezone.utc)

        appt = session.scalar(
            select(Appointment)
            .where(Appointment.contact_id == contact.id, Appointment.source == "valeria",
                   Appointment.status == "solicitada",
                   Appointment.requested_at >= requested - MERGE_WINDOW, Appointment.requested_at <= requested)
            .order_by(Appointment.requested_at.desc()).limit(1))
        if appt is not None:
            _apply(appt.lead, appt, rec)
            result.actualizadas += 1
            action = "update"
        else:
            lead = Lead(contact_id=contact.id, stage="cita_solicitada")
            appt = Appointment(contact_id=contact.id, source="valeria", status="solicitada", requested_at=requested)
            _apply(lead, appt, rec)
            session.add(lead)
            session.flush()
            appt.lead_id = lead.id
            session.add(appt)
            result.nuevas += 1
            action = "create"
        session.flush()
        session.add(ImportedCita(line_hash=line_hash, appointment_id=appt.id))
        audit.record(session, action, actor="script", entity="appointment", entity_id=appt.id,
                     details={"origen": "citas.jsonl.enc", "registro": rec.get("origen") or "desconocido"})
    audit.record(session, "import_citas", actor="script", details=result.as_dict())
    return result
