"""Cifrado AES-256-GCM de campos, rotación de clave y phone_hash (HMAC-SHA256). No necesitan BD."""

import base64
import secrets

import pytest

from app.security.crypto import CryptoError, KeyRing, normalize_phone, parse_key

K1, K2, H = (secrets.token_bytes(32) for _ in range(3))


def ring(active=1, keys=None, hmac_key=H):
    return KeyRing(keys or {1: K1}, active, hmac_key)


def test_cifrar_y_descifrar():
    r = ring()
    blob = r.encrypt("María Pérez Ríos", "contacts.full_name")
    assert isinstance(blob, bytes)
    assert "María".encode() not in blob and b"Mar" not in blob
    assert r.decrypt(blob, "contacts.full_name") == "María Pérez Ríos"
    assert r.encrypt(None, "contacts.email") is None and r.decrypt(None, "contacts.email") is None
    assert r.decrypt(r.encrypt("", "x"), "x") == ""


def test_nonce_distinto_en_cada_cifrado():
    r = ring()
    a, b = r.encrypt("51900000000", "contacts.phone"), r.encrypt("51900000000", "contacts.phone")
    assert a != b and a[2:14] != b[2:14]


def test_el_valor_queda_ligado_a_su_columna_y_no_admite_cambios():
    r = ring()
    blob = r.encrypt("ana@example.com", "contacts.email")
    with pytest.raises(CryptoError):
        r.decrypt(blob, "contacts.full_name")
    tampered = bytearray(blob)
    tampered[-1] ^= 1
    with pytest.raises(CryptoError):
        r.decrypt(bytes(tampered), "contacts.email")
    with pytest.raises(CryptoError):
        ring(keys={1: K2}).decrypt(blob, "contacts.email")


def test_rotacion_de_clave():
    old = ring()
    blob_v1 = old.encrypt("dato", "leads.summary")
    assert KeyRing.version_of(blob_v1) == 1
    new = ring(active=2, keys={1: K1, 2: K2})
    assert new.decrypt(blob_v1, "leads.summary") == "dato"  # lo viejo se sigue leyendo
    blob_v2 = new.reencrypt(blob_v1, "leads.summary")
    assert KeyRing.version_of(blob_v2) == 2
    assert new.decrypt(blob_v2, "leads.summary") == "dato"
    assert new.reencrypt(blob_v2, "leads.summary") is blob_v2
    # Sin la clave 1 ya no se pueden leer los valores viejos.
    with pytest.raises(CryptoError, match="CRM_DATA_KEY_V1"):
        ring(active=2, keys={2: K2}).decrypt(blob_v1, "leads.summary")
    # La versión forma parte del AAD: cambiar la cabecera no sirve para engañar.
    forged = (2).to_bytes(2, "big") + blob_v1[2:]
    with pytest.raises(CryptoError):
        new.decrypt(forged, "leads.summary")


def test_parse_key_base64_hex_y_errores_sin_mostrar_la_clave():
    raw = secrets.token_bytes(32)
    assert parse_key(base64.b64encode(raw).decode(), "X") == raw
    assert parse_key(base64.urlsafe_b64encode(raw).decode().rstrip("="), "X") == raw
    assert parse_key(raw.hex(), "X") == raw
    for bad in ["", "corta", base64.b64encode(b"x" * 16).decode()]:
        with pytest.raises(CryptoError) as e:
            parse_key(bad, "CRM_DATA_KEY_V1")
        assert "CRM_DATA_KEY_V1" in str(e.value)
        assert not bad or bad not in str(e.value)


def test_from_env():
    env = {"CRM_DATA_KEY_V1": base64.b64encode(K1).decode(), "CRM_DATA_KEY_V2": K2.hex(),
           "CRM_HMAC_KEY": base64.b64encode(H).decode(), "CRM_ACTIVE_KEY_VERSION": "2"}
    r = KeyRing.from_env(env)
    assert r.active_version == 2
    assert KeyRing.version_of(r.encrypt("x", "t")) == 2
    with pytest.raises(CryptoError, match="CRM_DATA_KEY_V3"):
        KeyRing.from_env({**env, "CRM_ACTIVE_KEY_VERSION": "3"})
    with pytest.raises(CryptoError, match="CRM_HMAC_KEY"):
        KeyRing.from_env({"CRM_DATA_KEY_V1": K1.hex()})
    with pytest.raises(CryptoError, match="CRM_DATA_KEY_V1"):
        KeyRing.from_env({"CRM_HMAC_KEY": H.hex()})


def test_phone_hash_estable_normalizado_y_secreto():
    r = ring()
    h = r.phone_hash("51900000000")
    assert len(h) == 64 and int(h, 16) >= 0
    assert "900000000" not in h
    for same in ["+51 900 000 000", "51-900-000-000", "900000000", " 51900000000 "]:
        assert r.phone_hash(same) == h, same
    assert ring().phone_hash("51900000000") == h  # estable entre instancias con la misma clave
    assert r.phone_hash("51900000001") != h
    assert ring(hmac_key=K2).phone_hash("51900000000") != h  # depende de la clave
    assert normalize_phone("+1 (555) 010-9999") == "15550109999"
    with pytest.raises(CryptoError):
        normalize_phone("123")
