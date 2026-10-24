"""Pruebas de los nodos del flujo de n8n desde Python (unittest).

Ejecutan el código real de n8n_workflow_template.json con Node (pruebas/nodos_n8n.js), de modo que
`python -m unittest discover -s pruebas` cubre también la puerta de consentimiento. Si Node no está
instalado, se omiten (las mismas pruebas existen en test_flujo.js, que puede correrse con Docker).
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "nodos_n8n.js"
NODE = shutil.which("node")
FROM = "51900000000"
POLICY = {"POLICY_VERSION": "2026-10"}
CONSENT_TEXT = ("Para gestionar su cita necesitamos usar algunos datos personales (nombre, horario preferido y, "
                "opcionalmente, su correo), conforme a la Ley N.° 29733. Puede revisar nuestra política en "
                "construccionesyedificacionesjys.com/terminos.html#privacidad. ¿Nos autoriza?")
CITA = {"nombre": "María Pérez Ríos", "area": "Procesos de alimentos", "resumen": "Pensión", "tipo_cliente": "persona_natural",
        "modalidad": "presencial", "preferencia_horaria": "martes por la mañana", "correo": "", "urgente": False,
        "contacto_humano": False, "estado": "completa"}


def run_node(node, input, env=None, store=None, refs=None):
    """Ejecuta un nodo Code y devuelve (json del primer ítem o None, store actualizado)."""
    req = {"node": node, "input": input, "env": {**POLICY, **(env or {})}, "store": store or {}, "refs": refs or {}}
    proc = subprocess.run([NODE, str(RUNNER)], input=json.dumps(req), capture_output=True, text=True, encoding="utf-8")
    data = json.loads(proc.stdout or "{}")
    if "error" in data or proc.returncode:
        raise AssertionError(f"{node}: {data.get('error') or proc.stderr}")
    out = data["output"]
    return (out[0]["json"] if out else None), data["store"]


def message(text, button_id="", **extra):
    return {"from": FROM, "messageId": "wamid.T", "contactName": "Prueba", "phoneNumberId": "100", "text": text,
            "buttonId": button_id, "needsAI": True, "directReply": "", **extra}


def gate(text, store=None, button_id="", env=None, **extra):
    return run_node("Puerta de consentimiento", message(text, button_id, **extra), env, store)


def llm_turn(text, llm_content, store, env=None):
    """Puerta → Preparar → Procesar con una respuesta simulada de Ollama."""
    g, store = gate(text, store, env=env)
    assert g["action"] == "llm", g
    ctx, store = run_node("Preparar solicitud LLM", {}, env, store, {"Puerta de consentimiento": g})
    out, store = run_node("Procesar respuesta", {"message": {"content": llm_content}}, env, store,
                          {"Preparar solicitud LLM": ctx})
    return out, store, ctx


def granted():
    return {"consents": {FROM: {"estado": "otorgado", "fecha": "2026-10-24T12:00:00.000Z", "version": "2026-10",
                                "canal": "whatsapp_botones"}}}


@unittest.skipUnless(NODE, "Node.js no está instalado")
class TestPuertaConsentimiento(unittest.TestCase):
    def test_cita_sin_consentimiento_envia_botones_sin_ia(self):
        out, store = gate("Quiero agendar una cita")
        self.assertEqual(out["action"], "reply")
        self.assertEqual(out["waMessage"]["type"], "interactive")
        self.assertEqual(out["waMessage"]["interactive"]["body"]["text"], CONSENT_TEXT)
        ids = [b["reply"]["id"] for b in out["waMessage"]["interactive"]["action"]["buttons"]]
        self.assertEqual(ids, ["consent_si", "consent_no"])
        self.assertTrue(store["consents"][FROM]["prompted"])

    def test_boton_acepto_registra_otorgado(self):
        _, store = gate("Quiero una cita")
        out, store = gate("Acepto", store, button_id="consent_si")
        self.assertEqual(out["action"], "reply")
        consent = store["consents"][FROM]
        self.assertEqual((consent["estado"], consent["version"], consent["canal"]), ("otorgado", "2026-10", "whatsapp_botones"))
        self.assertTrue(consent["fecha"])
        self.assertEqual(gate("Quiero una cita", store)[0]["action"], "llm")

    def test_acepto_por_texto(self):
        out, store = gate("Sí, acepto")
        self.assertEqual(store["consents"][FROM]["estado"], "otorgado")
        self.assertEqual(store["consents"][FROM]["canal"], "whatsapp_texto")

    def test_rechazo_no_pide_datos_y_da_canales(self):
        _, store = gate("Quiero una cita")
        out, store = gate("No acepto", store, button_id="consent_no")
        self.assertEqual(store["consents"][FROM]["estado"], "rechazado")
        self.assertIn("956 771 681", out["reply"])
        self.assertNotIn("nombre", out["reply"].lower())

    def test_filtro_de_salida_reemplaza_pedido_de_datos(self):
        out, store, ctx = llm_turn("¿Qué servicios tienen?", "Con gusto. ¿Me indica su nombre completo y su DNI?", {})
        self.assertTrue(ctx["llmBody"]["messages"][0]["content"].rstrip().endswith("Consentimiento: pendiente"))
        self.assertTrue(out["blockedByConsent"])
        self.assertEqual(out["waMessage"]["type"], "interactive")
        self.assertEqual(out["reply"], CONSENT_TEXT)

    def test_filtro_de_salida_respeta_consentimiento_otorgado(self):
        out, _, _ = llm_turn("Quiero una cita", "Gracias. ¿Me indica su nombre completo, por favor?", granted())
        self.assertFalse(out["blockedByConsent"])
        self.assertEqual(out["waMessage"]["text"]["body"], "Gracias. ¿Me indica su nombre completo, por favor?")

    def test_cita_sin_consentimiento_no_se_registra(self):
        out, _, _ = llm_turn("Hola", "Gracias.\n<cita>" + json.dumps(CITA, ensure_ascii=False) + "</cita>", {})
        self.assertFalse(out["hasAppointment"])
        self.assertIsNone(out["appointment"])


try:
    import cryptography  # noqa: F401
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@unittest.skipUnless(NODE and HAS_CRYPTO, "requiere Node.js y 'python -m pip install cryptography'")
class TestCitasCifradas(unittest.TestCase):
    """n8n cifra la cita (nodo Guardar cita cifrada) y herramientas/ver_citas.py la descifra."""

    KEY = "0f" * 32

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(HERE.parent / "herramientas"))
        import ver_citas
        cls.vc = ver_citas

    def save(self, cita, file, store=None, key=None):
        inp = {"from": FROM, "phoneNumberId": "100", "appointment": cita, "consentStatus": "otorgado", "policyVersion": "2026-10"}
        return run_node("Guardar cita cifrada", inp, {"CITAS_FILE_KEY": key or self.KEY, "CITAS_FILE_PATH": str(file)},
                        store if store is not None else granted())

    def test_cifrar_en_n8n_y_descifrar_con_ver_citas(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "datos" / "citas.jsonl.enc"
            out, store = self.save(CITA, file)
            self.assertTrue(out["saved"])
            out, store = self.save({**CITA, "correo": "maria@example.com"}, file, store)
            self.assertTrue(out["saved"])
            self.assertNotIn("María", file.read_text(encoding="ascii"))

            citas, bad = self.vc.read_citas(file, self.vc.parse_key(self.KEY))
            self.assertEqual(bad, 0)
            self.assertEqual(len(citas), 2)
            self.assertEqual(citas[0]["nombre"], "María Pérez Ríos")
            self.assertEqual(citas[0]["consentimiento"]["version"], "2026-10")
            latest = self.vc.latest_per_phone(citas)
            self.assertEqual(len(latest), 1)
            self.assertEqual(latest[0]["correo"], "maria@example.com")
            table = self.vc.format_table(latest, width=400)
            self.assertIn("María Pérez Ríos", table)
            self.assertIn("Procesos de alimentos", table)

            # Con otra clave no se descifra nada.
            citas, bad = self.vc.read_citas(file, self.vc.parse_key("aa" * 32))
            self.assertEqual((len(citas), bad), (0, 2))

    def test_ver_citas_main_con_clave_del_entorno(self):
        import io
        import os
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory
        from unittest import mock
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "citas.jsonl.enc"
            self.save(CITA, file)
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CITAS_FILE_KEY": self.KEY}), redirect_stdout(buf):
                code = self.vc.main(["--archivo", str(file)])
            self.assertEqual(code, 0)
            self.assertIn("María Pérez Ríos", buf.getvalue())
            self.assertIn("1 solicitud(es)", buf.getvalue())
            buf = io.StringIO()
            with mock.patch.dict(os.environ, {"CITAS_FILE_KEY": "corta"}), redirect_stdout(buf):
                self.assertEqual(self.vc.main(["--archivo", str(file)]), 2)
            self.assertIn("64 caracteres", buf.getvalue())

    def test_env_file_solo_lee_la_clave(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("META_ACCESS_TOKEN=secreto\nCITAS_FILE_KEY=" + self.KEY + "\n", encoding="utf-8")
            self.assertEqual(self.vc.key_from_env_file(env), self.KEY)

    def test_sin_clave_valida_no_se_guarda(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "citas.jsonl.enc"
            out, _ = self.save(CITA, file, key="no-hex")
            self.assertFalse(out["saved"])
            self.assertFalse(file.exists())


if __name__ == "__main__":
    unittest.main()
