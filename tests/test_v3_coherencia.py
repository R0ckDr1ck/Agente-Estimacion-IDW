"""v3.1 · el agente avisa ANTES de ejecutar de lo que haría fallar un paso a mitad del flujo:
columnas que un paso nombra y su modelo no tendrá, y tablas CSV de parámetros que difieren del conf (editadas y no cargadas).

La carpeta `fixtures/confs_mixtos/` reproduce el caso real: la estimación produce «cu_idw» pero la valorización y el reporte
siguen apuntando a «au_idw» (valorizar fallaba con «El modelo de bloques no tiene la(s) columna(s): ley de 'au' ('au_idw')»)."""

import json
import shutil
from pathlib import Path

from agente import coherencia, tablas
from agente.config import AgentConfig
from agente.models import PlannerOutput
from agente.orchestrator import Agent

FIX = Path(__file__).parent / "fixtures" / "confs_mixtos"
MODEL_COLS = "XC,YC,ZC,DX,DY,DZ,domcu,lva_b,lva_d,lva_p,topof,densidad,cu_idw,categ"


def make_agent(tmp_path: Path) -> Agent:
    root = tmp_path / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    for f in FIX.glob("conf_*.json"):
        shutil.copy(f, root / "Configuraciones" / f.name)
    d, o = tmp_path / "in" / "01_Data", tmp_path / "out" / "02_Resultados"
    d.mkdir(parents=True)
    o.mkdir(parents=True)
    cfg = AgentConfig.load(root=root, data_dir=d, results_dir=o)
    cfg.runs_dir, cfg.templates_dir, cfg.llm.enabled = tmp_path / "runs", tmp_path / "tpl", False
    ag = Agent(cfg)
    ag.align_paths_to_workspace()
    return ag


def edit(ag: Agent, conf: str, fn) -> None:
    p = ag.ctx.cfg_path(conf)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    fn(data)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    ag.ctx.invalidate()


def put_model(ag: Agent, conf: str, key: str = "modelo", cols: str = MODEL_COLS) -> Path:
    p = Path(json.loads(ag.ctx.cfg_path(conf).read_text(encoding="utf-8-sig"))[key])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cols + "\n1,2,3\n", encoding="utf-8")
    return p


def plan(ag: Agent, *tools: str):
    return ag.plan_from(PlannerOutput(intent="run", tools=list(tools)), "prueba")


def alerts(st, code="COLUMN_MISSING"):
    return [f for f in st.findings if f.code == code]


def fix_all_to_cu(ag: Agent) -> None:
    for n in ("conf_valorizacion.json", "conf_reporte.json"):
        p = ag.ctx.cfg_path(n)
        p.write_text(p.read_text(encoding="utf-8-sig").replace("au_idw", "cu_idw").replace('"au"', '"cu"'), encoding="utf-8")
    ag.ctx.invalidate()


# ------------------------------------------------------------------------------------------------ columnas entre pasos
def test_the_reported_failure_is_detected_before_running(tmp_path):
    ag = make_agent(tmp_path)
    put_model(ag, "conf_estimacion.json")
    st = plan(ag, "estimar", "valorizar")
    a = [f for f in alerts(st) if f.tool == "valorizar"]
    assert a, "la valorización usa au_idw pero la estimación solo produce cu_idw"
    msg = " ".join(f.message for f in a)
    assert "au_idw" in msg and "metales[0].columna" in msg and "valorizar" in msg
    assert any("cu_idw" in f.hint for f in a)                         # sugiere la columna que sí existe
    assert any("valorizacion_metales.csv" in f.hint for f in a)       # y dónde corregirlo
    assert all(f.severity == "warn" for f in a)                       # alerta, no bloqueo


def test_expressions_are_checked_too_and_pct_rec_columns_are_known(tmp_path):
    ag = make_agent(tmp_path)
    put_model(ag, "conf_estimacion.json")
    fix_all_to_cu(ag)
    edit(ag, "conf_valorizacion.json", lambda d: d.update(exp_nsr="cu_idw*100*pct_rec_cu - zn_idw", exp_equiv="cu_idw"))
    msgs = [f.message for f in alerts(plan(ag, "estimar", "valorizar"))]
    assert len(msgs) == 1 and "zn_idw" in msgs[0] and "exp_nsr" in msgs[0]       # pct_rec_cu la crea el propio paso


def test_consistent_confs_raise_no_alert_for_the_whole_chain(tmp_path):
    ag = make_agent(tmp_path)
    put_model(ag, "conf_estimacion.json")
    fix_all_to_cu(ag)
    edit(ag, "conf_valorizacion.json", lambda d: d.update(exp_nsr="cu_idw*100", exp_equiv="cu_idw"))
    edit(ag, "conf_reporte.json", lambda d: d["columnas"].update(coboEquiv="equiv"))
    assert alerts(plan(ag, "estimar", "valorizar", "categorizar", "reporte")) == []


