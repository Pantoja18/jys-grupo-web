"""Tablas del núcleo del CRM (Fase 1): roles, users, contacts, consents, leads, appointments,
audit_log e imported_citas. Ver crm/ARQUITECTURA.md §4.1. Campos 🔒 = `Encrypted`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer,
                        String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Encrypted, KeyVersioned, Timestamps, UUIDPk, check_in

ROLES = ("admin", "abogado", "asistente")
CLIENT_TYPES = ("persona_natural", "empresa")
CONTACT_SOURCES = ("whatsapp", "web", "manual")
LINES = ("juridico", "inmobiliario")
CONTACT_STATUSES = ("prospecto", "cliente", "inactivo")
CONSENT_STATUSES = ("pendiente", "otorgado", "rechazado", "revocado")
CONSENT_DECISIONS = ("otorgado", "rechazado", "revocado")
# whatsapp_import: consentimiento traído de datos/citas.jsonl.enc (el registro de n8n no guarda el canal).
CONSENT_CHANNELS = ("whatsapp_buttons", "whatsapp_text", "web_form", "whatsapp_import")
LEAD_STAGES = ("nueva", "contactada", "cita_solicitada", "cita_confirmada", "cliente", "descartada")
MODALITIES = ("presencial", "virtual", "por_definir")
APPOINTMENT_STATUSES = ("solicitada", "confirmada", "reprogramada", "cancelada", "atendida", "no_asistio")
APPOINTMENT_SOURCES = ("valeria", "panel")
AUDIT_ACTORS = ("user", "bot", "script")


class Role(Base):
    """Roles del equipo. Los permisos de cada rol se aplican en el código del panel (Fase 2)."""

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))


class User(UUIDPk, Timestamps, KeyVersioned, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    email: Mapped[str] = mapped_column(String(254), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(ForeignKey("roles.name"))
    totp_secret: Mapped[str | None] = mapped_column(Encrypted("users.totp_secret"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    failed_logins: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Contact(UUIDPk, Timestamps, KeyVersioned, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint(check_in("client_type", CLIENT_TYPES), name="client_type"),
        CheckConstraint(check_in("source", CONTACT_SOURCES), name="source"),
        CheckConstraint(check_in("line", LINES), name="line"),
        CheckConstraint(check_in("status", CONTACT_STATUSES), name="status"),
        CheckConstraint(check_in("consent_status", CONSENT_STATUSES), name="consent_status"),
    )

    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    phone: Mapped[str] = mapped_column(Encrypted("contacts.phone"))
    full_name: Mapped[str | None] = mapped_column(Encrypted("contacts.full_name"))
    email: Mapped[str | None] = mapped_column(Encrypted("contacts.email"))
    wa_profile_name: Mapped[str | None] = mapped_column(Encrypted("contacts.wa_profile_name"))
    client_type: Mapped[str] = mapped_column(String(20), server_default="persona_natural")
    source: Mapped[str] = mapped_column(String(20), server_default="whatsapp")
    line: Mapped[str] = mapped_column(String(20), server_default="juridico")
    status: Mapped[str] = mapped_column(String(20), server_default="prospecto")
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_interaction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Derivado de `consents` (la última decisión vigente); se guarda para filtrar rápido.
    consent_status: Mapped[str] = mapped_column(String(20), server_default="pendiente")

    consents: Mapped[list[Consent]] = relationship(back_populates="contact")
    leads: Mapped[list[Lead]] = relationship(back_populates="contact", passive_deletes=True)


class Consent(UUIDPk, Base):
    """Prueba del consentimiento (Ley N.° 29733). No se modifica: cada decisión es una fila nueva.
    ON DELETE RESTRICT: no se puede borrar un contacto que tenga consentimientos registrados."""

    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint(check_in("decision", CONSENT_DECISIONS), name="decision"),
        CheckConstraint(check_in("channel", CONSENT_CHANNELS), name="channel"),
        UniqueConstraint("contact_id", "decision", "policy_version", "decided_at", name="uq_consents_decision"),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    policy_version: Mapped[str] = mapped_column(String(20))
    policy_url: Mapped[str] = mapped_column(String(300))
    text_shown: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20))
    wa_message_id: Mapped[str | None] = mapped_column(String(128))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    contact: Mapped[Contact] = relationship(back_populates="consents")


class Lead(UUIDPk, Timestamps, KeyVersioned, Base):
    """Consulta (embudo). `project_id → re_projects` se agrega cuando exista esa tabla (Fase 3/4)."""

    __tablename__ = "leads"
    __table_args__ = (CheckConstraint(check_in("stage", LEAD_STAGES), name="stage"),)

    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    area: Mapped[str] = mapped_column(String(60), server_default="Otro")
    summary: Mapped[str | None] = mapped_column(Encrypted("leads.summary"))
    stage: Mapped[str] = mapped_column(String(20), server_default="nueva")
    urgent: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    human_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    closed_reason: Mapped[str | None] = mapped_column(String(200))

    contact: Mapped[Contact] = relationship(back_populates="leads")
    appointments: Mapped[list[Appointment]] = relationship(back_populates="lead", passive_deletes=True)


class Appointment(UUIDPk, Timestamps, KeyVersioned, Base):
    """Solicitud de cita. `scheduled_at` lo fija el equipo; Valeria solo crea solicitudes."""

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(check_in("modality", MODALITIES), name="modality"),
        CheckConstraint(check_in("status", APPOINTMENT_STATUSES), name="status"),
        CheckConstraint(check_in("source", APPOINTMENT_SOURCES), name="source"),
        Index("ix_appointments_contact_requested", "contact_id", "requested_at"),
    )

    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    modality: Mapped[str] = mapped_column(String(20), server_default="por_definir")
    preferred_slot: Mapped[str | None] = mapped_column(String(200))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), server_default="solicitada")
    lawyer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Encrypted("appointments.notes"))
    source: Mapped[str] = mapped_column(String(20), server_default="valeria")

    lead: Mapped[Lead] = relationship(back_populates="appointments")


class AuditLog(Base):
    """Registro de auditoría. SOLO INSERCIÓN: un trigger rechaza UPDATE, DELETE y TRUNCATE (también
    para el dueño de la tabla) y el rol de la aplicación solo tiene SELECT e INSERT.
    `user_id` no tiene ON DELETE: un usuario con auditoría no se borra, se desactiva."""

    __tablename__ = "audit_log"
    __table_args__ = (CheckConstraint(check_in("actor", AUDIT_ACTORS), name="actor"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(10))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    entity: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(INET)
    # Solo metadatos (conteos, campos cambiados): NUNCA datos personales en claro.
    details: Mapped[dict | None] = mapped_column(JSONB)


class ImportedCita(Base):
    """Líneas de whatsapp-bot/datos/citas.jsonl.enc ya importadas (sha256 de la línea cifrada), para
    que importar dos veces no duplique citas."""

    __tablename__ = "imported_citas"

    line_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("appointments.id", ondelete="SET NULL"))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
