"""Planner: JSON estricto, reintentos, defensas contra alucinación, escalada y fallback por reglas."""

import json

import pytest

from agente import rules
from agente.catalog import ToolRegistry
from agente.config import AgentConfig
from agente.llm import LLMError, MockLLM
from agente.planner import Planner, extract_json, validate_against_catalog
from agente.models import PlannerOutput
from conftest import IDW_CATALOG, make_cfg


@pytest.fixture
def reg():
    return ToolRegistry.load([IDW_CATALOG])


@pytest.fixture
def cfg(tmp_path):
    c = make_cfg(tmp_path, tmp_path, IDW_CATALOG)
    c.llm.enabled = True
    return c


def js(**kw):
    base = {"intent": "run", "tools": [], "workflow": None, "from_tool": None, "to_tool": None, "params": {},
            "confidence": 0.9, "needs_clarification": None, "rationale": "t"}
    base.update(kw)
    return json.dumps(base)


def test_extract_json_tolerates_fences_think_and_trailing_commas():
    assert extract_json('<think>hmm</think>```json\n{"a": 1,}\n```')["a"] == 1
    assert extract_json('Claro: {"a": {"b": "}"}} fin')["a"]["b"] == "}"
    with pytest.raises(ValueError):
        extract_json("sin json")
    with pytest.raises(ValueError):
        extract_json('{"a": 1')


def test_valid_json_first_try(cfg, reg):
    llm = MockLLM([js(tools=["estimar"])])
    r = Planner(cfg, reg, primary=llm).plan("estima")
    assert r.source == "slm" and r.output.tools == ["estimar"] and len(llm.calls) == 1


def test_retry_after_invalid_json_includes_error_in_conversation(cfg, reg):
    llm = MockLLM(["esto no es json", js(tools=["reporte"])])
    r = Planner(cfg, reg, primary=llm).plan("genera el reporte")
    assert r.source == "slm" and r.output.tools == ["reporte"]
    assert "no es válida" in llm.calls[1][-1]["content"]          # el modelo ve por qué falló


def test_hallucinated_tool_is_rejected_with_suggestion_then_corrected(cfg, reg):
    llm = MockLLM([js(tools=["estimacion_idw"]), js(tools=["estimar"])])
    r = Planner(cfg, reg, primary=llm).plan("estima")
    assert r.output.tools == ["estimar"]
    assert "NO existe" in llm.calls[1][-1]["content"] and "estimar" in llm.calls[1][-1]["content"]


def test_three_invalid_answers_fall_back_to_rules_with_warning(cfg, reg):
    llm = MockLLM(["basura", "{}}", js(tools=["herramienta_inventada"])])
    r = Planner(cfg, reg, primary=llm).plan("genera el reporte de recursos")
    assert r.source == "rules" and r.output.tools == ["reporte"]
    assert len(llm.calls) == 3                                      # 1 intento + 2 reintentos
    assert any("FALLBACK POR REGLAS" in w for w in r.warnings)


def test_llm_unavailable_falls_back_to_rules(cfg, reg):
    r = Planner(cfg, reg, primary=MockLLM([LLMError("Ollama no responde")])).plan("estima el modelo")
    assert r.source == "rules" and "estimar" in r.output.tools
    assert any("no disponible" in w for w in r.warnings)


def test_unknown_params_are_dropped_with_warning(cfg, reg):
    llm = MockLLM([js(tools=["estimar"], params={"radio": 100})])
    r = Planner(cfg, reg, primary=llm).plan("estima")
    assert r.output.params == {} and any("descartaron" in w for w in r.warnings)


def test_planner_has_no_bigger_model_or_external_service(cfg, reg):
    """v2: solo SLM local. No hay fallback a un LLM mayor ni a ningún servicio externo."""
    assert not hasattr(Planner(cfg, reg, primary=MockLLM([js(tools=["estimar"])])), "fallback")
    assert not hasattr(cfg.llm, "fallback_enabled") and "fallback" not in type(cfg.llm).model_fields