def test_columns_added_by_estimation_follow_the_variable_name(tmp_path):
    ag = make_agent(tmp_path)
    put_model(ag, "conf_estimacion.json")
    fix_all_to_cu(ag)
    edit(ag, "conf_estimacion.json", lambda d: [p.update(Variable="zz_idw") for p in d["pasadas"]])
    msgs = " ".join(f.message for f in alerts(plan(ag, "estimar", "valorizar", "categorizar")))
    assert "cu_idw_nholes" in msgs and "cu_idw_pass" in msgs                  # categorizar espera las columnas de cu_idw
    assert "d3h_avgdist_anisot" not in msgs                                    # esa sí la agrega la estimación


def test_model_header_on_disk_is_used_when_no_previous_step_builds_it(tmp_path):
    ag = make_agent(tmp_path)
    fix_all_to_cu(ag)
    assert alerts(plan(ag, "valorizar")) == []                                 # el modelo estimado no existe: no se puede verificar
    put_model(ag, "conf_valorizacion.json", cols="XC,YC,ZC,au_x,densidad")
    msgs = " ".join(f.message for f in alerts(plan(ag, "valorizar")))
    assert "cu_idw" in msgs and "mb_smeb_estimado.csv" in msgs


def test_block_model_created_in_the_plan_defines_the_columns(tmp_path):
    ag = make_agent(tmp_path)                                                  # conf_crear_mb: variables au_idw/envau, sólidos codifican «domcu»
    msgs = " ".join(f.message for f in alerts(plan(ag, "modelo")))
    assert "domcu" in msgs and "solidos[" in msgs


def test_names_in_expressions():
    assert coherencia.names_in_expression("28.877*ag_idw if ag_idw < 2.8 else 4.22*math.log(ag_idw)+76.51") == ["ag_idw"]
    assert set(coherencia.names_in_expression("(ag_idw*13.9*pct_rec_ag + pb_idw*1e-3)/100")) == {"ag_idw", "pct_rec_ag", "pb_idw"}
    assert coherencia.names_in_expression("f.ley * f.dens") == ["ley", "dens"]


# ------------------------------------------------------------------------------------------------ tablas sin cargar
def write_csv(ag: Agent, name: str, text: str) -> Path:
    p = ag.ctx.anchor_dir("01_Data") / "04_Parametros_Entrada" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("﻿" + text, encoding="utf-8")
    return p


def test_csv_edited_but_not_loaded_is_reported_with_a_load_button(tmp_path):
    ag = make_agent(tmp_path)
    write_csv(ag, "valorizacion_metales.csv", "Clave,Columna de ley,Expresión de recuperación\ncu,cu_idw,1\n")
    st = plan(ag, "valorizar")
    f = next(f for f in alerts(st, "TABLE_NOT_LOADED") if f.tool == "valorizar")
    assert f.severity == "warn" and "au_idw" in f.message and "cu_idw" in f.message
    assert f.fix.kind == "load_table" and f.fix.detail["table"] == "valorizacion_metales"
    ag.apply_table_fix(f.fix, st.run_id)
    assert json.loads(ag.ctx.cfg_path("conf_valorizacion.json").read_text(encoding="utf-8-sig"))["metales"][0]["columna"] == "cu_idw"
    st = ag.prepare(st)
    assert [x for x in alerts(st, "TABLE_NOT_LOADED") if x.tool == "valorizar"] == []          # ya coincide


def test_identical_csv_raises_nothing_and_tables_of_other_steps_are_only_informative(tmp_path):
    ag = make_agent(tmp_path)
    pt = ag.registry.param_tables["valorizacion_metales"]
    write_csv(ag, "valorizacion_metales.csv", tablas.export_current(ag.ctx, pt).lstrip("﻿"))
    write_csv(ag, "reporte_metales.csv", "Columna de ley,Etiqueta,Unidad,Factor\ncu_idw,Cu,pct,1\n")
    got = {(f.tool, f.severity) for f in alerts(plan(ag, "valorizar"), "TABLE_NOT_LOADED")}
    assert ("valorizar", "warn") not in got and ("reporte", "info") in got          # el reporte no está en el plan: solo informa


def test_table_with_numbers_is_compared_by_value(tmp_path):
    ag = make_agent(tmp_path)
    pt = ag.registry.param_tables["acotamiento_topes"]
    cur = tablas.export_current(ag.ctx, pt).lstrip("﻿")
    write_csv(ag, "acotamiento_topes.csv", cur.replace("3.15", "3,150"))                # 3,150 == 3.15
    assert [f for f in alerts(plan(ag, "acotar"), "TABLE_NOT_LOADED") if f.tool == "acotar"] == []
    write_csv(ag, "acotamiento_topes.csv", cur.replace("3.15", "3.2", 1))
    assert [f for f in alerts(plan(ag, "acotar"), "TABLE_NOT_LOADED") if f.tool == "acotar"]
