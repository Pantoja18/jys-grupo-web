"""Contraseñas del equipo con argon2id (argon2-cffi, parámetros recomendados por la librería)."""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12

_hasher = PasswordHasher(type=Type.ID)


def validate_password(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.")
    if password.strip() != password:
        raise ValueError("La contraseña no debe empezar ni terminar con espacios.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
