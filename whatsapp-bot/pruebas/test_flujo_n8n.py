"""Pruebas de los nodos del flujo de n8n desde Python (unittest).

Ejecutan el código real de n8n_workflow_template.json con Node (pruebas/nodos_n8n.js), de modo que
`python -m unittest discover -s pruebas` cubre también la puerta de consentimiento y el estado de la cita. Si Node no está
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


def eval_param(node, key, json_data, env=None):
    """Evalúa una expresión {{ ... }} de un parámetro de nodo, como lo haría n8n."""
    req = {"param": [node, key], "json": json_data, "env": env or {}}
    proc = subprocess.run([NODE, str(RUNNER)], input=json.dumps(req), capture_output=True, text=True, encoding="utf-8")
    data = json.loads(proc.stdout or "{}")
    if "error" in data or proc.returncode:
        raise AssertionError(data.get("error") or proc.stderr)
    return data["value"]


def message(text, button_id="", **extra):
    return {"from": FROM, "messageId": "wamid.T", "contactName": "Prueba", "phoneNumberId": "100", "text": text,
            "buttonId": button_id, "needsAI": True, "directReply": "", **extra}


def gate(text, store=None, button_id="", env=None, **extra):
    return run_node("Puerta de consentimiento", message(text, button_id, **extra), env, store)


def route(text, store=None, button_id="", env=None, **extra):
    """Puerta de consentimiento → Estado de la cita."""
    g, store = gate(text, store, button_id, env, **extra)
    return run_node("Estado de la cita", g, env, store)


def finish(json_data, store, env=None):
    """Guardar cita cifrada: último nodo antes de enviar la respuesta."""
    return run_node("Guardar cita cifrada", json_data, env, store)


def llm_turn(text, llm_content, store, env=None):
    """Puerta → Estado → Preparar → Procesar con una respuesta simulada de Ollama."""
    g, store = route(text, store, env=env)
    assert g["action"] == "llm", g
    ctx, store = run_node("Preparar solicitud LLM", {}, env, store, {"Estado de la cita": g})
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
        out, _, _ = llm_turn("¿Atienden casos de herencia?", "Gracias. ¿Me indica su nombre completo, por favor?", granted())
        self.assertFalse(out["blockedByConsent"])
        self.assertEqual(out["waMessage"]["text"]["body"], "Gracias. ¿Me indica su nombre completo, por favor?")

    def test_cita_sin_consentimiento_no_se_registra(self):
        out, _, _ = llm_turn("Hola", "Gracias.\n<cita>" + json.dumps(CITA, ensure_ascii=False) + "</cita>", {})
        self.assertFalse(out["hasAppointment"])
        self.assertIsNone(out["appointment"])


@unittest.skipUnless(NODE, "Node.js no está instalado")
class TestReiniciar(unittest.TestCase):
    def store(self):
        s = granted()
        s["conversations"] = {FROM: {"updatedAt": 0, "messages": [{"role": "user", "content": "Hola"}]}}
        return s

    def test_numero_permitido(self):
        out, store = gate("/reiniciar", self.store(), env={"TEST_NUMBERS": "51911111111,51900000000"})
        self.assertEqual(out["reply"], "Conversación reiniciada.")
        self.assertNotIn(FROM, store["consents"])
        self.assertNotIn(FROM, store["conversations"])

    def test_numero_no_permitido(self):
        out, store = gate("/reiniciar", self.store(), env={"TEST_NUMBERS": "51911111111"})
        self.assertEqual(out["action"], "llm")
        self.assertEqual(store["consents"][FROM]["estado"], "otorgado")
        self.assertIn(FROM, store["conversations"])


@unittest.skipUnless(NODE, "Node.js no está instalado")
class TestIndicadorEscribiendo(unittest.TestCase):
    def test_cuerpo_y_url_del_indicador(self):
        g, _ = gate("Hola", messageId="wamid.XYZ", phoneNumberId="200")
        body = json.loads(eval_param("Indicador escribiendo", "jsonBody", g))
        self.assertEqual(body, {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.XYZ",
                                "typing_indicator": {"type": "text"}})
        url = eval_param("Indicador escribiendo", "url", g, {"META_API_VERSION": "v25.0"})
        self.assertEqual(url, "https://graph.facebook.com/v25.0/200/messages")

    def test_un_fallo_del_indicador_no_bloquea(self):
        wf = json.loads((HERE.parent / "n8n_workflow_template.json").read_text(encoding="utf-8"))
        node = next(n for n in wf["nodes"] if n["name"] == "Indicador escribiendo")
        self.assertEqual(node.get("onError"), "continueRegularOutput")
        g, store = route("Hola")
        ctx, _ = run_node("Preparar solicitud LLM", {"error": {"message": "fallo"}}, None, store, {"Estado de la cita": g})
        self.assertEqual(ctx["from"], FROM)


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
        inp = {"from": FROM, "phoneNumberId": "100", "appointment": cita, "hasAppointment": True, "consentStatus": "otorgado",
               "policyVersion": "2026-10"}
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


@unittest.skipUnless(NODE and HAS_CRYPTO, "requiere Node.js y 'python -m pip install cryptography'")
class TestEstadoCita(unittest.TestCase):
    """n8n lleva la solicitud de cita: la guarda aunque la IA no emita el bloque <cita>."""

    KEY = "1e" * 32

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(HERE.parent / "herramientas"))
        import ver_citas
        cls.vc = ver_citas

    def turn(self, text, store, env, button_id=""):
        r, store = route(text, store, button_id, env)
        self.assertEqual(r["action"], "reply", f"{text!r} fue a la IA")
        return finish(r, store, env)

    def test_flujo_completo_sin_bloque_cita_y_ver_citas_lo_lee(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "datos" / "citas.jsonl.enc"
            env = {"CITAS_FILE_KEY": self.KEY, "CITAS_FILE_PATH": str(file)}
            out, store = self.turn("Quiero una cita", {}, env)
            self.assertEqual(out["waMessage"]["type"], "interactive")
            out, store = self.turn("Acepto", store, env, "consent_si")
            self.assertIn("nombre completo", out["reply"])
            for text, button in [("Ana Torres Vega", ""), ("Pensión de alimentos para mi hija", ""),
                                 ("Virtual", "cita_virtual"), ("Miércoles a las 3 pm", "")]:
                out, store = self.turn(text, store, env, button)
                self.assertFalse(file.exists(), "no se guarda antes de tener todos los datos")
            out, store = self.turn("Sin correo", store, env, "cita_sin_correo")
            self.assertTrue(out["saved"])
            self.assertTrue(out["reply"].startswith("Su *solicitud* de cita quedó registrada"))
            self.assertEqual(store["citaFlow"][FROM]["estado"], "registrada")

            citas, bad = self.vc.read_citas(file, self.vc.parse_key(self.KEY))
            self.assertEqual((len(citas), bad), (1, 0))
            c = citas[0]
            self.assertEqual((c["nombre"], c["area"], c["modalidad"], c["preferencia_horaria"], c["estado"], c["origen"]),
                             ("Ana Torres Vega", "Procesos de alimentos", "virtual", "Miércoles a las 3 pm", "completa", "flujo"))

    def test_si_no_se_guarda_no_dice_registrada(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "citas.jsonl.enc"
            env = {"CITAS_FILE_KEY": "no-hex", "CITAS_FILE_PATH": str(file)}
            store = granted()
            for text in ["Quiero una cita", "Ana Torres Vega", "Un contrato de alquiler", "presencial", "lunes 10 am"]:
                _, store = self.turn(text, store, env)
            out, store = self.turn("no", store, env)
            self.assertFalse(out["saved"])
            self.assertNotIn("registrada", out["reply"])
            self.assertIn("956 771 681", out["reply"])
            self.assertFalse(file.exists())

    def test_ia_dice_registrada_sin_cita(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            env = {"CITAS_FILE_KEY": self.KEY, "CITAS_FILE_PATH": str(Path(tmp) / "citas.jsonl.enc")}
            out, store, _ = llm_turn("¿Atienden sábados?", "Su solicitud de cita quedó registrada.", granted(), env)
            out, store = finish(out, store, env)
            self.assertFalse(out["saved"])
            self.assertTrue(out["reply"].startswith("Aún no he registrado su solicitud de cita."))
            self.assertNotIn("quedó registrada", store["conversations"][FROM]["messages"][-1]["content"])

    def test_respaldo_bloque_cita_de_la_ia(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            file = Path(tmp) / "citas.jsonl.enc"
            env = {"CITAS_FILE_KEY": self.KEY, "CITAS_FILE_PATH": str(file)}
            content = "Su solicitud de cita quedó registrada.\n<cita>" + json.dumps(CITA, ensure_ascii=False) + "</cita>"
            out, store, _ = llm_turn("¿Atienden sábados?", content, granted(), env)
            out, store = finish(out, store, env)
            self.assertTrue(out["saved"])
            self.assertEqual(out["origen"], "ia")
            self.assertEqual(out["reply"], "Su solicitud de cita quedó registrada.")
            citas, _ = self.vc.read_citas(file, self.vc.parse_key(self.KEY))
            self.assertEqual(citas[0]["nombre"], "María Pérez Ríos")


if __name__ == "__main__":
    unittest.main()
