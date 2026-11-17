"""Router/Planner: de la solicitud en lenguaje natural a un PlannerOutput VÁLIDO.

Cadena de decisión (v3: SLM en Hugging Face con tu API key, o local con Ollama):
    SLM  --(JSON inválido: hasta max_retries reintentos con el error)-->
    reglas por palabras clave (siempre disponibles, también si falla la red o falta la API key).
Defensas contra alucinación:
    - JSON estricto validado con Pydantic;
    - ids de herramienta/workflow/rango validados contra el catálogo (si no existen se reintenta mostrando
      al modelo los ids más parecidos);
    - parámetros no declarados se descartan y se avisa;
    - 'segunda opinión' por reglas: si el modelo y las palabras clave discrepan, se avisa en el reporte.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from . import rules
from .catalog import ToolRegistry
from .config import AgentConfig
from .llm import CloudClient, LLMClient, LLMError, OllamaClient, build_client, gpu_memory_gb
from .models import PlannerMetrics, PlannerOutput
from .textutil import normalize


class PlannerValidationError(ValueError):
    """La respuesta del modelo es JSON pero incumple el catálogo (ids inventados, run sin herramientas...)."""


@dataclass
class PlanResult:
    output: PlannerOutput
    source: str                                   # slm | rules
    attempts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    agrees_with_rules: Optional[bool] = None
    metrics: PlannerMetrics = field(default_factory=PlannerMetrics)


# --------------------------------------------------------------------------------------------- JSON
_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON de la respuesta (tolera ```json, <think> y texto alrededor)."""
    text = _THINK.sub("", text).strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("la respuesta no contiene un objeto JSON")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                chunk = text[start:i + 1]
                try:
                    return json.loads(chunk)
                except ValueError:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))     # comas finales
    raise ValueError("JSON incompleto (llaves sin cerrar)")


# --------------------------------------------------------------------------------------------- validación
def validate_against_catalog(out: PlannerOutput, reg: ToolRegistry) -> list[str]:
    problems = []
    for t in out.tools:
        if t not in reg:
            sug = reg.suggest_ids(t)
            problems.append(f"la herramienta '{t}' NO existe en el catálogo" + (f" (¿quisiste decir {', '.join(sug)}?)" if sug else ""))
    if out.workflow and out.workflow not in reg.workflows:
        problems.append(f"el workflow '{out.workflow}' NO existe (disponibles: {', '.join(reg.workflows) or 'ninguno'})")
    flow = {t.id for t in reg.flow()}
    for name, val in (("from_tool", out.from_tool), ("to_tool", out.to_tool)):
        if val and val not in flow:
            problems.append(f"{name}='{val}' no es un paso del flujo")
    if out.intent == "run" and not (out.tools or out.workflow or out.from_tool or out.to_tool) and not out.needs_clarification:
        problems.append("intent 'run' sin herramientas: indica 'tools' o, si no sabes, 'needs_clarification'")
    return problems


