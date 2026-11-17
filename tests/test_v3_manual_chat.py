"""v3 · manual de parámetros + chatbot: responde con fichas reales, cita sus fuentes y no inventa."""

import json
import shutil
from pathlib import Path

import pytest

from agente import chat, manual as M
from agente.catalog import ToolRegistry
from agente.config import AgentConfig
from agente.llm import LLMError, MockLLM
from agente.orchestrator import Agent

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "tests" / "fixtures" / "confs_mixtos"
CAT = BASE / "catalog" / "tools_idw.yaml"


@pytest.fixture(scope="module")
def reg():
    return ToolRegistry.load([CAT])


@pytest.fixture(scope="module")
def man(reg):
    return M.Manual.load(None, {t.id: t.title for t in reg})


def make_agent(tmp: Path, llm=None) -> Agent:
    root = tmp / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    for f in FIX.glob("conf_*.json"):
        shutil.copy(f, root / "Configuraciones" / f.name)
    d, o = tmp / "in", tmp / "out"
    d.mkdir()
    o.mkdir()
    cfg = AgentConfig.load(root=root, data_dir=d, results_dir=o)
    cfg.runs_dir, cfg.templates_dir, cfg.llm.enabled = tmp / "runs", tmp / "tpl", llm is not None
    return Agent(cfg, primary=llm)


# ------------------------------------------------------------------------------------------------ el manual
def test_every_key_of_every_real_conf_is_documented(man):
    """Si alguien agrega un parámetro a un conf y no lo explica en el manual, esta prueba falla."""
    confs = sorted(FIX.glob("conf_*.json"))
    assert len(confs) == 12
    for f in confs:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        assert man.coverage(f.name, data) == [], f.name


def test_every_catalog_tool_has_a_manual_section_with_steps_or_summary(man, reg):
    for t in reg:
        e = man.tool_entry(t.id)
        assert e is not None and e.meaning.strip(), t.id
    assert man.tool_entry("proyecto") is not None


def test_markdown_manual_lists_all_tools_and_parameters(man, reg):
    md = M.render_markdown(man, reg)
    for t in reg:
        assert f"`{t.id}`" in md
    for key in ("pasadas[*].Potencia", "topes.*.*.tope", "distancias.medido", "cutoff"):
        assert key in md
    assert md.count("#### ") >= 150


@pytest.mark.parametrize("question,expected", [
    ("qué pasa si subo la potencia", "param:estimar:pasadas[*].Potencia"),
    ("cómo elijo el tope de altas leyes", "param:acotar:topes.*.*.tope"),
    ("para qué sirve el radio mayor", "param:estimar:pasadas[*].Mayor"),
    ("qué es el cutoff", "param:reporte:cutoff"),
    ("distancia máxima de medido", "param:categorizar:distancias.medido"),
    ("qué es la tolerancia de pared", "param:codificar:tolerancia_pared"),
    ("longitud del compósito", "param:compositar:longitud"),
    ("máximo por octante", "param:estimar:pasadas[*].Máx/oct"),
])
def test_retrieval_finds_the_right_card(man, question, expected):
    hits = man.find(question)
    assert hits and expected in [e.id for e, _ in hits[:2]], [e.id for e, _ in hits]


def test_nonsense_finds_nothing(man):
    assert man.find("receta de empanadas de pino") == []


# ------------------------------------------------------------------------------------------------ el chat
def test_answer_without_model_returns_the_cards_and_cites_sources(tmp_path):
    ag = make_agent(tmp_path)
    ans = ag.ask("qué pasa si subo la potencia")
    assert ans.mode == "manual" and "Potencia del IDW" in ans.text and ans.sources[0].key == "pasadas[*].Potencia"
    assert "Hoy en tu proyecto" in ans.text and "3" in ans.text                     # valor real de conf_estimacion.json
    assert "Fuentes del manual" in chat.render_answer(ans)


def test_answer_with_model_uses_only_the_cards_and_reports_metrics(tmp_path):
    llm = MockLLM(["<think>pienso</think>Subir la potencia hace la estimación más local (ficha «Potencia del IDW»)."])
    ag = make_agent(tmp_path, llm)
    ans = ag.ask("qué pasa si subo la potencia")
    assert ans.mode == "slm" and ans.text.startswith("Subir la potencia") and "pienso" not in ans.text
    assert ans.tokens_in > 0 and ans.tokens_out > 0 and ans.sources
    sent = llm.calls[0]
    assert sent[0]["role"] == "system" and "SOLO" in sent[0]["content"]
    assert "FICHAS DEL MANUAL" in sent[-1]["content"] and "Potencia del IDW" in sent[-1]["content"]


def test_nothing_private_is_sent_to_the_model(tmp_path):
    """Ni rutas ni datos: solo la pregunta y las fichas (con valores simples del conf)."""
    llm = MockLLM(["ok"])
    ag = make_agent(tmp_path, llm)
    ag.ask("qué ruta tiene la base de datos del flujo y para qué sirve")
    blob = json.dumps(llm.calls[0], ensure_ascii=False)
    assert "Prueba_Agente" not in blob and "D:\\\\" not in blob and ":\\\\" not in blob


def test_model_failure_falls_back_to_the_cards(tmp_path):
    ag = make_agent(tmp_path, MockLLM([LLMError("HTTP 429: límite")]))
    ans = ag.ask("qué es el cutoff")
    assert ans.mode == "manual" and "Ley de corte" in ans.text and any("429" in w for w in ans.warnings)


def test_no_cards_means_no_model_call(tmp_path):
    llm = MockLLM([])                                   # si se llamara al modelo, MockLLM lanzaría error
    ans = make_agent(tmp_path, llm).ask("receta de empanadas de pino")
    assert ans.sources == [] and "No encontré" in ans.text and llm.calls == []


def test_conversation_history_reaches_the_model(tmp_path):
    llm = MockLLM(["respuesta"])
    ag = make_agent(tmp_path, llm)
    ag.ask("y si la subo a 4 qué pasa con la potencia", history=[{"role": "user", "content": "qué es la potencia"},
                                                                  {"role": "assistant", "content": "Es el exponente del IDW."}])
    roles = [m["role"] for m in llm.calls[0]]
    assert roles == ["system", "user", "assistant", "user"]


def test_current_value_hides_paths_and_summarises_lists(tmp_path):
    ag = make_agent(tmp_path)
    man = ag.manual
    pot = man.get("param:estimar:pasadas[*].Potencia")
    assert chat.current_value(ag.ctx, pot).startswith("3")
    ruta = man.get("param:estimar:proyecto")
    assert chat.current_value(ag.ctx, ruta) == ""                                    # es una ruta: no se muestra ni se envía


def test_agent_routes_questions_to_the_chat_and_keeps_the_answer_in_the_run(tmp_path):
    ag = make_agent(tmp_path)
    st = ag.plan("¿qué pasa si subo la potencia?")
    assert st.status == "info" and st.planner.intent == "ask" and st.answer and st.answer.sources
    assert "Potencia" in ag.report(st)
    again = ag.store.load(st.run_id)
    assert again.answer and again.answer.text == st.answer.text                    # persiste en la ejecución
    st2 = ag.plan("explícame qué hace categorizar")
    assert st2.planner.intent == "explain" and st2.answer and "categoriz" in ag.report(st2).lower()


def test_questions_never_execute_anything(tmp_path):
    ag = make_agent(tmp_path)
    st = ag.plan("qué pasa si subo la potencia")
    st = ag.execute(st, assume_yes=True)
    assert st.status == "info" and not st.plan.steps
