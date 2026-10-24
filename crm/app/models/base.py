"""Base declarativa, columnas comunes y el tipo de columna cifrada."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, MetaData, SmallInteger, event, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.types import TypeDecorator

from app.security.crypto import get_keyring

# Nombres de restricciones estables (las migraciones dependen de ellos).
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Encrypted(TypeDecorator):
    """Texto cifrado con AES-256-GCM en la aplicación (bytea en PostgreSQL). `label` es el AAD."""

    impl = LargeBinary
    cache_ok = True

    def __init__(self, label: str):
        super().__init__()
        self.label = label

    def process_bind_param(self, value, dialect):
        return get_keyring().encrypt(value, self.label)

    def process_result_value(self, value, dialect):
        return get_keyring().decrypt(value, self.label)


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KeyVersioned:
    """Versión de la clave con que se cifraron los campos 🔒 de la fila (para encontrar qué rotar)."""

    key_version: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))


@event.listens_for(KeyVersioned, "before_insert", propagate=True)
def _key_version_on_insert(mapper, connection, target):
    target.key_version = get_keyring().active_version


@event.listens_for(KeyVersioned, "before_update", propagate=True)
def _key_version_on_update(mapper, connection, target):
    # Si la fila se cifró con una clave anterior, al actualizarla se vuelven a cifrar TODOS sus campos
    # 🔒 con la clave activa (así key_version siempre describe la fila completa).
    active = get_keyring().active_version
    if target.key_version == active:
        return
    encrypted = [p.key for p in mapper.column_attrs if isinstance(p.columns[0].type, Encrypted)]
    if all(k in target.__dict__ for k in encrypted):
        for k in encrypted:
            flag_modified(target, k)
        target.key_version = active


def check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"
