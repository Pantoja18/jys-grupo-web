"""Registro en audit_log (solo inserción). `details` lleva solo metadatos, nunca datos personales."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(session: Session, action: str, *, actor: str = "user", entity: str | None = None,
           entity_id: str | uuid.UUID | None = None, user_id: uuid.UUID | None = None,
           ip: str | None = None, details: dict | None = None) -> AuditLog:
    entry = AuditLog(actor=actor, action=action, entity=entity,
                     entity_id=str(entity_id) if entity_id is not None else None,
                     user_id=user_id, ip=ip, details=details)
    session.add(entry)
    return entry