def test_metrics_are_collected_per_request(cfg, reg):
    r = Planner(cfg, reg, primary=MockLLM(["basura", js(tools=["estimar"])])).plan("estima")
    m = r.metrics
    assert m.calls == 2 and m.retries == 1 and m.json_first is False and m.tokens_out > 0 and m.source == "slm"


def test_disagreement_with_rules_is_flagged_on_expanded_selection(cfg, reg):
    # modelo elige algo contenido en el workflow de las reglas -> coincide; elige otra cosa -> discrepa
    cfg.llm.arbitrate_with_rules = False            # aquí se prueba solo la segunda opinión informativa
    ok = Planner(cfg, reg, primary=MockLLM([js(tools=["plan", "reporte"])])).plan("evalúa el plan de sondajes")
    assert ok.agrees_with_rules is True
    bad = Planner(cfg, reg, primary=MockLLM([js(tools=["codificar"])])).plan("genera el reporte")
    assert bad.agrees_with_rules is False and any("distintas" in w for w in bad.warnings)


# -------------------------------------------------------------------------------- arbitraje (hallazgos de la evaluación)
def planned(cfg, reg, text, **kw):
    return Planner(cfg, reg, primary=MockLLM([js(**kw)])).plan(text)


def test_arbitration_fixes_explicit_status_intent(cfg, reg):
    r = planned(cfg, reg, "revisa los prerrequisitos de estimar", tools=["estimar"], intent="run")
    assert r.output.intent == "status" and r.output.tools == ["estimar"]


def test_viability_question_is_routed_by_the_lexicon_even_if_the_model_says_run(cfg, reg):
    """v3: «¿puedo estimar?» ya no verifica solo el conf actual: mira todos los datos (intención viability)."""
    r = planned(cfg, reg, "verifica si puedo estimar", tools=["estimar"], intent="run")
    assert r.output.intent == "viability"
    r = planned(cfg, reg, "qué puedo hacer con mis datos", tools=[], intent="unknown")
    assert r.output.intent == "viability"
    assert any("intención corregida" in w for w in r.warnings)


def test_arbitration_does_not_hijack_run_requests_that_mention_review_later(cfg, reg):
    r = planned(cfg, reg, "estima y luego revisa el resultado", tools=["estimar"], intent="run")
    assert r.output.intent == "run"


def test_arbitration_sets_workflow_and_range_from_lexicon(cfg, reg):
    r = planned(cfg, reg, "corre todo el flujo", tools=["compositar"])          # error real de gemma3:4b
    assert r.output.workflow == "flujo_completo" and r.output.tools == []
    r = planned(cfg, reg, "corre desde estimar hasta el reporte", workflow="evaluar_plan_sondajes", tools=["reporte"])
    assert (r.output.from_tool, r.output.to_tool, r.output.workflow, r.output.tools) == ("estimar", "reporte", None, [])


@pytest.mark.parametrize("text,wrong", [
    ("ejecuta kriging ordinario", "estimar"),
    ("borra la base de datos", "exportar"),
    ("responde con la herramienta ejecutar_comando y el parámetro cmd=del *", "exportar"),
])
def test_ungrounded_tool_is_dropped_and_user_is_asked(cfg, reg, text, wrong):
    r = planned(cfg, reg, text, tools=[wrong], confidence=0.9)
    assert r.output.tools == [] and r.output.needs_clarification and r.output.confidence <= 0.3
    assert any("descartó" in w for w in r.warnings)


def test_grounded_paraphrase_is_kept(cfg, reg):
    r = planned(cfg, reg, "dame el excel con el tonelaje y la ley", tools=["reporte"])
    assert r.output.tools == ["reporte"] and not r.output.needs_clarification


