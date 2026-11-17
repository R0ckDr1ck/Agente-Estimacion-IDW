"""Preflight (omisiones, dependencias Python, cadena rota) y corrección de rutas obsoletas."""

import json
import shutil
from pathlib import Path

import pytest

from agente import pathfix
from agente.context import ProjectContext
from agente.catalog import ToolRegistry
from agente.config import AgentConfig
from agente.executor import diagnose, run_command
from agente.models import PlannerOutput
from agente.orchestrator import Agent
from conftest import ScriptedIO, make_cfg, write_catalog

CAT = """
config_dir: Configuraciones
tools:
  - id: a
    title: A
    description: primero
    order: 1
    runner: {script: s/a.py, args: ["{config}"]}
    config: conf_a.json
    requires_python: [{module: modulo_que_no_existe_xyz, pip: paquete-xyz}]
    skip_when: [{key: activo, op: falsy, default: true}]
    inputs: [{name: i, key: entrada}]
    outputs: [{name: o, key: salida}]
  - id: b
    title: B
    description: segundo
    order: 2
    runner: {script: s/a.py, args: ["{config}"]}
    config: conf_b.json
    inputs: [{name: i, key: entrada}, {name: sol, key: "solidos[*].ruta"}]
    outputs: [{name: o, key: salida}]
"""


def project(tmp_path, a_conf, b_conf):
    root = tmp_path / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    (root / "s").mkdir()
    (root / "s" / "a.py").write_text("print('ok')", encoding="utf-8")
    for n, c in (("conf_a.json", a_conf), ("conf_b.json", b_conf)):
        if c is not None:
            (root / "Configuraciones" / n).write_text(json.dumps(c), encoding="utf-8")
    return root


def agent_for(tmp_path, root):
    cfg = make_cfg(tmp_path, root, write_catalog(tmp_path, CAT))
    return Agent(cfg, io=ScriptedIO())


def test_skip_when_omits_from_workflow_but_not_from_explicit_request(tmp_path):
    root = project(tmp_path, {"activo": False, "entrada": "x", "salida": "y"}, {"entrada": "y", "salida": "z", "solidos": []})
    ag = agent_for(tmp_path, root)
    st = ag.plan_from(PlannerOutput(from_tool="a", to_tool="b"), "rango")
    assert [s.tool_id for s in st.plan.steps] == ["b"]
    assert "omitido por la configuración" in st.plan.omitted[0].reason
    st = ag.plan_from(PlannerOutput(tools=["a"]), "explícito")
    assert st.plan.steps[0].forced and ("a", "SKIPPED_FORCED") in {(f.tool, f.code) for f in st.findings}


def test_missing_python_dependency_gives_exact_install_command(tmp_path):
    root = project(tmp_path, {"entrada": "x", "salida": "y"}, {})
    (root / "x").write_text("d", encoding="utf-8")
    st = agent_for(tmp_path, root).plan_from(PlannerOutput(tools=["a"]), "dep")
    f = next(f for f in st.findings if f.code == "DEP_MISSING")
    assert "pip install paquete-xyz" in f.fix.command and f.severity == "error"


def test_broken_chain_suggests_the_producer(tmp_path):
    # b lee 'yy' pero a produce 'y': nombre casi igual -> sugerencia
    root = project(tmp_path, {"entrada": "x", "salida": "out/y.csv"}, {"entrada": "out/yy.csv", "salida": "z", "solidos": []})
    st = agent_for(tmp_path, root).plan_from(PlannerOutput(tools=["b"]), "cadena")
    f = next(f for f in st.findings if f.code == "INPUT_MISSING")
    assert "y.csv" in f.hint and "'a'" in f.hint


def test_invalid_json_and_missing_script_are_reported(tmp_path):
    root = project(tmp_path, None, {})
    (root / "Configuraciones" / "conf_a.json").write_text("{ no es json", encoding="utf-8")
    (root / "s" / "a.py").unlink()
    st = agent_for(tmp_path, root).plan_from(PlannerOutput(tools=["a"]), "x")
    c = {f.code for f in st.findings}
    assert {"CFG_INVALID", "SCRIPT_MISSING"} <= c and st.status == "blocked"


