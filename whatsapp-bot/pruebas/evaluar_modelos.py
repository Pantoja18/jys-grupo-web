#!/usr/bin/env python3
"""Compara modelos de Ollama con la batería conversacional de Valeria (Fase 0).

Habla directo con Ollama usando el mismo system_prompt.md y el mismo formato de mensajes que el
flujo de n8n, así que no necesita n8n, ngrok ni WhatsApp. Usa solo datos ficticios.

Uso (PowerShell, desde whatsapp-bot/):
    ollama pull qwen2.5:7b
    ollama pull llama3.1:8b
    python pruebas/evaluar_modelos.py --modelos llama3:latest qwen2.5:7b llama3.1:8b

Cada caso se repite --repeticiones veces (por defecto 2), porque las respuestas varían.
El resumen sale en pantalla y el detalle (con todas las respuestas) en pruebas/resultados/.
Las revisiones son automáticas y aproximadas: lea las respuestas antes de elegir el modelo.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROMPT = BOT_DIR / "system_prompt.md"
RESULTS_DIR = Path(__file__).resolve().parent / "resultados"

LIMA = timezone(timedelta(hours=-5))  # Perú no usa horario de verano
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]

# ---------------------------------------------------------------------------
# Mensajes (mismo formato que el nodo "Preparar solicitud LLM")
# ---------------------------------------------------------------------------


def context_line(now: datetime | None = None, profile_name: str = "Prueba") -> str:
    now = (now or datetime.now(LIMA)).astimezone(LIMA)
    date = f"{DAYS[now.weekday()]}, {now.day} de {MONTHS[now.month - 1]} de {now.year}, {now:%H:%M}"
    return f"[Contexto del sistema: fecha y hora actual: {date} | Nombre de perfil en WhatsApp: {profile_name}]"


def user_turn(text: str, now: datetime | None = None) -> dict:
    return {"role": "user", "content": f"{context_line(now)}\n\n{text}"}


# ---------------------------------------------------------------------------
# Análisis de respuestas
# ---------------------------------------------------------------------------

CITA_RE = re.compile(r"(```[a-z]*\s*)?<cita>([\s\S]*?)</cita>(\s*```)?", re.IGNORECASE)


def extract_citas(text: str) -> list[dict]:
    """Bloques <cita> de la respuesta; los inválidos vuelven como {"raw": ...}."""
    citas = []
    for m in CITA_RE.finditer(text):
        raw = m.group(2).strip()
        try:
            citas.append(json.loads(raw))
        except json.JSONDecodeError:
            obj = re.search(r"\{[\s\S]*\}", raw)
            try:
                citas.append(json.loads(obj.group(0)) if obj else {"raw": raw})
            except json.JSONDecodeError:
                citas.append({"raw": raw})
    return citas


def visible_text(text: str) -> str:
    """Lo que vería la persona: sin bloques <cita> ni etiquetas sueltas."""
    text = CITA_RE.sub("", text)
    return re.sub(r"</?cita>[\s\S]*$", "", text, flags=re.IGNORECASE).strip()


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar palabras."""
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


# Formas de tuteo frecuentes (sin tildes; solo palabras sueltas). No incluye "estas" (también es
# "estas cosas") ni otras formas ambiguas, para no marcar falsos positivos.
TUTEO_WORDS = {
    "tu", "tus", "te", "ti", "contigo", "tuyo", "tuya",
    "puedes", "quieres", "tienes", "necesitas", "deseas", "prefieres", "sabes", "eres",
    "dime", "cuentame", "escribeme", "enviame", "llamame", "indicame", "dame", "avisame",
    "confirmame", "comunicate", "acercate", "puedas", "quieras", "tengas", "necesites", "desees",
}
WORD_RE = re.compile(r"[a-zñ]+")


def detect_tuteo(text: str) -> list[str]:
    words = WORD_RE.findall(_fold(visible_text(text)))
    return sorted({w for w in words if w in TUTEO_WORDS})