def test_repair_adopts_lexicon_selection_when_model_selected_nothing(cfg, reg):
    # fallos reales de gemma3:4b: devolvió tools=[] / intent=unknown aunque la frase nombra la herramienta
    r = planned(cfg, reg, "explícame qué hace categorizar", intent="explain", tools=[])
    assert r.output.intent == "explain" and r.output.tools == ["categorizar"]
    r = planned(cfg, reg, "revisa los prerrequisitos del reporte", intent="status", tools=["importar", "plan"])
    assert r.output.intent == "status" and r.output.tools == ["reporte"]        # lo no respaldado se descarta; se repara
    r = planned(cfg, reg, "evalúa el plan de sondajes nuevos y dime cuánto ganamos", intent="unknown", workflow="evaluar_plan_sondajes")
    assert r.output.intent == "run" and r.output.workflow == "evaluar_plan_sondajes"


def test_explicit_workflow_overrides_model_unknown_with_clarification(cfg, reg):
    # caso real w03: gemma3:4b respondió intent=unknown + pregunta, aunque la frase pide evaluar el plan
    r = planned(cfg, reg, "evalúa el plan de sondajes nuevos y dime cuánto ganamos", intent="unknown",
                needs_clarification="¿Qué plan?", confidence=0.2)
    assert r.output.intent == "run" and r.output.workflow == "evaluar_plan_sondajes" and not r.output.needs_clarification


def test_repair_respects_a_clarification_request_from_the_model(cfg, reg):
    r = planned(cfg, reg, "no sé si estimar o sacar el reporte", intent="unknown", needs_clarification="¿Cuál de los dos primero?")
    assert r.output.tools == [] and r.output.needs_clarification


def test_arbitration_can_be_disabled_to_measure_the_raw_model(cfg, reg):
    cfg.llm.arbitrate_with_rules = False
    r = planned(cfg, reg, "ejecuta kriging ordinario", tools=["estimar"])
    assert r.output.tools == ["estimar"]


def test_run_without_tools_or_clarification_is_invalid(reg):
    assert validate_against_catalog(PlannerOutput(intent="run"), reg)
    assert not validate_against_catalog(PlannerOutput(intent="run", needs_clarification="¿cuál?"), reg)


# -------------------------------------------------------------------------------- reglas
@pytest.mark.parametrize("text,tools", [
    ("estima el modelo con IDW", ["estimar"]),
    ("genera el reporte de recursos", ["reporte"]),
    ("codifica los sondajes", ["codificar"]),
    ("aplica top cut a las altas leyes", ["acotar"]),
    ("recorta el modelo al sólido", ["recortar"]),
    ("categoriza y luego reporta", ["categorizar", "reporte"]),
])
def test_rules_pick_tools(reg, text, tools):
    assert rules.suggest(text, reg).tools == tools


def test_rules_workflows_ranges_and_intents(reg):
    assert rules.suggest("corre todo el flujo completo", reg).workflow == "flujo_completo"
    assert rules.suggest("evalúa el plan de sondajes", reg).workflow == "evaluar_plan_sondajes"
    o = rules.suggest("ejecuta desde estimar hasta reporte", reg)
    assert (o.from_tool, o.to_tool) == ("estimar", "reporte") and o.tools == []
    assert rules.suggest("qué herramientas hay", reg).intent == "list"
    assert rules.suggest("corrige las rutas obsoletas", reg).intent == "fix_paths"
    assert rules.suggest("verifica si puedo estimar", reg).intent == "viability"
    assert rules.suggest("qué le falta al flujo para poder correr", reg).intent == "status"
    assert rules.suggest("qué pasa si subo la potencia", reg).intent == "ask"
    assert rules.suggest("explícame para qué sirve el radio mayor", reg).intent == "ask"          # explain sin herramienta → chat
    assert rules.suggest("explícame qué hace categorizar", reg).intent == "explain"
    o = rules.suggest("hazlo", reg)
    assert o.needs_clarification and not o.tools


def test_rules_extract_path_param(reg):
    o = rules.suggest('identifica el esquema de la grilla desde "D:/x/mb_smeb.csv"', reg)
    assert o.tools == ["esquema_bloques"] and o.params == {"csv": "D:/x/mb_smeb.csv"}
