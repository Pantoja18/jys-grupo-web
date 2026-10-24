"""Importa whatsapp-bot/datos/citas.jsonl.enc a PostgreSQL (cifrado), sin duplicar si se ejecuta otra vez.

Uso (desde whatsapp-bot/, en PowerShell):
    docker compose run --rm crm-tools python -m scripts.importar_citas
    docker compose run --rm crm-tools python -m scripts.importar_citas --archivo /data/datos/citas.jsonl.enc

Requiere CITAS_FILE_KEY (la misma clave con la que n8n cifra el archivo) y las claves del CRM
(CRM_DATA_KEY_V1, CRM_HMAC_KEY). Solo muestra conteos: nunca imprime datos personales.
"""

import argparse
import os
import sys
from pathlib import Path

from app.db import session_scope
from app.security.crypto import CryptoError, get_keyring
from app.services.citas_import import CitasFileError, import_citas, parse_citas_key

DEFAULT_FILE = "/data/datos/citas.jsonl.enc"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa las solicitudes de cita de n8n al CRM.")
    parser.add_argument("--archivo", default=os.environ.get("CITAS_FILE_PATH", DEFAULT_FILE),
                        help=f"ruta de citas.jsonl.enc (por defecto {DEFAULT_FILE})")
    args = parser.parse_args(argv)
    try:
        key = parse_citas_key(os.environ.get("CITAS_FILE_KEY"))
        keyring = get_keyring()
        with session_scope() as session:
            result = import_citas(session, Path(args.archivo), key, keyring)
    except (CitasFileError, CryptoError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    r = result.as_dict()
    print("Importación terminada: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in r.items()))
    if result.invalidas:
        print("Aviso: hay líneas que no se pudieron leer (¿otra CITAS_FILE_KEY o archivo dañado?).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
