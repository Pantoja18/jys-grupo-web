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


if __name__ == "__main__":
    unittest.main()
