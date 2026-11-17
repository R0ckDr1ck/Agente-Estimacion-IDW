"""Coherencia del catálogo real contra el proyecto real (SOLO LECTURA). Se omite si el proyecto no está.

Detecta cuándo el catálogo se desfasa del proyecto (script movido, clave renombrada en un conf...).
Las rutas obsoletas y los insumos aún no generados NO son fallos del catálogo (son hallazgos normales).
"""

import pytest

from agente.config import AgentConfig
from agente.orchestrator import Agent
from agente.models import PlannerOutput
from conftest import IDW_CATALOG, ScriptedIO

cfg0 = AgentConfig.load(catalogs=[str(IDW_CATALOG)])
pytestmark = pytest.mark.skipif(not (cfg0.idw_root / "Flujo_Estimacion" / "EjecutarFlujo.py").is_file(),
                                reason="Proyecto_IDW no disponible")


@pytest.fixture(scope="module")
def state(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("real")
    cfg = AgentConfig.load(catalogs=[str(IDW_CATALOG)])
    cfg.runs_dir, cfg.templates_dir, cfg.llm.enabled = tmp / "runs", tmp / "pl", False
    agent = Agent(cfg, io=ScriptedIO())
    flow = [t.id for t in agent.registry.flow()]
    return agent.plan_from(PlannerOutput(tools=flow + ["esquema_bloques"]), "check real", {"csv": "x.csv"})


def test_every_catalog_script_exists(state):
    assert not [f for f in state.findings if f.code == "SCRIPT_MISSING"]


def test_every_required_key_exists_in_real_configs(state):
    assert not [f for f in state.findings if f.code == "CFG_KEY_MISSING"], \
        [f.message for f in state.findings if f.code == "CFG_KEY_MISSING"]


def test_real_configs_satisfy_declared_schemas(state):
    assert not [f for f in state.findings if f.code in ("CFG_SCHEMA", "CFG_MODULE_MISMATCH", "CFG_INVALID", "CFG_MISSING")], \
        [f.message for f in state.findings if f.code.startswith("CFG")]


def test_real_dependencies_are_installed(state):
    assert not [f for f in state.findings if f.code == "DEP_MISSING"], [f.message for f in state.findings if f.code == "DEP_MISSING"]


def test_skip_logic_matches_ejecutarflujo(state):
    """Debe coincidir con pasos_omitidos() de EjecutarFlujo.py para la configuración actual."""
    import json, sys
    cfg = AgentConfig.load(catalogs=[str(IDW_CATALOG)])
    sys.path.insert(0, str(cfg.idw_root / "Flujo_Estimacion"))
    try:
        import EjecutarFlujo
    finally:
        sys.path.pop(0)
    expected = EjecutarFlujo.pasos_omitidos(str(cfg.config_path))
    from agente.catalog import ToolRegistry
    from agente.context import ProjectContext
    ctx = ProjectContext(cfg, ToolRegistry.load([IDW_CATALOG]))
    mine = {t.id for t in ctx.registry.flow() if ctx.skip_reasons(t)}
    assert mine == set(expected)