# ------------------------------------------------------------------------------ rutas obsoletas
def old_layout(tmp_path):
    root = tmp_path / "idw"
    old = tmp_path / "otro" / "v7"
    for p in (root / "01_Data" / "03_Dominio", root / "Configuraciones", root / "s", old / "01_Data" / "03_Dominio"):
        p.mkdir(parents=True)
    (root / "01_Data" / "03_Dominio" / "m.obj").write_text("o", encoding="utf-8")
    (old / "01_Data" / "03_Dominio" / "m.obj").write_text("o", encoding="utf-8")        # existe pero FUERA de la raíz
    (root / "01_Data" / "z.csv").write_text("c", encoding="utf-8")
    (root / "s" / "a.py").write_text("print(1)", encoding="utf-8")
    conf = {"entrada": str(old / "01_Data" / "03_Dominio" / "m.obj"), "salida": str(root / "out" / "o.csv"),
            "solidos": [{"ruta": str(tmp_path / "viejo" / "z.csv")}], "acentos": "Compósito"}
    text = json.dumps(conf, indent=4, ensure_ascii=False)
    (root / "Configuraciones" / "conf_b.json").write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    return root, text


def test_stale_paths_are_relocated_inside_project_even_if_they_exist_elsewhere(tmp_path):
    root, _ = old_layout(tmp_path)
    ag = agent_for(tmp_path, root)
    st = ag.plan_from(PlannerOutput(tools=["b"]), "rutas")
    assert ("b", "STALE_PATH") in {(f.tool, f.code) for f in st.findings}      # aviso aunque el archivo exista
    fx = {f.jpath: f for f in st.path_fixes}
    assert fx["entrada"].new == str(root / "01_Data" / "03_Dominio" / "m.obj")  # ancla 01_Data
    assert fx["solidos[0].ruta"].new == str(root / "01_Data" / "z.csv")          # único por nombre de archivo


def test_apply_fixes_is_textual_with_backup_and_preserves_bom_and_format(tmp_path):
    root, text = old_layout(tmp_path)
    ag = agent_for(tmp_path, root)
    fixes = ag.propose_path_fixes()
    log = ag.apply_path_fixes([f for f in fixes if f.new])
    conf = root / "Configuraciones" / "conf_b.json"
    raw = conf.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                                       # BOM conservado
    data = json.loads(raw.decode("utf-8-sig"))
    assert data["entrada"].startswith(str(root)) and data["acentos"] == "Compósito"
    # solo cambiaron las cadenas de las rutas: el resto del texto es idéntico
    assert raw.decode("utf-8-sig").count("\n") == text.count("\n")
    backups = list((root / "Configuraciones" / "_respaldo_agente").rglob("conf_b.json"))
    assert backups and json.loads(backups[0].read_text(encoding="utf-8-sig"))["entrada"].startswith(str(tmp_path / "otro"))
    assert any("Respaldo" in l for l in log)
    assert ag.propose_path_fixes() == []                                          # idempotente


def test_ambiguous_match_is_never_applied(tmp_path):
    root, _ = old_layout(tmp_path)
    (root / "01_Data" / "otra").mkdir()
    (root / "01_Data" / "otra" / "z.csv").write_text("c", encoding="utf-8")        # ahora hay dos z.csv
    ag = agent_for(tmp_path, root)
    fx = {f.jpath: f for f in ag.propose_path_fixes()}
    assert fx["solidos[0].ruta"].ambiguous and not fx["solidos[0].ruta"].new
    ag.apply_path_fixes(list(fx.values()))
    data = json.loads((root / "Configuraciones" / "conf_b.json").read_text(encoding="utf-8-sig"))
    assert data["solidos"][0]["ruta"].endswith("viejo\\z.csv") or data["solidos"][0]["ruta"].endswith("viejo/z.csv")


# ------------------------------------------------------------------------------ ejecutor
def test_executor_timeout_kills_process_tree(tmp_path):
    import sys
    import time
    t0 = time.time()
    res = run_command([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"],
                      tmp_path, 2, tmp_path / "t.log")
    assert res.timed_out and time.time() - t0 < 20 and "límite" in res.diagnosis
    assert "TIMEOUT" in (tmp_path / "t.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("trace,expected", [
    ("ModuleNotFoundError: No module named 'trimesh'", "pip install trimesh"),
    ("PermissionError: [Errno 13] Permission denied: 'C:/x/a.xlsx'", "abierto en Excel"),
    ("sqlite3.OperationalError: database is locked", "locked"),
    ("KeyError: 'cu_idw'", "cu_idw"),
    ("MemoryError", "Memoria"),
])
def test_diagnose_translates_tracebacks(trace, expected):
    assert expected in diagnose(trace, "py")
