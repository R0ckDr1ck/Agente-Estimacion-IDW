"""v3 · «¿mis datos alcanzan?» mira TODOS los datos (collar, survey, assay, compósitos, modelo…), no solo lo que apunta el conf."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from agente import datos
from agente.catalog import ToolRegistry
from agente.config import AgentConfig
from agente.models import PlannerOutput
from agente.orchestrator import Agent

BASE = Path(__file__).resolve().parents[1]
FIX = BASE / "tests" / "fixtures" / "confs_mixtos"
CAT = BASE / "catalog" / "tools_idw.yaml"
PRUEBAS = Path(r"D:\Proyecto_SLM")

HEADERS = {
    "collar": "id,este,norte,elevacion,longitud",
    "survey": "id,depth,azimuth,dip",
    "assay": "id,from,to,cupct,dominio_antiguo",
    "composites": "Sondaje,Desde,Hasta,length,MIDX,MIDY,MIDZ,TOPX,TOPY,TOPZ,BOTX,BOTY,BOTZ,CUPCT,CUPCT_ACOT,DOMCU,DOMCU_PCT",
    "block_model": "XC,YC,ZC,DX,DY,DZ,domcu,lva_b,lva_d,lva_p,topof,densidad,cu_idw,categ",
    "plan_collar": "ID,X,Y,Z,Longitud",
    "plan_survey": "ID,Depth,Az,Dip",
}
PLACE = {"collar": "01_Database/ddh_collar.csv", "survey": "01_Database/ddh_survey.csv", "assay": "01_Database/ddh_assay.csv",
         "composites": "01_Database/assay_comp.csv", "block_model": "02_Modelo-Bloques/mb.csv",
         "plan_collar": "07_Sondajes_Nuevos/Plan-DDH_collar.csv", "plan_survey": "07_Sondajes_Nuevos/Plan-DDH_survey.csv"}


def make_ws(tmp: Path, kinds: list[str], solids: bool = True, target: bool = False):
    """Carpeta de entrada con SOLO los datos pedidos (archivos mínimos: la detección es por cabeceras)."""
    d = tmp / "in" / "01_Data"
    for k in kinds:
        p = d / PLACE[k]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(HEADERS[k] + "\n1,2,3\n", encoding="utf-8")
    if solids:
        (d / "03_Dominio").mkdir(parents=True, exist_ok=True)
        (d / "03_Dominio" / "21_A.obj").write_text("o\n", encoding="utf-8")
    if target:
        (d / "05_Objetivo").mkdir(parents=True, exist_ok=True)
        (d / "05_Objetivo" / "OXIDOS.obj").write_text("o\n", encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_agent(tmp: Path, data_dir: Path) -> Agent:
    root = tmp / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    for f in FIX.glob("conf_*.json"):
        shutil.copy(f, root / "Configuraciones" / f.name)
    out = tmp / "out" / "02_Resultados"
    out.mkdir(parents=True)
    cfg = AgentConfig.load(root=root, data_dir=data_dir, results_dir=out)
    cfg.runs_dir, cfg.templates_dir, cfg.llm.enabled = tmp / "runs", tmp / "tpl", False
    ag = Agent(cfg)
    ag.align_paths_to_workspace()
    return ag


def route(rep, rid):
    return next(r for r in rep.routes if r.route.id == rid)


def tool(rep, tid):
    return next(t for t in rep.tools if t.tool_id == tid)


# ------------------------------------------------------------------------------------------------ reconocimiento por cabeceras
def test_files_are_recognised_by_their_headers_not_by_name_or_conf(tmp_path):
    d = make_ws(tmp_path, ["collar", "survey", "assay", "composites", "block_model", "plan_collar", "plan_survey"], target=True)
    (d / "01_Database" / "ddh_collar.csv").rename(d / "01_Database" / "cualquier_nombre.csv")        # el nombre no importa
    rep = make_agent(tmp_path, d).data_report()
    assert set(rep.found) == {"collar", "survey", "assay", "composites", "block_model", "plan_collar", "plan_survey", "solids", "solid_target"}
    assert Path(rep.found["collar"].files[0]).name == "cualquier_nombre.csv"
    assert rep.found["solids"].detail.startswith("1 archivo")


def test_plan_files_are_not_confused_with_real_boreholes(tmp_path):
    d = make_ws(tmp_path, ["plan_collar", "plan_survey"])
    rep = make_agent(tmp_path, d).data_report()
    assert "collar" not in rep.found and "survey" not in rep.found and {"plan_collar", "plan_survey"} <= set(rep.found)


def test_headers_with_semicolon_bom_and_other_names_are_understood(tmp_path):
    d = tmp_path / "in" / "01_Data"
    (d / "x").mkdir(parents=True)
    (d / "x" / "a.csv").write_bytes("\ufeffHoleID;East;North;RL;Depth\n".encode("utf-8"))
    (d / "x" / "b.csv").write_text("BHID\tFrom\tTo\tAu\n", encoding="utf-8")
    rep = make_agent(tmp_path, d).data_report()
    assert {"collar", "assay"} <= set(rep.found)


# ------------------------------------------------------------------------------------------------ rutas y procesos
def test_composites_route_does_not_need_collar_survey_or_assay(tmp_path):
    d = make_ws(tmp_path, ["composites", "block_model"])
    rep = make_agent(tmp_path, d).data_report()
    assert route(rep, "estimar_desde_compositos").feasible
    r2 = route(rep, "estimar_desde_sondajes")
    assert not r2.feasible and set(r2.missing_base) == {"collar", "survey", "assay"}


def test_raw_boreholes_route_works_when_the_conf_says_composites(tmp_path):
    """El caso reportado: la importación del conf está en «compósitos», pero hay collar + survey + assay: también es viable."""
    d = make_ws(tmp_path, ["collar", "survey", "assay", "block_model"])
    ag = make_agent(tmp_path, d)
    conf = json.loads((ag.ctx.cfg_path("conf_importar_bd.json")).read_text(encoding="utf-8-sig"))
    conf["origen"] = "compositos"
    ag.ctx.cfg_path("conf_importar_bd.json").write_text(json.dumps(conf), encoding="utf-8")
    rep = ag.data_report()
    assert route(rep, "estimar_desde_sondajes").feasible and not route(rep, "estimar_desde_compositos").feasible
    assert route(rep, "estimar_desde_compositos").conf_state == "actual"          # el conf apunta a una ruta que NO se puede
    assert route(rep, "estimar_desde_sondajes").conf_state == "distinta"
    assert tool(rep, "codificar").state == "encadenado" and tool(rep, "codificar").chain == ["importar"]


def test_nothing_is_viable_without_enough_data_and_the_report_says_what_is_missing(tmp_path):
    d = make_ws(tmp_path, ["collar", "survey", "block_model"])
    ag = make_agent(tmp_path, d)
    rep = ag.data_report()
    assert not any(r.feasible for r in rep.routes[:2])
    text = datos.render_markdown(rep, ag.registry)
    assert "Todavía no es viable" in text and "Ensayos" in text
    assert tool(rep, "estimar").state == "falta"
    assert tool(rep, "modelo").state == "listo" and tool(rep, "esquema_bloques").state == "listo"


def test_block_model_can_be_created_from_solids_so_estimation_is_still_viable(tmp_path):
    d = make_ws(tmp_path, ["composites"], solids=True)                    # sin modelo de bloques, pero con sólidos
    rep = make_agent(tmp_path, d).data_report()
    r = route(rep, "estimar_desde_compositos")
    assert r.feasible and any(s.tool_id == "modelo" and s.state == "ok" for s in r.steps)
    t = tool(rep, "estimar")
    assert t.state == "encadenado" and t.chain == ["importar", "modelo"]


def test_without_solids_the_missing_block_model_blocks_the_route(tmp_path):
    d = make_ws(tmp_path, ["composites"], solids=False)
    rep = make_agent(tmp_path, d).data_report()
    r = route(rep, "estimar_desde_compositos")
    assert not r.feasible and "solids" in r.missing_base


def test_plan_route_needs_plan_files(tmp_path):
    d = make_ws(tmp_path, ["composites", "block_model"])
    assert not route(make_agent(tmp_path, d).data_report(), "evaluar_plan_sondajes").feasible
    d2 = make_ws(tmp_path / "b", ["composites", "block_model", "plan_collar", "plan_survey"])
    assert route(make_agent(tmp_path / "b", d2).data_report(), "evaluar_plan_sondajes").feasible


def test_status_report_appends_the_data_based_alternatives(tmp_path):
    d = make_ws(tmp_path, ["collar", "survey", "assay", "block_model"])
    ag = make_agent(tmp_path, d)
    st = ag.plan_from(PlannerOutput(intent="status", tools=["estimar"]), "revisa los prerrequisitos de estimar")
    text = ag.report(st)
    assert "Con tus datos actuales" in text and "Estimar desde sondajes" in text


def test_viability_intent_is_informational_and_filters_routes_by_the_tool(tmp_path):
    d = make_ws(tmp_path, ["composites", "block_model"])
    ag = make_agent(tmp_path, d)
    st = ag.plan("¿es viable estimar con mis datos?")
    assert st.status == "info" and st.planner.intent == "viability"
    text = ag.report(st)
    assert "Sí es viable" in text and "Datos encontrados" in text
    assert "Evaluar un plan" not in text.split("###")[0]            # el titular solo habla de las rutas de estimar


# ------------------------------------------------------------------------------------------------ base de datos y resultados
def test_database_is_inspected_read_only_and_lets_the_route_resume(tmp_path):
    d = make_ws(tmp_path, ["block_model"])
    ag = make_agent(tmp_path, d)
    db = datos.find_db(ag.ctx) or Path(json.loads(ag.ctx.cfg_path("conf_importar_bd.json").read_text(encoding="utf-8-sig"))["proyecto"])
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE assay (Sondaje TEXT, Desde REAL, Hasta REAL, CUPCT REAL, CUPCT_ACOT REAL, DOMCU INTEGER)")
    con.execute("INSERT INTO assay VALUES ('A',0,2,0.3,0.3,21)")
    con.execute("CREATE TABLE assay_comp (Sondaje TEXT, MIDX REAL)")
    con.execute("INSERT INTO assay_comp VALUES ('A',1)")
    con.commit()
    con.close()
    before = db.read_bytes()
    rep = ag.data_report()
    assert {"db", "db_assay", "db_assay_coded", "db_assay_capped", "db_composites"} <= set(rep.found)
    assert rep.found["db_composites"].detail.startswith("tabla «assay_comp»: 1 filas")
    assert db.read_bytes() == before                                   # solo lectura
    r = route(rep, "estimar_desde_compositos")
    assert r.feasible and r.steps[0].state == "omitido"                # ya hay compósitos en la base: no hace falta importar


def test_existing_results_are_recognised(tmp_path):
    d = make_ws(tmp_path, ["composites", "block_model"])
    ag = make_agent(tmp_path, d)
    est = next(rp for rp in ag.ctx.resolve_all(ag.registry.get("estimar"), {}, roles=("output",)))
    est.path.parent.mkdir(parents=True, exist_ok=True)
    est.path.write_text("XC,YC\n1,2\n", encoding="utf-8")
    rep = ag.data_report()
    assert "model_estimated" in rep.found and tool(rep, "valorizar").state == "listo"


# ------------------------------------------------------------------------------------------------ conf inválido y cambio de ruta
def test_invalid_origin_value_is_flagged(tmp_path):
    d = make_ws(tmp_path, ["composites", "block_model"])
    ag = make_agent(tmp_path, d)                                       # los conf de prueba traen origen = «tablas»
    rep = ag.data_report()
    assert rep.conf_origin == "tablas" and any("no es válido" in n for n in rep.notes)


def test_apply_route_conf_is_textual_and_backed_up(tmp_path):
    d = make_ws(tmp_path, ["collar", "survey", "assay"])
    ag = make_agent(tmp_path, d)
    path = ag.ctx.cfg_path("conf_importar_bd.json")
    raw = path.read_bytes()
    r = next(r for r in ag.registry.routes if r.id == "estimar_desde_sondajes")
    log = datos.apply_route_conf(ag.ctx, r)
    after = path.read_bytes()
    assert json.loads(after.decode("utf-8-sig"))["origen"] == "sondajes"
    assert raw.replace(b'"origen": "tablas"', b'"origen": "sondajes"') == after      # solo cambió ese valor
    assert any("respaldo" in l for l in log)
    assert list((ag.ctx.config_dir / "_respaldo_agente").rglob("conf_importar_bd.json"))
    assert datos.apply_route_conf(ag.ctx, r) == ["conf_importar_bd.json: ya estaba así."]


# ------------------------------------------------------------------------------------------------ las 3 carpetas de prueba reales (solo lectura)
@pytest.mark.skipif(not (PRUEBAS / "Prueba_Agente_v1" / "01_Data").is_dir(), reason="no están las carpetas de prueba")
@pytest.mark.parametrize("v", ["v1", "v2", "v3"])
def test_the_real_test_folders_are_analysed_without_errors(tmp_path, v):
    """Humo sobre TUS carpetas de prueba (solo lectura). No afirma qué datos tienen: las vas completando y cambian;
    los escenarios concretos (solo compósitos, sin assay, sin modelo…) se prueban con carpetas sintéticas más arriba."""
    ag = make_agent(tmp_path, PRUEBAS / f"Prueba_Agente_{v}" / "01_Data")
    rep = ag.data_report()
    assert rep.n_files > 0 and len(rep.routes) >= 3
    assert all(r.steps for r in rep.routes)
    assert datos.headline(rep, ag.registry)


def test_catalog_declares_a_consistent_data_model():
    reg = ToolRegistry.load([CAT])
    assert len(reg.routes) >= 3 and "composites" in reg.data_kinds
    assert all(t.data for t in reg), [t.id for t in reg if not t.data]
    # cada tipo de dato que una herramienta necesita lo aporta el usuario (detect) o lo produce otra herramienta
    produced = {g for t in reg for a in t.data.alternatives for g in a.gives}
    for t in reg:
        for a in t.data.alternatives:
            for n in a.needs:
                assert reg.data_kinds[n].detect or n in produced, (t.id, n)


# ------------------------------------------------------------------------------------------------ v3.1 · evaluar un plan con compósitos (caso reportado)
def set_origin(ag: Agent, value: str) -> None:
    p = ag.ctx.cfg_path("conf_importar_bd.json")
    conf = json.loads(p.read_text(encoding="utf-8-sig"))
    conf["origen"] = value
    p.write_text(json.dumps(conf), encoding="utf-8")
    ag.ctx.invalidate()


def plan_wf(ag: Agent):
    return ag.plan_from(PlannerOutput(intent="run", workflow="evaluar_plan_sondajes"), "evalúa el plan de sondajes")


PLAN_ONLY_COMPOSITES = ["composites", "block_model", "plan_collar", "plan_survey"]


def test_plan_route_offers_the_origin_the_data_allows_and_can_apply_it(tmp_path):
    """La ruta «evaluar un plan» también ofrece «Usar esta ruta»: el origen que le corresponde sale de los datos (compósitos)."""
    ag = make_agent(tmp_path, make_ws(tmp_path, PLAN_ONLY_COMPOSITES))            # el conf trae origen = «tablas» (inválido)
    rs = route(ag.data_report(), "evaluar_plan_sondajes")
    assert rs.feasible and rs.conf == {"conf_importar_bd.json": {"origen": "compositos"}} and rs.conf_state == "distinta"
    log = datos.apply_route_conf(ag.ctx, rs)
    assert any("origen" in line for line in log)
    rs = route(ag.data_report(), "evaluar_plan_sondajes")
    assert rs.conf_state == "actual" and datos.conf_origin(ag.ctx) == "compositos"


def test_plan_route_with_only_raw_boreholes_asks_for_origin_sondajes(tmp_path):
    ag = make_agent(tmp_path, make_ws(tmp_path, ["collar", "survey", "assay", "block_model", "plan_collar", "plan_survey"]))
    rs = route(ag.data_report(), "evaluar_plan_sondajes")
    assert rs.feasible and rs.conf == {"conf_importar_bd.json": {"origen": "sondajes"}}
    assert [s.state for s in rs.steps if s.tool_id in ("codificar", "acotar", "compositar")] == ["ok", "ok", "ok"]


def test_plan_route_keeps_the_current_origin_when_both_are_possible(tmp_path):
    ag = make_agent(tmp_path, make_ws(tmp_path, ["collar", "survey", "assay", "composites", "block_model", "plan_collar", "plan_survey"]))
    set_origin(ag, "sondajes")
    rs = route(ag.data_report(), "evaluar_plan_sondajes")
    assert rs.feasible and rs.conf_state == "actual" and rs.conf["conf_importar_bd.json"]["origen"] == "sondajes"
    set_origin(ag, "compositos")
    assert route(ag.data_report(), "evaluar_plan_sondajes").conf_state == "actual"


def test_preflight_explains_that_the_data_allow_composites_instead_of_asking_for_assay(tmp_path):
    """El caso reportado: pide evaluar el plan, el conf está en sondajes/«tablas» y la carpeta solo tiene compósitos."""
    ag = make_agent(tmp_path, make_ws(tmp_path, PLAN_ONLY_COMPOSITES))
    st = plan_wf(ag)
    codes = {f.code for f in st.findings}
    assert "INPUT_MISSING" in codes and {"CFG_ORIGIN_INVALID", "ORIGIN_ALTERNATIVE"} <= codes
    alt = next(f for f in st.findings if f.code == "ORIGIN_ALTERNATIVE")
    assert alt.fix.kind == "edit_config" and alt.fix.detail["values"] == {"origen": "compositos"} and "assay" not in alt.message.lower()
    # nada cambia solo: el conf sigue igual hasta que el usuario acepte
    assert datos.conf_origin(ag.ctx) == "tablas"
    ag.apply_conf_fix(alt.fix, st.run_id)
    st = ag.prepare(st)
    assert datos.conf_origin(ag.ctx) == "compositos"
    assert not [f for f in st.errors() if f.tool == "importar" and f.code != "SCRIPT_MISSING"]          # (el sandbox no trae los scripts)
    assert not [f for f in st.findings if f.code.startswith(("ORIGIN", "CFG_ORIGIN"))]


def test_valid_origin_sondajes_without_assay_also_gets_the_alternative(tmp_path):
    ag = make_agent(tmp_path, make_ws(tmp_path, PLAN_ONLY_COMPOSITES))
    set_origin(ag, "sondajes")
    st = plan_wf(ag)
    codes = {f.code for f in st.findings}
    assert "ORIGIN_ALTERNATIVE" in codes and "CFG_ORIGIN_INVALID" not in codes


def test_no_alternative_is_offered_when_the_data_do_not_allow_it(tmp_path):
    ag = make_agent(tmp_path, make_ws(tmp_path, ["block_model"]))
    set_origin(ag, "sondajes")
    assert "ORIGIN_ALTERNATIVE" not in {f.code for f in plan_wf(ag).findings}


def test_plan_workflow_builds_composites_only_when_the_import_is_from_boreholes(tmp_path):
    ag = make_agent(tmp_path, make_ws(tmp_path, PLAN_ONLY_COMPOSITES))
    set_origin(ag, "compositos")
    ids = [s.tool_id for s in plan_wf(ag).plan.steps]
    assert ids[:2] == ["importar", "plan"] and not {"codificar", "acotar", "compositar"} & set(ids)
    set_origin(ag, "sondajes")
    ids = [s.tool_id for s in plan_wf(ag).plan.steps]
    assert ids[:5] == ["importar", "codificar", "acotar", "compositar", "plan"]