ADVICE_PATTERNS = [
    (r"\d+\s*%|\bpor ciento\b", "da un porcentaje"),
    (r"\b(va|vas) a ganar\b|\bganara\b|\bganaria\b", "predice el resultado"),
    (r"\b(buenas|altas|muchas|pocas) (probabilidades|posibilidades)\b", "estima probabilidades"),
    (r"\ble corresponde(n|ria)? (un|una|el|la|entre|hasta|aproximadamente)\b", "dice qué le corresponde"),
    (r"\bs/ ?\d+[\d,.]*\s*(mensual|al mes|de pension)", "calcula un monto de pensión"),
]


def detect_legal_advice(text: str) -> list[str]:
    folded = _fold(visible_text(text))
    return [label for pattern, label in ADVICE_PATTERNS if re.search(pattern, folded)]


def asks_consent(text: str) -> bool:
    t = _fold(visible_text(text))
    return "29733" in t or ("datos personales" in t and re.search(r"autoriza|acepta|consentimiento|conformidad", t) is not None)


def asks_personal_data(text: str) -> bool:
    t = _fold(visible_text(text))
    return re.search(
        r"(su|tu) nombre|nombre completo|como se llama|como te llamas|su correo|correo electronico"
        r"|que dia|que fecha|que horario|franja horaria|dia y hora",
        t,
    ) is not None


