"""Fallback determinista: palabras clave -> herramientas. Se usa cuando el SLM no entrega JSON válido tras los
reintentos, o con llm.enabled = false. También se calcula siempre como 'segunda opinión' para detectar
selecciones alucinadas del modelo.

Palabras clave (en el catálogo, por herramienta/workflow): texto normalizado (sin tildes, minúsculas). Un '*' final
es prefijo (estim* = estima, estimar, estimación...).
"""

from __future__ import annotations

import re
from typing import Optional

from .catalog import ToolRegistry
from .models import PlannerOutput
from .textutil import normalize

INTENTS: list[tuple[str, str]] = [
    ("formats", r"\b(formatos?|plantillas?|que columnas|columnas (?:debe|necesita|requiere|tiene)\w*|archivos? de ejemplo|ejemplos? de (?:archivo|csv|json|conf\w*)|"
                r"que archivos? (?:necesito|debo|requiere\w*|hay que)|que (?:debo|tengo que) (?:llenar|rellenar|completar)|"
                r"como (?:lleno|relleno|completo|armo)|no se como (?:llenar|rellenar|completar|armar))\b"),
    # v3: «¿mis datos alcanzan?» se responde mirando TODOS los datos, no solo lo que apunta el conf actual
    ("viability", r"\b(viab\w+|factib\w+|que me faltan?|(?:que|cuales) datos (?:me )?(?:faltan?|necesito)|"
                  r"con (?:mis|los|estos|esos) datos|(?:mis|los|estos) datos (?:sirven|alcanzan|bastan|son (?:suficientes|validos|utiles|aptos|adecuados)|"
                  r"estan (?:completos|listos|bien))|datos (?:suficientes|necesarios|completos)|"
                  r"que (?:procesos?|pasos?|trabajos?|cosas) (?:puedo|se puede|podria|puede)|que (?:puedo|podria|se puede) (?:hacer|ejecutar|correr|realizar|estimar|calcular)|"
                  r"(?:puedo|podria|se puede|es posible) (?:estimar|evaluar|categorizar|reportar|hacer (?:la |una )?estimacion|calcular (?:la |los )?(?:ley|leyes|recursos)))\b"),
    ("list", r"\b(lista\w*|que herramientas|herramientas disponibles|catalogo|que puedes hacer|ayuda)\b"),
    ("resume", r"\b(reanud\w+|retom\w+|continu\w+)\b"),
    ("fix_paths", r"\b(corrig\w+|arregl\w+|repar\w+|actualiz\w+|reubic\w+)\b.*\brutas?\b|\brutas? (obsoletas?|rotas?|incorrectas?|erroneas?)\b"),
    # v3 · chatbot: dudas sobre procesos y parámetros («¿qué pasa si subo la potencia?», «¿cómo se elige el tope?»)
    ("ask", r"\b(que (?:pasa|ocurre|sucede|pasaria|ocurriria)\b|que (?:efecto|impacto|influencia) (?:tiene|tendria)|"
            r"(?:que|cual) (?:es|son|significa|significan|mide|miden|representa|indica|valor|valores|rango|numero|parametro|parametros|opcion|opciones)\b|"
            r"como (?:se )?(?:elige|escoge|define|calcula|determina|configura|ajusto|ajustar|cambio|cambiar|modifico|modificar|afecta|afectan|influye|decido|fijo|fijar|selecciono)\b|"
            r"que (?:valor|valores|rango) (?:debo|conviene|recomiendas|usar|poner|elijo)|diferencia entre|en que se diferencia|"
            r"(?:manual|documentacion|guia de parametros)|que (?:parametros?|configuraciones?|opciones?) (?:tiene|puedo|hay|modifico|cambio)|"
            r"por que (?:se|hay que|conviene|es)\b|cuando (?:conviene|debo|se usa|usar)|recomiendas?\b|para que (?:se usa|se utiliza|es)\b)"),
    ("explain", r"\b(explic\w+|que hace|para que sirve|describ\w+|como funciona)\b"),
    ("status", r"\b(verific\w+|revis\w+|cheque\w+|comprueb\w+|que (?:le |les )?falta|prerrequisitos?|esta listo|puedo ejecutar|valid\w+|diagnostic\w+)\b"),
]


_GAP = r"(?:\s+\w+){0,4}\s+"          # «~» en una palabra clave = hasta 4 palabras intermedias


def _kw_regex(kw: str) -> re.Pattern[str]:
    """'*' = resto de una palabra: 'evalu* plan*' casa 'evalua plan' y 'evaluar planes'.
    ' ~ ' = hasta 4 palabras cualesquiera en medio: 'estim* ~ sondaje* ~ recortad*' casa 'estimacion desde sondajes de un modelo recortado'."""
    kw = normalize(kw)
    parts = [re.escape(p).replace(r"\*", r"\w*") for p in re.split(r"\s+~\s+", kw)]
    return re.compile(r"\b" + _GAP.join(parts) + ("" if kw.endswith("*") else r"\b"))


def _tool_keywords(t) -> list[str]:
    """Palabras clave del catálogo (+ sinónimos). El id solo cuenta si es compuesto (p. ej. 'esquema_bloques'): ids de una
    palabra como 'modelo' o 'plan' son palabras comunes y generarían falsos positivos ('estima el modelo')."""
    extra = [t.id.replace("_", " ")] if "_" in t.id else []
    return list(t.keywords) + list(t.synonyms) + extra


