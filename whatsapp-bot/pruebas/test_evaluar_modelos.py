"""Pruebas del evaluador de modelos y de la coherencia de system_prompt.md.

Uso (desde whatsapp-bot/):  python -m unittest discover -s pruebas -v
No necesita Ollama: usa un servidor falso local.
"""

import json
import re
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluar_modelos as ev  # noqa: E402

PROMPT = ev.DEFAULT_PROMPT.read_text(encoding="utf-8")
CITA_KEYS = {"nombre", "area", "resumen", "tipo_cliente", "modalidad", "preferencia_horaria",
             "correo", "urgente", "contacto_humano", "estado"}
# Texto fijo de consentimiento de crm/ARQUITECTURA.md §6 (lo enviará el sistema en la Fase 3).
CONSENT_TEXT = ("Para gestionar su cita necesitamos usar algunos datos personales (nombre, horario preferido y, "
                "opcionalmente, su correo), conforme a la Ley N.° 29733. Puede revisar nuestra política en "
                "construccionesyedificacionesjys.com/terminos.html#privacidad. ¿Nos autoriza?")


class TestSystemPrompt(unittest.TestCase):
    def test_cita_example_is_valid_one_line_json_with_all_keys(self):
        blocks = re.findall(r"^<cita>(.*)</cita>$", PROMPT, re.M)
        self.assertGreaterEqual(len(blocks), 2, "faltan el formato y el ejemplo del bloque <cita>")
        example = json.loads(blocks[-1])
        self.assertEqual(set(example), CITA_KEYS)
        self.assertEqual(example["estado"], "completa")

    def test_cita_format_keys_match(self):
        fmt = re.findall(r"^<cita>(.*)</cita>$", PROMPT, re.M)[0]
        self.assertEqual(set(re.findall(r'"([a-z_]+)":', fmt)), CITA_KEYS)

    def test_usted_section_has_three_examples(self):
        section = PROMPT.split('## Trato de "Usted" (obligatorio)')[1].split("\n## ")[0]
        self.assertEqual(section.count("❌"), 3)
        self.assertEqual(section.count("✅"), 3)

    def test_correct_examples_have_no_tuteo_and_incorrect_ones_do(self):
        section = PROMPT.split('## Trato de "Usted" (obligatorio)')[1].split("\n## ")[0]
        for wrong, right in re.findall(r'❌ "([^"]+)" → ✅ "([^"]+)"', section):
            self.assertTrue(ev.detect_tuteo(wrong), wrong)
            self.assertFalse(ev.detect_tuteo(right), right)

    def test_consent_text_matches_architecture(self):
        self.assertIn(CONSENT_TEXT, PROMPT)
        self.assertFalse(ev.detect_tuteo(CONSENT_TEXT))

    def test_final_reminder_is_last_section(self):
        self.assertTrue(PROMPT.rstrip().split("\n## ")[-1].startswith("Recordatorio final"))


class TestDetectors(unittest.TestCase):
    def test_tuteo(self):
        self.assertEqual(ev.detect_tuteo("Hola, ¿en qué te puedo ayudar? Cuéntame tu caso."), ["cuentame", "te", "tu"])
        self.assertEqual(ev.detect_tuteo("Buenos días, ¿en qué puedo ayudarle? Estas opciones le pueden servir."), [])
        # El bloque <cita> no cuenta: no lo ve la persona.
        self.assertEqual(ev.detect_tuteo('Gracias. <cita>{"resumen":"dime tu caso"}</cita>'), [])

    def test_legal_advice(self):
        self.assertIn("da un porcentaje", ev.detect_legal_advice("Normalmente es el 20% de sus ingresos."))
        self.assertIn("predice el resultado", ev.detect_legal_advice("Con esas pruebas usted va a ganar."))
        self.assertEqual(ev.detect_legal_advice("Esa evaluación debe hacerla un abogado en la consulta."), [])

    def test_consent_and_personal_data(self):
        self.assertTrue(ev.asks_consent(CONSENT_TEXT))
        self.assertFalse(ev.asks_consent("¿Cuál es su nombre completo?"))
        self.assertTrue(ev.asks_personal_data("¿Me indica su nombre completo, por favor?"))
        self.assertFalse(ev.asks_personal_data("¿En qué puedo ayudarle?"))

    def test_extract_citas(self):
        text = 'Listo.\n```json\n<cita>{"nombre":"Ana","contacto_humano":true}</cita>\n```\n<cita>{roto</cita>'
        citas = ev.extract_citas(text)
        self.assertEqual(citas[0]["nombre"], "Ana")
        self.assertIn("raw", citas[1])
        self.assertEqual(ev.visible_text(text), "Listo.")
        self.assertEqual(ev.visible_text('Perfecto. <cita>{"nombre":"An'), "Perfecto.")

    def test_context_line_matches_n8n_format(self):
        from datetime import datetime
        line = ev.context_line(datetime(2026, 10, 27, 9, 30, tzinfo=ev.LIMA))
        self.assertEqual(line, "[Contexto del sistema: fecha y hora actual: martes, 27 de octubre de 2026, 09:30 | Nombre de perfil en WhatsApp: Prueba]")


# ---------- Ejecución completa contra un Ollama falso ----------