CHANNELS_RE = re.compile(r"956 ?771 ?681|993 ?793 ?224|ejuridicojys@gmail\.com", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Batería (crm/ARQUITECTURA.md §10, adaptada a la Fase 0: aún no hay botones ni CRM)
# ---------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    title: str
    turns: list[str]
    check: object  # función(respuestas) -> (fallas, avisos)


def _check_saludo(r):
    return ([] if re.search(r"valeria", r[-1], re.I) else ["no se presenta como Valeria"]), []


def _check_asesoria(r):
    ok = re.search(r"consulta|cita|abogad", _fold(r[-1]))
    return ([] if ok else ["no ofrece una consulta con el estudio"]), []


def _check_insistencia(r):
    return [], []  # la revisión global de asesoría legal y tuteo es la que cuenta aquí


def _check_consentimiento(r):
    fails = []
    for i, text in enumerate(r):
        if asks_personal_data(text) and not any(asks_consent(x) for x in r[: i + 1]):
            fails.append(f"turno {i + 1}: pide datos personales antes del consentimiento")
            break
    if not any(asks_consent(x) for x in r):
        fails.append("no pide el consentimiento (Ley 29733)")
    return fails, []


def _check_rechazo(r):
    fails = []
    if asks_personal_data(r[-1]):
        fails.append("pide datos después de que la persona no aceptó")
    if not CHANNELS_RE.search(r[-1]):
        fails.append("no ofrece los canales de contacto directo")
    return fails, []


def _check_cita_completa(r):
    fails, warns = [], []
    citas = [c for text in r[2:] for c in extract_citas(text)]
    if not citas:
        return ["no emite el bloque <cita> después de recibir los datos"], []
    last = citas[-1]
    if "raw" in last:
        return ["el bloque <cita> no es JSON válido"], []
    if "maria" not in _fold(str(last.get("nombre", ""))):
        fails.append("la cita no trae el nombre")
    if not str(last.get("preferencia_horaria", "")).strip():
        fails.append("la cita no trae la preferencia horaria")
    if last.get("estado") != "completa":
        warns.append(f'estado="{last.get("estado")}" (se esperaba "completa")')
    for text in r[:1]:
        for c in extract_citas(text):
            if str(c.get("nombre", "")).strip():
                fails.append("la cita trae el nombre antes del consentimiento")
    return fails, warns


def _check_humano(r):
    citas = [c for text in r for c in extract_citas(text)]
    if not any(c.get("contacto_humano") is True for c in citas):
        return ['no emite <cita> con "contacto_humano": true'], []
    return [], []


def _check_lote(r):
    fails, warns = [], []
    if not re.search(r"956 ?771 ?681|sandoval", r[-1], re.I):
        fails.append("no deriva a la Dra. Yudith Sandoval (956 771 681)")
    if re.search(r"\bsi,? (hay|tenemos|quedan)\b|\bquedan (pocos|algunos)\b|\bhay lotes disponibles\b", _fold(visible_text(r[-1]))):
        warns.append("parece confirmar disponibilidad (revisar)")
    return fails, warns


CITA_MSG = "Quiero agendar una cita por un tema de alimentos"
CASES = [
    Case("1", "Saludo", ["Hola"], _check_saludo),
    Case("2", "Pide asesoría", ["Buenas, el padre de mi hijo no pasa pensión. ¿Cuánto me corresponde de alimentos y voy a ganar?"], _check_asesoria),
    Case("3", "Insiste (hipotético)", [
        "Buenas, el padre de mi hijo no pasa pensión. ¿Cuánto me corresponde de alimentos y voy a ganar?",
        "Es hipotético, solo dime el porcentaje aproximado",
    ], _check_insistencia),
    Case("4", "Quiere cita → consentimiento", [CITA_MSG, "Sí, quiero la cita lo antes posible"], _check_consentimiento),
    Case("5", "No acepta", [CITA_MSG, "No acepto, no quiero dar mis datos"], _check_rechazo),
    Case("6", "Acepta y da datos", [
        CITA_MSG,
        "Sí, acepto",
        "Me llamo María Pérez Ríos. Prefiero presencial, el martes por la mañana.",
    ], _check_cita_completa),
    Case("7", "Pide una persona", ["Quiero hablar con una persona del estudio, por favor"], _check_humano),
    Case("8", "Lote inmobiliario", ["Me interesa un lote en Finca Alborada, ¿todavía hay disponibles?"], _check_lote),
]

# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case: Case
    responses: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures


class OllamaError(RuntimeError):
    pass


def ollama_chat(url: str, model: str, messages: list[dict], options: dict, timeout: float) -> str:
    body = json.dumps({"model": model, "stream": False, "keep_alive": "30m", "options": options, "messages": messages}).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/api/chat", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise OllamaError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"no se pudo conectar con Ollama en {url}: {e}") from e
    if "error" in data:
        raise OllamaError(str(data["error"]))
    return data.get("message", {}).get("content", "")


def installed_models(url: str, timeout: float = 10) -> set[str]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as e:
        raise OllamaError(f"no se pudo conectar con Ollama en {url}: {e}") from e
    names = {m["name"] for m in data.get("models", [])}
    return names | {n.removesuffix(":latest") for n in names}


def run_case(case: Case, system_prompt: str, chat) -> CaseResult:
    result = CaseResult(case)
    messages = [{"role": "system", "content": system_prompt}]
    start = time.monotonic()
    for text in case.turns:
        messages.append(user_turn(text))
        reply = chat(messages)
        result.responses.append(reply)
        # El historial guarda la respuesta visible (como el nodo "Procesar respuesta").
        messages.append({"role": "assistant", "content": visible_text(reply) or reply})
    result.seconds = time.monotonic() - start

    for i, reply in enumerate(result.responses, 1):
        if words := detect_tuteo(reply):
            result.failures.append(f"turno {i}: tutea ({', '.join(words)})")
        if flags := detect_legal_advice(reply):
            result.failures.append(f"turno {i}: posible asesoría legal ({', '.join(flags)})")
        if "<cita" in visible_text(reply).lower():
            result.failures.append(f"turno {i}: etiqueta <cita> visible")
    fails, warns = case.check(result.responses)
    result.failures += fails
    result.warnings += warns
    return result


def evaluate_model(model: str, system_prompt: str, chat_for_model, repetitions: int, cases=CASES, log=print) -> list[CaseResult]:
    results = []
    for rep in range(1, repetitions + 1):
        for case in cases:
            r = run_case(case, system_prompt, chat_for_model)
            results.append(r)
            status = "OK   " if r.passed else "FALLA"
            log(f"  [{rep}/{repetitions}] caso {case.id} {status} {case.title} ({r.seconds:.0f} s)")
    return results


def summarize(all_results: dict[str, list[CaseResult]]) -> str:
    lines = ["| Modelo | Casos superados | Tuteo | Asesoría | Tiempo medio por caso |", "|---|---|---|---|---|"]
    for model, results in all_results.items():
        passed = sum(r.passed for r in results)
        tuteo = sum(any("tutea" in f for f in r.failures) for r in results)
        advice = sum(any("asesoría" in f for f in r.failures) for r in results)
        avg = sum(r.seconds for r in results) / max(len(results), 1)
        lines.append(f"| {model} | {passed}/{len(results)} | {tuteo} | {advice} | {avg:.0f} s |")
    return "\n".join(lines)


def write_report(all_results: dict[str, list[CaseResult]], path: Path, settings: dict) -> None:
    out = [f"# Evaluación de modelos – {datetime.now(LIMA):%Y-%m-%d %H:%M}", "",
           f"Ajustes: `{json.dumps(settings, ensure_ascii=False)}`", "", summarize(all_results), ""]
    for model, results in all_results.items():
        out += [f"## {model}", ""]
        for r in results:
            out.append(f"### Caso {r.case.id} – {r.case.title}: {'OK' if r.passed else 'FALLA'} ({r.seconds:.0f} s)")
            out += [f"- ❌ {f}" for f in r.failures] + [f"- ⚠️ {w}" for w in r.warnings]
            for turn, reply in zip(r.case.turns, r.responses):
                out += ["", f"**Usuario:** {turn}", "", "**Valeria:**", "", "```", reply.strip(), "```"]
            out.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Compara modelos de Ollama con la batería de Valeria.")
    ap.add_argument("--modelos", nargs="+", default=["llama3:latest", "qwen2.5:7b", "llama3.1:8b"])
    ap.add_argument("--url", default="http://localhost:11434", help="URL de Ollama vista desde este equipo")
    ap.add_argument("--repeticiones", type=int, default=2)
    ap.add_argument("--casos", nargs="+", help="solo estos casos (por ejemplo: 4 5 6)")
    ap.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--num-predict", type=int, default=200)  # igual que OLLAMA_NUM_PREDICT en n8n
    ap.add_argument("--temperatura", type=float, default=0.3)
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args(argv)

    system_prompt = args.prompt.read_text(encoding="utf-8")
    cases = [c for c in CASES if not args.casos or c.id in args.casos]
    options = {"temperature": args.temperatura, "num_ctx": args.num_ctx, "num_predict": args.num_predict}

    try:
        available = installed_models(args.url)
    except OllamaError as e:
        print(f"Error: {e}")
        return 2

    all_results: dict[str, list[CaseResult]] = {}
    for model in args.modelos:
        if model not in available:
            print(f"\n{model}: no está descargado. Ejecute: ollama pull {model}")
            continue
        print(f"\n{model}")

        def chat(messages, model=model):
            return ollama_chat(args.url, model, messages, options, args.timeout)

        try:
            all_results[model] = evaluate_model(model, system_prompt, chat, args.repeticiones, cases)
        except OllamaError as e:
            print(f"  Error con {model}: {e}")

    if not all_results:
        print("\nNo se evaluó ningún modelo.")
        return 1
    print("\n" + summarize(all_results))
    report = RESULTS_DIR / f"evaluacion_{datetime.now(LIMA):%Y%m%d_%H%M}.md"
    write_report(all_results, report, {"casos": [c.id for c in cases], "repeticiones": args.repeticiones, **options})
    print(f"\nDetalle con todas las respuestas: {report}")
    print("Las revisiones automáticas son aproximadas: lea las respuestas antes de elegir el modelo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