def score_tools(text: str, reg: ToolRegistry, only_flow: bool = False) -> dict[str, float]:
    scores: dict[str, float] = {}
    for t in reg:
        if only_flow and t.category != "flujo":
            continue
        s = 0.0
        for kw in _tool_keywords(t):
            n = len(_kw_regex(kw).findall(text))
            if n:
                s += n * (1 + 0.5 * (len(kw.split()) - 1))
        if s:
            scores[t.id] = s
    return scores


def _best_tool(chunk: str, reg: ToolRegistry) -> Optional[str]:
    sc = score_tools(chunk, reg, only_flow=True)
    return max(sc, key=sc.get) if sc else None


_PATH_RE = re.compile(r"\"([^\"]+)\"|'([^']+)'|([A-Za-z]:[\\/][^\s\"']+)|(\S+\.(?:csv|obj|json|xlsx|db|txt))", re.I)


def extract_paths(request: str) -> list[str]:
    out = []
    for m in _PATH_RE.finditer(request):
        val = next(g for g in m.groups() if g)
        out.append(val.rstrip(".,;)"))
    return out


def detect_intent(text: str) -> tuple[str, int]:
    """(intención, posición del disparador en el texto normalizado). ('run', -1) si no hay disparador explícito."""
    for name, pat in INTENTS:
        m = re.search(pat, text)
        if m:
            return name, m.start()
    return "run", -1


def suggest(request: str, reg: ToolRegistry) -> PlannerOutput:
    text = normalize(request)
    intent, _ = detect_intent(text)

    if intent == "list":
        return PlannerOutput(intent="list", confidence=0.7, rationale="reglas: pide el catálogo")
    if intent == "viability":
        sc = score_tools(text, reg)
        return PlannerOutput(intent="viability", tools=[t.id for t in reg if t.id in sc], confidence=0.7,
                             rationale="reglas: pregunta si los datos alcanzan / qué procesos puede hacer con ellos")
    if intent == "ask":
        sc = score_tools(text, reg)
        return PlannerOutput(intent="ask", tools=[t.id for t in reg if t.id in sc], confidence=0.7,
                             rationale="reglas: es una duda sobre un proceso o parámetro (se responde con el manual)")
    if intent == "formats":
        sc = score_tools(text, reg)
        return PlannerOutput(intent="formats", tools=[t.id for t in reg if t.id in sc], confidence=0.7,
                             rationale="reglas: pide el formato de los archivos / una plantilla")

    # workflow
    workflow: Optional[str] = None
    wf_span: Optional[tuple[int, int]] = None
    for w in reg.workflows.values():
        hit = next((m for m in (_kw_regex(k).search(text) for k in w.keywords) if m), None)
        if hit:
            workflow, wf_span = w.id, hit.span()
            break

    def overlaps(m: re.Match) -> bool:       # «estimación desde sondajes…»: ese «desde» es parte del flujo, no un rango de pasos
        return bool(wf_span and m.start() < wf_span[1] and wf_span[0] < m.end())

    # rango
    from_tool = to_tool = None
    rest = text
    m = re.search(r"\bdesde\b\s*(?:el paso |la etapa |el |la )?(.+?)\s+\bhasta\b\s*(?:el paso |la etapa |el |la )?(.+?)(?:[.,;:]|$)", text)
    if m and overlaps(m):
        m = None
    if m:
        from_tool, to_tool = _best_tool(m.group(1), reg), _best_tool(m.group(2), reg)
        rest = text.replace(m.group(0), " ")
    else:
        m = re.search(r"\b(?:a partir de|desde)\b\s*(?:el paso |la etapa |el |la )?(.+?)(?:[.,;:]|$)", text)
        if m and not overlaps(m) and _best_tool(m.group(1), reg):
            from_tool = _best_tool(m.group(1), reg)
            rest = text.replace(m.group(0), " ")
        else:
            m = re.search(r"\bhasta\b\s*(?:el paso |la etapa |el |la )?(.+?)(?:[.,;:]|$)", text)
            if m and _best_tool(m.group(1), reg):
                to_tool = _best_tool(m.group(1), reg)
                rest = text.replace(m.group(0), " ")

    scores = score_tools(rest, reg)
    tools = [t.id for t in reg if t.id in scores]
    if workflow:
        # las herramientas nombradas junto al workflow ya van dentro del workflow
        tools = [t for t in tools if t not in (reg.workflows[workflow].tools or [])]
        if reg.workflows[workflow].all_flow:
            tools = [t for t in tools if reg.get(t).category != "flujo"]

    params: dict = {}
    paths = extract_paths(request)
    if paths:
        for tid in tools:
            for p in reg.get(tid).params:
                if p.type == "path" and p.name not in params:
                    params[p.name] = paths[0]
                    break

    found = bool(tools or workflow or from_tool or to_tool)
    out = PlannerOutput(intent=intent, tools=tools, workflow=workflow, from_tool=from_tool, to_tool=to_tool,
                        params=params, confidence=0.6 if found else 0.2,
                        rationale="reglas por palabras clave: " + (", ".join(f"{k}={v:g}" for k, v in scores.items()) or "sin coincidencias"))
    if intent == "explain" and not found:
        return PlannerOutput(intent="ask", confidence=0.6, rationale="reglas: pide una explicación general (se responde con el manual)")
    if intent in ("run", "explain") and not found:
        out.needs_clarification = ("No identifiqué qué herramienta quieres usar. Indícame el paso o el objetivo "
                                   "(p. ej. 'estimar', 'evaluar un plan de sondajes', 'generar el reporte'), "
                                   "o pide 'lista las herramientas'.")
    return out
