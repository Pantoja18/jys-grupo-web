"""Cifrado de campos personales (AES-256-GCM) y hash de teléfonos (HMAC-SHA256).

Formato de cada valor cifrado (columna bytea):

    versión de clave (2 bytes, big-endian) | nonce (12 bytes) | texto cifrado + etiqueta GCM (16 bytes)

- La clave de cada versión viene de `CRM_DATA_KEY_V<n>` (32 bytes, en base64 o 64 caracteres hex);
  se cifra siempre con `CRM_ACTIVE_KEY_VERSION`. Para rotar: agregar `CRM_DATA_KEY_V2`, poner
  `CRM_ACTIVE_KEY_VERSION=2` y volver a cifrar con `KeyRing.reencrypt()` (las versiones viejas se
  siguen leyendo mientras su clave esté configurada).
- El AAD liga cada valor a su columna ("contacts.phone") y a la versión: un valor copiado a otra
  columna, o con la versión alterada, no se descifra.
- `phone_hash` = HMAC-SHA256(`CRM_HMAC_KEY`, teléfono normalizado), en hex: permite buscar por teléfono
  sin descifrar. Si se cambia `CRM_HMAC_KEY` hay que recalcular todos los hashes.

Los mensajes de error nunca incluyen claves ni datos.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import secrets
import struct
from collections.abc import Mapping
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
HEADER = struct.Struct(">H")
KEY_ENV = re.compile(r"^CRM_DATA_KEY_V(\d+)$")


class CryptoError(Exception):
    """Error de configuración o de descifrado (sin datos sensibles en el mensaje)."""


def parse_key(value: str | None, name: str) -> bytes:
    """Clave de 32 bytes en base64 (estándar o url-safe) o en 64 caracteres hex."""
    value = (value or "").strip()
    if not value:
        raise CryptoError(f"{name} no está configurada.")
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return bytes.fromhex(value)
    try:
        raw = base64.b64decode(value.replace("-", "+").replace("_", "/") + "=" * (-len(value) % 4), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 32:
        raise CryptoError(f"{name} debe tener 32 bytes (en base64 o 64 caracteres hex).")
    return raw


def normalize_phone(phone: str) -> str:
    """Solo dígitos, con código de país. Un celular peruano de 9 dígitos (9XXXXXXXX) recibe el 51."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 9 and digits.startswith("9"):
        digits = "51" + digits
    if len(digits) < 8:
        raise CryptoError("Número de teléfono no válido.")
    return digits


class KeyRing:
    def __init__(self, keys: Mapping[int, bytes], active_version: int, hmac_key: bytes):
        if active_version not in keys:
            raise CryptoError(f"CRM_ACTIVE_KEY_VERSION={active_version}, pero no existe CRM_DATA_KEY_V{active_version}.")
        if not 1 <= active_version <= 32767:
            raise CryptoError("CRM_ACTIVE_KEY_VERSION debe estar entre 1 y 32767.")
        self._keys = {v: AESGCM(k) for v, k in keys.items()}
        self.active_version = active_version
        self._hmac_key = hmac_key

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> KeyRing:
        env = os.environ if env is None else env
        keys = {int(m.group(1)): parse_key(v, name) for name, v in env.items() if (m := KEY_ENV.match(name)) and v.strip()}
        if not keys:
            raise CryptoError("CRM_DATA_KEY_V1 no está configurada.")
        try:
            active = int(env.get("CRM_ACTIVE_KEY_VERSION") or max(keys))
        except ValueError as e:
            raise CryptoError("CRM_ACTIVE_KEY_VERSION debe ser un número.") from e
        return cls(keys, active, parse_key(env.get("CRM_HMAC_KEY"), "CRM_HMAC_KEY"))

    @staticmethod
    def _aad(label: str, version: int) -> bytes:
        return f"jys-crm|{label}|v{version}".encode()

    def encrypt(self, plaintext: str | None, label: str) -> bytes | None:
        if plaintext is None:
            return None
        nonce = secrets.token_bytes(NONCE_BYTES)
        v = self.active_version
        ct = self._keys[v].encrypt(nonce, plaintext.encode("utf-8"), self._aad(label, v))
        return HEADER.pack(v) + nonce + ct

    @staticmethod
    def version_of(blob: bytes) -> int:
        if len(blob) < HEADER.size + NONCE_BYTES + 16:
            raise CryptoError("Valor cifrado incompleto.")
        return HEADER.unpack_from(blob)[0]

    def decrypt(self, blob: bytes | None, label: str) -> str | None:
        if blob is None:
            return None
        blob = bytes(blob)
        v = self.version_of(blob)
        if v not in self._keys:
            raise CryptoError(f"Falta CRM_DATA_KEY_V{v} para descifrar un valor de {label}.")
        nonce = blob[HEADER.size:HEADER.size + NONCE_BYTES]
        try:
            return self._keys[v].decrypt(nonce, blob[HEADER.size + NONCE_BYTES:], self._aad(label, v)).decode("utf-8")
        except InvalidTag as e:
            raise CryptoError(f"No se pudo descifrar un valor de {label} (clave incorrecta o dato alterado).") from e

    def reencrypt(self, blob: bytes | None, label: str) -> bytes | None:
        """Rotación: descifra con la versión con que se guardó y vuelve a cifrar con la activa."""
        if blob is None or self.version_of(bytes(blob)) == self.active_version:
            return blob
        return self.encrypt(self.decrypt(blob, label), label)

    def phone_hash(self, phone: str) -> str:
        return hmac.new(self._hmac_key, normalize_phone(phone).encode(), hashlib.sha256).hexdigest()


@lru_cache
def get_keyring() -> KeyRing:
    return KeyRing.from_env()


def reset_keyring() -> None:
    """Vuelve a leer las claves del entorno (pruebas o tras rotar)."""
    get_keyring.cache_clear()
