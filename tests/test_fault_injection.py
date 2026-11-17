"""L3 · Inyección de fallos: cada prueba rompe UNA cosa y exige el hallazgo y la acción esperados."""

import json

import pytest

from agente.models import PlannerOutput
from agente.orchestrator import Agent
from conftest import ScriptedIO, make_cfg, write_catalog

CAT = """
config_dir: Configuraciones
tools:
  - id: p
    title: P
    description: herramienta con parámetro de ruta
    order: 1
    runner: {script: s/a.py, args: ["{param.f}"]}
    params: [{name: f, type: path, required: true, description: Archivo de entrada, why: Es lo que se procesa, example: data/x.csv}]
    inputs: [{name: i, param: f}]
  - id: q
    title: Q
    description: conf sin plantilla automática
    order: 2
    runner: {script: s/a.py, args: ["{config}"]}
    config: conf_q.json
    required_keys: [k, anidada.x]
  - id: r
    title: R
    description: conf con esquema
    order: 3
    runner: {script: s/a.py, args: ["{config}"]}
    config: conf_r.json
    config_template:
      module: ModR
      fields:
        - {name: n, type: number, default: 1, description: Un número}
        - {name: sub.flag, type: boolean, default: true, description: Un booleano}
"""


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    (root / "s").mkdir()
    (root / "s" / "a.py").write_text("print(1)", encoding="utf-8")
    cfg = make_cfg(tmp_path, root, write_catalog(tmp_path, CAT))
    return root, Agent(cfg, io=ScriptedIO())


def findings(ag, tool, params=None):
    st = ag.plan_from(PlannerOutput(tools=[tool]), "x", params or {})
    return st, {(f.code): f for f in st.findings}


def test_path_param_outside_project_is_rejected(env, tmp_path):
    root, ag = env
    outside = tmp_path / "fuera.csv"
    outside.write_text("a", encoding="utf-8")
    st, f = findings(ag, "p", {"f": str(outside)})
    assert "PARAM_FORBIDDEN_PATH" in f and st.status == "blocked"


def test_missing_param_message_explains_what_why_and_example(env):
    _, ag = env
    _, f = findings(ag, "p")
    h = f["PARAM_MISSING"].hint
    assert "Archivo de entrada" in h and "Es lo que se procesa" in h and "data/x.csv" in h
    assert f["PARAM_MISSING"].fix.kind == "ask_param"


def test_param_inside_project_passes_and_empty_input_warns(env):
    root, ag = env
    (root / "x.csv").write_text("", encoding="utf-8")
    st, f = findings(ag, "p", {"f": "x.csv"})                  # ruta relativa a la raíz
    assert "PARAM_FORBIDDEN_PATH" not in f and f["INPUT_EMPTY"].severity == "warn"


def test_relative_path_param_is_passed_absolute_to_the_script(env):
    """Regresión (hallada ejecutando esquema_bloques en el proyecto real): el script corre en otra carpeta."""
    root, ag = env
    (root / "x.csv").write_text("a", encoding="utf-8")
    (root / "s" / "a.py").write_text(
        "import sys, pathlib\nassert pathlib.Path(sys.argv[1]).is_absolute() and pathlib.Path(sys.argv[1]).is_file(), sys.argv[1]\n",
        encoding="utf-8")
    st = ag.plan_from(PlannerOutput(tools=["p"]), "x", {"f": "x.csv"})
    st = ag.execute(st, assume_yes=True)
    assert st.status == "completed", st.plan.steps[0].diagnosis


def test_conf_without_template_gives_manual_instruction(env):
    _, ag = env
    st, f = findings(ag, "q")
    assert f["CFG_MISSING"].fix.kind == "manual" and "plantillas" in f["CFG_MISSING"].hint
    assert not st.generated                                      # no inventa un conf que no sabe construir


def test_missing_required_keys_name_the_key(env):
    root, ag = env
    (root / "Configuraciones" / "conf_q.json").write_text(json.dumps({"anidada": {}}), encoding="utf-8")
    st, _ = findings(ag, "q")
    msgs = [x.message for x in st.findings if x.code == "CFG_KEY_MISSING"]
    assert any("'k'" in m for m in msgs) and any("anidada.x" in m for m in msgs)


def test_schema_type_mismatch_and_module_mismatch_are_warnings(env):
    root, ag = env
    (root / "Configuraciones" / "conf_r.json").write_text(
        json.dumps({"__modulo__": "OtroModulo", "n": "no es número", "sub": {"flag": "quizá"}}), encoding="utf-8")
    st, _ = findings(ag, "r")
    got = {(x.code, x.severity) for x in st.findings}
    assert ("CFG_SCHEMA", "warn") in got and ("CFG_MODULE_MISMATCH", "warn") in got
    assert any("sub.flag" in x.message for x in st.findings if x.code == "CFG_SCHEMA")


def test_valid_conf_has_no_findings(env):
    root, ag = env
    (root / "Configuraciones" / "conf_r.json").write_text(
        json.dumps({"__modulo__": "ModR", "n": 3, "sub": {"flag": False}}), encoding="utf-8")
    st, f = findings(ag, "r")
    assert [x for x in st.findings if x.severity != "info"] == [] and st.status == "ready"


def test_missing_interpreter_is_reported_once_and_blocks(env):
    _, ag = env
    ag.ctx.python = "Z:/no/existe/python.exe"
    st, f = findings(ag, "r")
    assert "PY_MISSING" in f and st.status == "blocked"


def test_missing_root_blocks_with_actionable_message(tmp_path):
    cfg = make_cfg(tmp_path, tmp_path / "no_existe", write_catalog(tmp_path, CAT))
    st = Agent(cfg, io=ScriptedIO()).plan_from(PlannerOutput(tools=["r"]), "x")
    assert st.status == "blocked" and any(f.code == "ROOT_MISSING" and "idw_root" in f.hint for f in st.findings)