def _build_examples(reg: ToolRegistry, max_examples: int = 6) -> str:
    """Few-shot automático desde `examples` del catálogo, repartido a lo largo del flujo (no los primeros N).
    Si la herramienta tiene un parámetro de tipo ruta y la frase menciona un archivo, el ejemplo lo extrae."""
    with_ex = [t for t in reg if t.examples]
    step = max(1, len(with_ex) // max_examples)
    lines = []
    for t in with_ex[::step][:max_examples]:
        params: dict = {}
        paths = rules.extract_paths(t.examples[0])
        for p in t.params:
            if p.type == "path" and paths:
                params[p.name] = paths[0]
        js = {"intent": "run", "tools": [t.id], "workflow": None, "from_tool": None, "to_tool": None,
              "params": params, "confidence": 0.9, "needs_clarification": None,
              "rationale": f"pide {t.title.lower()}"}
        lines.append(f"Usuario: {t.examples[0]}\nJSON: {json.dumps(js, ensure_ascii=False)}")

    def shot(text: str, **kw) -> str:
        base = {"intent": "run", "tools": [], "workflow": None, "from_tool": None, "to_tool": None, "params": {},
                "confidence": 0.9, "needs_clarification": None, "rationale": ""}
        base.update(kw)
        return f"Usuario: {text}\nJSON: {json.dumps(base, ensure_ascii=False)}"

    # construcciones estructurales (workflow, rango, verificación): donde un SLM pequeño más falla
    for w in reg.workflows.values():
        if w.examples:
            lines.append(shot(w.examples[0], workflow=w.id, rationale=f"pide el workflow {w.id}"))
    flow = [t.id for t in reg.flow()]
    if len(flow) >= 3:
        a, b = flow[len(flow) // 2], flow[-1]
        lines.append(shot(f"ejecuta desde {a} hasta {b}", from_tool=a, to_tool=b, rationale="rango del flujo"))
        lines.append(shot(f"verifica si puedo ejecutar {flow[len(flow) // 2]}", intent="status", tools=[flow[len(flow) // 2]],
                          rationale="solo verificar, no ejecutar"))
    return "\n".join(lines)


class Planner:
    def __init__(self, cfg: AgentConfig, reg: ToolRegistry, primary: Optional[LLMClient] = None):
        self.cfg, self.reg = cfg, reg
        self.client_error = ""                       # por qué no hay modelo (p. ej. falta la API key); las reglas siguen funcionando
        self.primary = primary
        if primary is None and cfg.llm.enabled:
            try:
                self.primary = build_client(cfg.llm)
            except LLMError as e:
                self.client_error = str(e)
        self._system = self._build_system()

    # ------------------------------------------------------------------ prompt
    def _build_system(self) -> str:
        tpl = (self.cfg.prompts_dir / "planner_system.md").read_text(encoding="utf-8")
        tpl = re.sub(r"<!--.*?-->\s*", "", tpl, flags=re.S)
        return (tpl.replace("{{CATALOG}}", self.reg.llm_catalog())
                   .replace("{{WORKFLOWS}}", self.reg.llm_workflows())
                   .replace("{{EXAMPLES}}", _build_examples(self.reg)))

    # ------------------------------------------------------------------ API
    def plan(self, request: str) -> PlanResult:
        t0 = time.perf_counter()
        rule_out = rules.suggest(request, self.reg)
        warnings: list[str] = []
        attempts: list[dict] = []
        out: Optional[PlannerOutput] = None
        client = self.primary
        mark = client.usage.mark() if client is not None and hasattr(client, "usage") else 0

        if client is not None:
            try:
                out, tries = self._ask(client, request)
                attempts += [{"model": client.name, **t} for t in tries]
            except LLMError as e:
                attempts.append({"model": client.name, "error": str(e)})
                warnings.append(f"{client.name} no disponible: {e}")
            else:
                if out is None:
                    warnings.append(f"{client.name} no entregó un JSON válido tras {self.cfg.llm.max_retries + 1} intentos.")

        metrics = self._metrics(client, mark, attempts, time.perf_counter() - t0)
        if out is None:
            if self.cfg.llm.enabled and client is None and self.client_error:
                warnings.append(f"⚠ Se usó el FALLBACK POR REGLAS (palabras clave): {self.client_error}")
            elif self.cfg.llm.enabled:
                warnings.append("⚠ Se usó el FALLBACK POR REGLAS (palabras clave): el modelo no produjo una respuesta válida. "
                                "Revisa el plan antes de ejecutar.")
            metrics.source = "rules"
            return PlanResult(rule_out, "rules", attempts, warnings, None, metrics)

        out = self._clean_params(out, warnings)
        if self.cfg.llm.arbitrate_with_rules:
            out = self._arbitrate(out, rule_out, request, warnings)
        agrees = self._agree(out, rule_out)
        if agrees is False:
            warnings.append("El modelo y las reglas por palabras clave eligen herramientas distintas "
                            f"(modelo: {self._sel(out)}; reglas: {self._sel(rule_out)}). Verifica el plan.")
        metrics.source = "slm"
        return PlanResult(out, "slm", attempts, warnings, agrees, metrics)

    def _metrics(self, client: Optional[LLMClient], mark: int, attempts: list[dict], wall: float) -> PlannerMetrics:
        m = PlannerMetrics(wall_seconds=wall)
        if client is None:
            return m
        calls = client.usage.since(mark) if hasattr(client, "usage") else []
        graded = [a for a in attempts if "ok" in a]
        m.model = getattr(client, "name", "").split(":", 1)[-1]
        m.calls = len(calls)
        m.retries = max(0, len(graded) - 1)
        m.json_first = bool(graded and graded[0]["ok"]) if graded else None
        m.tokens_in, m.tokens_out = sum(c.tokens_in for c in calls), sum(c.tokens_out for c in calls)
        m.seconds, m.load_seconds = sum(c.seconds for c in calls), sum(c.load_seconds for c in calls)
        gen = sum(c.gen_seconds for c in calls)
        m.tokens_per_s = m.tokens_out / gen if gen > 0 else 0.0
        if calls and isinstance(client, OllamaClient):
            m.vram_gb = gpu_memory_gb(m.model, self.cfg.llm.primary.base_url)
        m.provider = ("huggingface" if isinstance(client, CloudClient) else "ollama" if isinstance(client, OllamaClient) else "")
        price = getattr(client, "price", None)
        if price and calls:
            m.cost_usd = (m.tokens_in * price[0] + m.tokens_out * price[1]) / 1e6
        return m

    # ------------------------------------------------------------------ internos
    def _ask(self, client: LLMClient, request: str) -> tuple[Optional[PlannerOutput], list[dict]]:
        messages = [{"role": "system", "content": self._system},
                    {"role": "user", "content": f"Usuario: {request}\nJSON:"}]
        tries: list[dict] = []
        for n in range(self.cfg.llm.max_retries + 1):
            raw = client.chat(messages, json_mode=False)
            try:
                out = PlannerOutput.model_validate(extract_json(raw))
                problems = validate_against_catalog(out, self.reg)
                if problems:
                    raise PlannerValidationError("; ".join(problems))
                tries.append({"attempt": n + 1, "ok": True})
                return out, tries
            except (ValueError, ValidationError) as e:          # incluye JSONDecodeError y PlannerValidationError
                err = str(e).replace("\n", " ")[:400]
                tries.append({"attempt": n + 1, "ok": False, "error": err, "raw": raw[:300]})
                messages += [{"role": "assistant", "content": raw},
                             {"role": "user", "content": f"Tu respuesta no es válida: {err}\n"
                                                         f"Corrige y responde SOLO con el objeto JSON del formato indicado.\nJSON:"}]
        return None, tries

    def _clean_params(self, out: PlannerOutput, warnings: list[str]) -> PlannerOutput:
        declared = {p.name for tid in out.tools for p in self.reg.get(tid).params}
        if out.workflow and out.workflow in self.reg.workflows:
            declared |= {p.name for tid in self.reg.workflows[out.workflow].tools for p in self.reg.get(tid).params}
        bad = [k for k in out.params if k not in declared]
        if bad:
            warnings.append(f"Se descartaron parámetros no declarados por las herramientas elegidas: {', '.join(bad)}")
            out = out.model_copy(update={"params": {k: v for k, v in out.params.items() if k in declared}})
        return out

    # ------------------------------------------------------------------ arbitraje
    LEAD_CHARS = 12          # una intención explícita solo manda si abre la frase («por favor verifica…» sí; «estima y luego revisa…» no)

    def _arbitrate(self, out: PlannerOutput, rule: PlannerOutput, request: str, warnings: list[str]) -> PlannerOutput:
        """Corrige al modelo donde un SLM de 4B es poco fiable, SIN volver a depender de él:

        1. Intención explícita al inicio de la frase («verifica…», «lista…», «explica…», «reanuda…», «corrige las rutas…»).
        2. Workflow y rango («todo el flujo», «desde X hasta Y»): frases estructurales que el léxico detecta con precisión.
        3. Anclaje: cada herramienta elegida debe tener al menos una palabra clave en la frase. Si no, se DESCARTA
           (un modelo puede «inventar» una herramienta plausible para una petición fuera de alcance, p. ej. kriging → estimar).
           Si no queda nada y el usuario pidió ejecutar, se pregunta en lugar de adivinar.
        """
        text = normalize(request)
        upd: dict = {}
        notes: list[str] = []

        r_intent, pos = rules.detect_intent(text)
        if r_intent in ("formats", "viability") and out.intent != r_intent:
            upd["intent"] = r_intent           # «no sé el formato…» / «¿es viable…?» son inequívocos aunque no abran la frase
            notes.append(f"intención corregida por el léxico: {out.intent} → {r_intent}")
        elif r_intent != "run" and 0 <= pos <= self.LEAD_CHARS and out.intent != r_intent:
            upd["intent"] = r_intent
            notes.append(f"intención corregida por el léxico: {out.intent} → {r_intent}")

        if rule.workflow or rule.from_tool or rule.to_tool:
            if (out.workflow, out.from_tool, out.to_tool) != (rule.workflow, rule.from_tool, rule.to_tool):
                upd.update(workflow=rule.workflow, from_tool=rule.from_tool, to_tool=rule.to_tool, tools=list(rule.tools))
                notes.append("workflow/rango fijado por el léxico: " + self._sel(rule))
            # una estructura explícita («todo el flujo», «desde X hasta Y») no es ambigua: se anula 'unknown'/aclaración del modelo
            if out.intent == "unknown":
                upd["intent"] = rule.intent if rule.intent not in ("list", "resume", "fix_paths") else "run"
            if out.needs_clarification:
                upd["needs_clarification"] = None
                notes.append("se anuló la aclaración del modelo: el léxico identifica un workflow/rango explícito")

        cur_tools = upd.get("tools", out.tools)
        covered = self._expand(out.model_copy(update=upd)) - set(cur_tools)
        scores = rules.score_tools(text, self.reg)
        kept = [t for t in cur_tools if scores.get(t, 0) > 0 or t in covered]
        dropped = [t for t in cur_tools if t not in kept]
        if dropped:
            upd["tools"] = kept
            notes.append("se descartó lo que la frase no respalda: " + ", ".join(dropped))
        final = out.model_copy(update=upd)

        # 4. Reparación: el modelo no eligió nada utilizable (o dijo 'unknown') pero el léxico sí tiene evidencia clara.
        #    No se aplica si el modelo pidió aclaración (p. ej. frase ambigua entre dos pasos).
        eff_intent = upd.get("intent", out.intent)
        if not self._expand(final) and self._expand(rule) and not out.needs_clarification \
                and eff_intent in ("run", "status", "explain", "unknown"):
            upd.update(tools=list(rule.tools), workflow=rule.workflow, from_tool=rule.from_tool, to_tool=rule.to_tool)
            if eff_intent == "unknown":
                upd["intent"] = rule.intent if rule.intent not in ("list", "resume", "fix_paths") else "run"
            notes.append("el modelo no eligió herramientas; se adoptó la selección respaldada por el léxico: " + self._sel(rule))
            dropped = []
            final = out.model_copy(update=upd)
        elif final.intent == "unknown" and self._expand(final) and not out.needs_clarification:
            upd["intent"] = rule.intent if rule.intent not in ("list", "resume", "fix_paths") else "run"
            notes.append("intención 'unknown' con selección válida → " + upd["intent"])
            final = out.model_copy(update=upd)

        intent = final.intent
        if dropped and intent in ("run", "status", "explain") and not self._expand(final):
            upd["needs_clarification"] = (f"No encuentro en tu solicitud nada que corresponda a las herramientas disponibles "
                                          f"(el modelo sugirió: {', '.join(dropped)}). ¿Qué paso quieres ejecutar o verificar?")
            upd["confidence"] = min(out.confidence, 0.3)
            final = out.model_copy(update=upd)
        if notes:
            warnings.append("Arbitraje: " + "; ".join(notes) + ".")
        return final

    @staticmethod
    def _sel(o: PlannerOutput) -> str:
        parts = list(o.tools)
        if o.workflow:
            parts.append(f"workflow:{o.workflow}")
        if o.from_tool or o.to_tool:
            parts.append(f"rango:{o.from_tool or '*'}→{o.to_tool or '*'}")
        return ", ".join(parts) or "(nada)"

    def _expand(self, o: PlannerOutput) -> set[str]:
        """Herramientas que la selección implica (workflow y rango expandidos), sin dependencias."""
        ids = {t for t in o.tools if t in self.reg}
        if o.workflow in self.reg.workflows:
            w = self.reg.workflows[o.workflow]
            ids |= {t.id for t in self.reg.flow()} if w.all_flow else set(w.tools)
        if o.from_tool or o.to_tool:
            flow = [t.id for t in self.reg.flow()]
            a = flow.index(o.from_tool) if o.from_tool in flow else 0
            b = flow.index(o.to_tool) if o.to_tool in flow else len(flow) - 1
            ids |= set(flow[a:b + 1])
        return ids

    def _agree(self, model: PlannerOutput, rule: PlannerOutput) -> Optional[bool]:
        """Coinciden si una selección contiene a la otra (el modelo puede ser más concreto o más amplio)."""
        if model.intent in ("list", "unknown", "resume") or rule.needs_clarification:
            return None            # las reglas no opinan
        a, b = self._expand(model), self._expand(rule)
        return a <= b or b <= a
