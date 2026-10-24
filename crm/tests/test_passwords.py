"""Contraseñas con argon2id."""

import pytest

from app.security.passwords import hash_password, needs_rehash, validate_password, verify_password


def test_argon2id():
    h = hash_password("una clave larga y segura")
    assert h.startswith("$argon2id$")
    assert "una clave" not in h
    assert verify_password(h, "una clave larga y segura")
    assert not verify_password(h, "otra clave larga y segura")
    assert not verify_password("no-es-un-hash", "x")
    assert hash_password("una clave larga y segura") != h  # sal aleatoria
    assert not needs_rehash(h)


def test_validacion_de_la_contrasena():
    validate_password("doce-caracts")
    for bad in ["corta", " con espacios al inicio", "con espacios al final "]:
        with pytest.raises(ValueError):
            validate_password(bad)
