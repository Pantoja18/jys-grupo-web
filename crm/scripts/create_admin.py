"""Crea un usuario administrador del CRM. Pide los datos por consola; la contraseña no se muestra.

Uso (desde whatsapp-bot/, en PowerShell):
    docker compose run --rm crm-tools python -m scripts.create_admin
"""

import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import User
from app.security import audit
from app.security.passwords import hash_password, validate_password

EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def create_user(session: Session, email: str, full_name: str, password: str, role: str = "admin") -> User:
    email = email.strip().lower()
    if not EMAIL.match(email):
        raise ValueError("El correo no es válido.")
    if not full_name.strip():
        raise ValueError("Indique el nombre completo.")
    validate_password(password)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise ValueError("Ya existe un usuario con ese correo.")
    user = User(email=email, full_name=full_name.strip(), password_hash=hash_password(password), role=role)
    session.add(user)
    session.flush()
    audit.record(session, "create_user", actor="script", entity="user", entity_id=user.id, details={"role": role})
    return user


def main() -> int:
    print("Crear usuario administrador del CRM")
    email = input("Correo (será el usuario): ")
    full_name = input("Nombre completo: ")
    password = getpass.getpass("Contraseña (mínimo 12 caracteres; no se muestra): ")
    if getpass.getpass("Repita la contraseña: ") != password:
        print("Error: las contraseñas no coinciden.", file=sys.stderr)
        return 1
    try:
        with session_scope() as session:
            create_user(session, email, full_name, password, role="admin")
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("Usuario administrador creado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