GOOD = {
    "Hola": "Buenos días, soy Valeria, la asistente virtual de JS CONSULTORES Y ABOGADOS. ¿En qué puedo ayudarle?",
    "pensión": "Entiendo su preocupación. Esa evaluación debe hacerla un abogado; ¿le parece si coordinamos una consulta?",
    "hipotético": "Comprendo, pero no puedo darle porcentajes. Un abogado del estudio puede orientarle en la consulta.",
    "agendar": CONSENT_TEXT + '\n<cita>{"nombre":"","area":"Procesos de alimentos","resumen":"","tipo_cliente":"persona_natural","modalidad":"por_definir","preferencia_horaria":"","correo":"","urgente":false,"contacto_humano":false,"estado":"incompleta"}</cita>',
    "lo antes posible": CONSENT_TEXT,
    "No acepto": "Entendido, no usaremos sus datos. Puede comunicarse al WhatsApp 956 771 681 o al 993 793 224.",
    "Sí, acepto": "Gracias. ¿Me indica su nombre completo?",
    "María": 'Gracias, Sra. Pérez. Registré su solicitud.\n<cita>{"nombre":"María Pérez Ríos","area":"Procesos de alimentos","resumen":"Pensión","tipo_cliente":"persona_natural","modalidad":"presencial","preferencia_horaria":"martes por la mañana","correo":"","urgente":false,"contacto_humano":false,"estado":"completa"}</cita>',
    "persona": 'Con gusto, el equipo le escribirá en horario de atención.\n<cita>{"nombre":"","area":"Otro","resumen":"","tipo_cliente":"persona_natural","modalidad":"por_definir","preferencia_horaria":"","correo":"","urgente":false,"contacto_humano":true,"estado":"incompleta"}</cita>',
    "Finca": "Finca Alborada tiene lotes desde 498 m². Para disponibilidad, comuníquese con la Dra. Yudith Sandoval al WhatsApp 956 771 681.",
}
BAD = {"Hola": "Hola, ¿en qué te puedo ayudar?"}


def fake_reply(table, messages):
    last = messages[-1]["content"]
    for key, reply in table.items():
        if key in last:
            return reply
    return GOOD["Hola"]


class FakeOllama(BaseHTTPRequestHandler):
    table = GOOD
    requests = []

    def log_message(self, *args):
        pass

    def _send(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "bueno:latest"}, {"name": "malo:latest"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(req)
        table = {**GOOD, **BAD} if req["model"].startswith("malo") else GOOD
        self._send({"message": {"role": "assistant", "content": fake_reply(table, req["messages"])}, "done": True})


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_installed_models_accepts_names_with_and_without_latest(self):
        names = ev.installed_models(self.url)
        self.assertIn("bueno:latest", names)
        self.assertIn("bueno", names)

    def test_good_model_passes_all_cases_and_request_matches_n8n(self):
        FakeOllama.requests.clear()
        chat = lambda m: ev.ollama_chat(self.url, "bueno", m, {"num_ctx": 8192}, 10)
        results = ev.evaluate_model("bueno", PROMPT, chat, 1, log=lambda *_: None)
        self.assertEqual(len(results), len(ev.CASES))
        for r in results:
            self.assertTrue(r.passed, f"caso {r.case.id}: {r.failures}")
        req = FakeOllama.requests[0]
        self.assertEqual(req["messages"][0], {"role": "system", "content": PROMPT})
        self.assertFalse(req["stream"])
        self.assertEqual(req["options"]["num_ctx"], 8192)
        # El historial lleva la respuesta visible, sin el bloque <cita>.
        later = [r for r in FakeOllama.requests if len(r["messages"]) > 3]
        self.assertTrue(all("<cita>" not in m["content"] for r in later for m in r["messages"] if m["role"] == "assistant"))

    def test_bad_model_fails_on_tuteo(self):
        chat = lambda m: ev.ollama_chat(self.url, "malo", m, {}, 10)
        results = ev.evaluate_model("malo", PROMPT, chat, 1, cases=ev.CASES[:1], log=lambda *_: None)
        self.assertFalse(results[0].passed)
        self.assertTrue(any("tutea" in f for f in results[0].failures))

    def test_main_writes_report_and_skips_missing_models(self):
        import io
        from contextlib import redirect_stdout
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            old = ev.RESULTS_DIR
            ev.RESULTS_DIR = Path(tmp)
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = ev.main(["--url", self.url, "--modelos", "bueno", "no-existe:7b", "--repeticiones", "1", "--casos", "1", "6"])
            finally:
                ev.RESULTS_DIR = old
            self.assertEqual(code, 0)
            self.assertIn("ollama pull no-existe:7b", out.getvalue())
            self.assertIn("| bueno | 2/2 |", out.getvalue())
            report = next(Path(tmp).glob("evaluacion_*.md")).read_text(encoding="utf-8")
            self.assertIn("María Pérez Ríos", report)

    def test_main_reports_unreachable_ollama(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            code = ev.main(["--url", "http://127.0.0.1:9", "--modelos", "x"])
        self.assertEqual(code, 2)
        self.assertIn("no se pudo conectar con Ollama", out.getvalue())


if __name__ == "__main__":
    unittest.main()
