"""Comprueba en docker-compose.yml que crm-tools puede leer datos/citas.jsonl.enc sin abrir permisos.

n8n (usuario "node", uid 1000) crea el archivo con permisos 600; crm-tools debe correr con ese mismo uid/gid
y montar ./datos en solo lectura. Se lee el YAML como texto para no depender de PyYAML.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def without_comments(path):
    """Contenido del archivo sin las líneas de comentario (los comentarios explican, no configuran)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(l for l in lines if not l.lstrip().startswith("#")) + "\n"


COMPOSE = without_comments(ROOT / "docker-compose.yml")
DOCKERFILE = without_comments(ROOT.parent / "crm" / "Dockerfile")


def service_block(name):
    """Texto del servicio `name` (desde "  name:" hasta el siguiente servicio o sección de primer nivel)."""
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^\S)", COMPOSE, re.S | re.M)
    if not m:
        raise AssertionError(f"No se encontró el servicio {name}")
    return m.group(1)


class ComposeCrmToolsTest(unittest.TestCase):
    def test_crm_tools_corre_con_el_uid_de_n8n(self):
        self.assertRegex(service_block("crm-tools"), r'(?m)^    user: "1000:1000"$')

    def test_n8n_usa_el_usuario_por_defecto_de_su_imagen(self):
        # Si n8n cambiara de usuario, crm-tools dejaría de poder leer el archivo.
        self.assertNotRegex(service_block("n8n"), r"(?m)^    user:")

    def test_datos_montado_en_solo_lectura(self):
        self.assertIn("- ./datos:/data/datos:ro", service_block("crm-tools"))

    def test_crm_api_no_monta_datos_ni_cambia_de_usuario(self):
        api = service_block("crm-api")
        self.assertNotIn("./datos", api)
        self.assertNotRegex(api, r"(?m)^    user:")

    def test_no_se_abren_permisos(self):
        for text in (COMPOSE, DOCKERFILE):
            self.assertNotRegex(text, r"chmod|\b(644|664|666|777)\b")

    def test_imagen_funciona_con_otro_uid(self):
        # Con uid 1000, /app es de solo lectura: HOME en /tmp y pytest sin caché.
        self.assertIn("HOME=/tmp", DOCKERFILE)
        self.assertIn('PYTEST_ADDOPTS="-p no:cacheprovider"', DOCKERFILE)


if __name__ == "__main__":
    unittest.main()
