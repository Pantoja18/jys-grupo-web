"""Muestra las solicitudes de cita guardadas por n8n en datos/citas.jsonl.enc (cifradas con AES-256-GCM).

Requisitos (una vez):   python -m pip install cryptography

Uso, desde whatsapp-bot/ en PowerShell:
    $env:CITAS_FILE_KEY = "<la clave de 64 caracteres hex>"
    python herramientas/ver_citas.py              # la última solicitud de cada número
    python herramientas/ver_citas.py --todas      # todas las líneas (cada actualización de la cita)
    python herramientas/ver_citas.py --env-file .env   # toma CITAS_FILE_KEY del .env (no muestra nada más)

Los datos descifrados solo se muestran en pantalla; no se escriben en ningún archivo.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FILE = BOT_DIR / "datos" / "citas.jsonl.enc"
AAD = b"jys-citas-v1"
LIMA = timezone(timedelta(hours=-5))  # Perú no usa horario de verano
COLUMNS = [
    ("registrado", "Registrado (Lima)"), ("telefono", "Teléfono"), ("nombre", "Nombre"), ("area", "Área"),
    ("modalidad", "Modalidad"), ("preferencia_horaria", "Preferencia"), ("correo", "Correo"),
    ("estado", "Estado"), ("urgente", "Urgente"), ("contacto_humano", "Humano"), ("resumen", "Resumen"),
]


class CitasError(Exception):
    pass


def parse_key(key_hex: str) -> bytes:
    key_hex = (key_hex or "").strip()
    try:
        key = bytes.fromhex(key_hex)
    except ValueError:
        key = b""
    if len(key) != 32:
        raise CitasError("CITAS_FILE_KEY debe tener 64 caracteres hexadecimales (32 bytes).")
    return key


def key_from_env_file(path: Path) -> str:
    """Lee solo la línea CITAS_FILE_KEY de un archivo .env."""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "CITAS_FILE_KEY":
            return value.strip().strip('"').strip("'")
    raise CitasError(f"{path} no contiene CITAS_FILE_KEY.")


def decrypt_line(line: str, key: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    line = line.strip()
    if not line.startswith("v1."):
        raise CitasError("formato desconocido")
    raw = base64.b64decode(line[3:])
    iv, data = raw[:12], raw[12:]  # data = texto cifrado | etiqueta de 16 bytes (lo que espera AESGCM)
    return json.loads(AESGCM(key).decrypt(iv, data, AAD).decode("utf-8"))


def read_citas(path: Path, key: bytes) -> tuple[list[dict], int]:
    """Devuelve (citas descifradas, líneas que no se pudieron descifrar)."""
    from cryptography.exceptions import InvalidTag

    citas, bad = [], 0
    for line in path.read_text(encoding="ascii").splitlines():
        if not line.strip():
            continue
        try:
            citas.append(decrypt_line(line, key))
        except (InvalidTag, CitasError, ValueError):
            bad += 1
    return citas, bad


def latest_per_phone(citas: list[dict]) -> list[dict]:
    last = {}
    for c in citas:
        last[c.get("telefono", "")] = c
    return sorted(last.values(), key=lambda c: c.get("registrado", ""))


def cell(value) -> str:
    if isinstance(value, bool):
        return "sí" if value else "no"
    text = str(value if value is not None else "")
    if len(text) >= 19 and text[4] == "-" and "T" in text:  # fecha ISO (UTC) → AAAA-MM-DD HH:MM en Lima
        try:
            text = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(LIMA).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return " ".join(text.split())


def format_table(citas: list[dict], width: int = 0) -> str:
    rows = [[cell(c.get(k, "")) for k, _ in COLUMNS] for c in citas]
    heads = [h for _, h in COLUMNS]
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(heads)]
    # Si no entra en la consola, se recortan primero las columnas largas (resumen, nombre, correo...).
    width = width or shutil.get_terminal_size((160, 20)).columns
    for i in (10, 5, 6, 2, 3):
        excess = sum(widths) + 3 * (len(widths) - 1) - width
        if excess <= 0:
            break
        widths[i] = max(len(heads[i]), 12, widths[i] - excess)

    def fmt(values):
        return " | ".join((v if len(v) <= w else v[: w - 1] + "…").ljust(w) for v, w in zip(values, widths))

    lines = [fmt(heads), "-+-".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Muestra las solicitudes de cita cifradas de Valeria.")
    parser.add_argument("--archivo", type=Path, default=DEFAULT_FILE, help=f"por defecto: {DEFAULT_FILE}")
    parser.add_argument("--todas", action="store_true", help="mostrar todas las líneas, no solo la última de cada número")
    parser.add_argument("--env-file", type=Path, help="leer CITAS_FILE_KEY de este archivo si no está en el entorno")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        import cryptography  # noqa: F401
    except ImportError:
        print("Falta la librería 'cryptography'. Instálela con:  python -m pip install cryptography")
        return 2

    try:
        key_hex = os.environ.get("CITAS_FILE_KEY", "")
        if not key_hex and args.env_file:
            key_hex = key_from_env_file(args.env_file)
        if not key_hex:
            raise CitasError("Defina CITAS_FILE_KEY en el entorno (PowerShell: $env:CITAS_FILE_KEY = \"...\") o use --env-file .env")
        key = parse_key(key_hex)
        if not args.archivo.exists():
            print(f"Todavía no hay citas guardadas ({args.archivo} no existe).")
            return 0
        citas, bad = read_citas(args.archivo, key)
    except CitasError as e:
        print(f"Error: {e}")
        return 2

    shown = citas if args.todas else latest_per_phone(citas)
    if shown:
        print(format_table(shown))
    print(f"\n{len(shown)} solicitud(es) mostrada(s); {len(citas)} línea(s) en el archivo.")
    if bad:
        print(f"ATENCIÓN: {bad} línea(s) no se pudieron descifrar (¿clave distinta o archivo alterado?).")
    return 1 if bad and not citas else 0


if __name__ == "__main__":
    sys.exit(main())
