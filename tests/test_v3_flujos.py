"""v3.1 · flujo «estimar desde sondajes (con modelo recortado)», conf que exige un flujo, Plan_Resumen opcional y
encadenado de modelos (lo que deja un paso es lo que lee el siguiente)."""

import json
from pathlib import Path

from agente import datos, jsonedit, rules
from agente.catalog import ToolRegistry
from agente.models import PlannerOutput
from test_v3_coherencia import edit, make_agent

CAT = Path(__file__).resolve().parents[1] / "catalog" / "tools_idw.yaml"
USER_FLOW = ["importar", "codificar", "acotar", "compositar", "modelo", "recortar", "estimar", "valorizar", "categorizar", "reporte"]


def wf(ag, name="estimar_desde_sondajes_recortado"):
    return ag.plan_from(PlannerOutput(intent="run", workflow=name), "flujo")


def codes(st, code):
    return [f for f in st.findings if f.code == code]


# ------------------------------------------------------------------------------------------------ léxico
def test_the_reported_phrase_selects_the_new_workflow_and_not_a_step_range():
    reg = ToolRegistry.load([CAT])
    for q in ["Realiza la estimacion desde sondajes de un modelo recortado", "estima desde sondajes con un modelo recortado",
              "quiero la estimación desde los sondajes y recortar el modelo"]:
        o = rules.suggest(q, reg)
        assert o.workflow == "estimar_desde_sondajes_recortado" and not o.from_tool and not o.to_tool, q
    o = rules.suggest("haz la estimación desde sondajes", reg)
    assert o.workflow == "estimar_desde_sondajes"


def test_explicit_step_ranges_still_work():
    reg = ToolRegistry.load([CAT])
    o = rules.suggest("corre desde estimar hasta categorizar", reg)
    assert (o.workflow, o.from_tool, o.to_tool) == (None, "estimar", "categorizar")
    assert rules.suggest("evalúa el plan de sondajes", reg).workflow == "evaluar_plan_sondajes"


def test_keyword_gap_token():
    r = rules._kw_regex("estim* ~ sondaje* ~ recortad*")
    assert r.search("la estimacion desde sondajes de un modelo recortado") and not r.search("estimacion de sondajes")


# ------------------------------------------------------------------------------------------------ el flujo
def test_workflow_needs_boreholes_import_and_block_model_creation_and_offers_to_set_them(tmp_path):
    ag = make_agent(tmp_path)                                          # los conf traen crear_modelo_bloques = false y origen «tablas»
    st = wf(ag)
    f = next(f for f in codes(st, "WORKFLOW_CONF") if "conf_proyecto.json" in f.message)
    assert f.severity == "error" and f.fix.kind == "edit_config" and f.fix.detail["values"] == {"crear_modelo_bloques": True}
    assert "modelo" not in [s.tool_id for s in st.plan.steps]            # hoy se omitiría en silencio
    for g in [g for g in st.findings if g.code == "WORKFLOW_CONF"]:
        ag.apply_conf_fix(g.fix, st.run_id)
    st = ag.prepare(st)
    assert [s.tool_id for s in st.plan.steps] == [t for t in USER_FLOW]   # exactamente el flujo pedido, en ese orden
    assert codes(st, "WORKFLOW_CONF") == []


def test_workflow_does_not_pull_the_boreholes_plan_and_offers_to_drop_the_gain_sheet(tmp_path):
    ag = make_agent(tmp_path)
    st = wf(ag)
    assert "plan" not in [s.tool_id for s in st.plan.steps]
    assert any(o.tool_id == "plan" and "no lo incluye" in o.reason for o in st.plan.omitted)
    f = next(f for f in codes(st, "CHAIN_PRODUCER_SKIPPED") if f.tool == "reporte")
    assert f.fix.kind == "edit_config" and f.fix.detail["values"] == {"ganancia.activo": False}
    ag.apply_conf_fix(f.fix, st.run_id)
    assert json.loads(ag.ctx.cfg_path("conf_reporte.json").read_text(encoding="utf-8-sig"))["ganancia"]["activo"] is False
    st = ag.prepare(st)
    assert [x for x in codes(st, "CHAIN_PRODUCER_SKIPPED") if x.tool == "reporte"] == []


def test_nested_conf_edit_changes_only_that_line(tmp_path):
    ag = make_agent(tmp_path)
    p = ag.ctx.cfg_path("conf_reporte.json")
    before = p.read_text(encoding="utf-8-sig")
    log = datos.apply_route_conf(ag.ctx, {"conf_reporte.json": {"ganancia.activo": False}})
    after = p.read_text(encoding="utf-8-sig")
    assert any("respaldo" in x for x in log)
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(diff) == 1 and "true" in diff[0][0] and "false" in diff[0][1] and before.count("\n") == after.count("\n")
    assert datos.apply_route_conf(ag.ctx, {"conf_reporte.json": {"ganancia.activo": False}}) == ["conf_reporte.json: ya estaba así."]


def test_textual_json_edit_edge_cases():
    t = '{\n  "a": {"activo": 1},\n  "g": {\n    "activo": true,\n    "s": "x \\" } y"\n  },\n  "activo": true\n}'
    assert json.loads(jsonedit.set_value_textual(t, "g.activo", False))["g"]["activo"] is False
    assert json.loads(jsonedit.set_value_textual(t, "g.activo", False))["a"]["activo"] == 1
    assert json.loads(jsonedit.set_value_textual(t, "activo", False))["activo"] is False
    assert jsonedit.set_value_textual(t, "g.no_existe", 1) is None and jsonedit.set_value_textual(t, "a", 1) is None


# ------------------------------------------------------------------------------------------------ encadenado de modelos
def prepare_workflow(ag):
    st = wf(ag)
    for g in [g for g in st.findings if g.code == "WORKFLOW_CONF"]:
        ag.apply_conf_fix(g.fix, st.run_id)
    return ag.prepare(st)


def test_created_block_model_must_be_the_one_that_gets_clipped(tmp_path):
    ag = make_agent(tmp_path)                                          # conf_recorte lee 01_Data/…/mb_smeb.csv; el flujo crea otro en 02_Resultados
    st = prepare_workflow(ag)
    f = next(f for f in codes(st, "MODEL_CHAIN_BROKEN") if f.tool == "recortar")
    assert f.severity == "error" and f.fix.kind == "edit_config" and "modelo" in f.fix.detail["values"]
    assert "02_Resultados" in f.fix.detail["values"]["modelo"]
    assert not [x for x in st.findings if x.code == "INPUT_MISSING" and x.tool == "recortar" and "mb_smeb.csv" in x.message]   # sin modelo de ejemplo falso
    ag.apply_conf_fix(f.fix, st.run_id)
    st = ag.prepare(st)
    assert codes(st, "MODEL_CHAIN_BROKEN") == []
    assert any(x.code == "INPUT_WILL_BE_PRODUCED" and x.tool == "recortar" for x in st.findings)


def test_skipping_valorization_offers_to_chain_categorization_to_the_estimated_model(tmp_path):
    ag = make_agent(tmp_path)
    edit(ag, "conf_proyecto.json", lambda d: d.update(valorizar_modelo=False))
    st = ag.plan_from(PlannerOutput(intent="run", from_tool="estimar", to_tool="reporte"), "estimar a reporte")
    assert "valorizar" not in [s.tool_id for s in st.plan.steps]
    f = next(f for f in codes(st, "MODEL_CHAIN_BROKEN") if f.tool == "categorizar")
    assert "estimado" in f.fix.detail["values"]["modelo"]


def test_repeated_solids_variable_is_reported_once(tmp_path):
    ag = make_agent(tmp_path)
    st = prepare_workflow(ag)
    a = [f for f in codes(st, "COLUMN_MISSING") if f.tool == "modelo"]
    assert len(a) == 1 and "otras" in a[0].message and "domcu" in a[0].message


# ------------------------------------------------------------------------------------------------ valores por defecto del modelo nuevo
def new_model_state(ag):
    st = prepare_workflow(ag)
    chain = next(f for f in codes(st, "MODEL_CHAIN_BROKEN") if f.tool == "recortar")
    ag.apply_conf_fix(chain.fix, st.run_id)
    return ag.prepare(st)


def cfg(ag, name):
    return json.loads(ag.ctx.cfg_path(name).read_text(encoding="utf-8-sig"))


def test_new_model_missing_columns_get_one_notice_with_the_default_values(tmp_path):
    ag = make_agent(tmp_path)
    st = new_model_state(ag)
    f = codes(st, "COLUMN_DEFAULTS")
    assert len(f) == 1 and f[0].severity == "warn" and f[0].fix.kind == "edit_config"
    msg = f[0].message
    for needle in ("bearing", "plunge", "dip", "pasa a 0", "2.65", "piso", "topof", "100", "comparación"):
        assert needle in msg, needle
    # ya no hay una alerta suelta por cada una de esas columnas
    assert not [x for x in codes(st, "COLUMN_MISSING") if x.tool in ("estimar", "valorizar", "categorizar", "reporte") and "domcu" not in x.message]
    # nada cambia solo
    assert cfg(ag, "conf_valorizacion.json")["asignar_densidad"] is False


def test_applying_the_defaults_sets_exactly_what_was_announced(tmp_path):
    ag = make_agent(tmp_path)
    st = new_model_state(ag)
    f = codes(st, "COLUMN_DEFAULTS")[0]
    log = ag.apply_conf_fix(f.fix, st.run_id)
    assert all("respaldo" in line for line in log) and len(log) == 5              # 5 conf cambiados
    est = cfg(ag, "conf_estimacion.json")["pasadas"]
    assert {p["Bearing"] for p in est} == {"0"} and {p["Plunge"] for p in est} == {"0"} and {p["Dip"] for p in est} == {"0"}
    assert cfg(ag, "conf_valorizacion.json")["asignar_densidad"] is True and cfg(ag, "conf_valorizacion.json")["densidad"] == 2.65
    assert cfg(ag, "conf_categ.json")["piso"]["activo"] is False
    assert cfg(ag, "conf_reporte.json")["comparacion"]["activo"] is False
    topof = [v for v in cfg(ag, "conf_crear_mb.json")["variables"] if v["nombre"] == "topof"]
    assert len(topof) == 1 and topof[0]["valor_defecto"] == "100" and topof[0]["tipo"] == "Float"
    st = ag.prepare(st)
    assert codes(st, "COLUMN_DEFAULTS") == []
    assert not [x for x in codes(st, "COLUMN_MISSING") if x.tool in ("estimar", "valorizar", "categorizar", "reporte") and "domcu" not in x.message]
    ag.apply_conf_fix(f.fix, st.run_id)                                            # idempotente: no duplica topof
    assert len([v for v in cfg(ag, "conf_crear_mb.json")["variables"] if v["nombre"] == "topof"]) == 1


def test_defaults_are_only_for_new_models_an_existing_model_keeps_the_plain_alert(tmp_path):
    ag = make_agent(tmp_path)
    from test_v3_coherencia import put_model
    put_model(ag, "conf_estimacion.json", cols="XC,YC,ZC,DX,DY,DZ,domcu,cu_idw")       # modelo existente sin lva_*
    st = ag.plan_from(PlannerOutput(intent="run", tools=["estimar"]), "estimar")
    assert codes(st, "COLUMN_DEFAULTS") == []
    assert any("lva_b" in f.message for f in codes(st, "COLUMN_MISSING"))


def test_csv_tables_changed_by_the_defaults_differ_on_purpose_and_only_inform(tmp_path):
    ag = make_agent(tmp_path)
    from agente import tablas
    pt = ag.registry.param_tables["estimacion_pasadas"]
    csv_text = tablas.export_current(ag.ctx, pt)                                      # el CSV del usuario conserva lva_b
    d = ag.ctx.anchor_dir("01_Data") / "04_Parametros_Entrada"
    d.mkdir(parents=True, exist_ok=True)
    (d / "estimacion_pasadas.csv").write_text(csv_text, encoding="utf-8")
    st = new_model_state(ag)
    assert not [f for f in codes(st, "TABLE_NOT_LOADED") if f.tool == "estimar"]       # iguales: sin aviso
    ag.apply_conf_fix(codes(st, "COLUMN_DEFAULTS")[0].fix, st.run_id)
    st = ag.prepare(st)
    f = next(f for f in codes(st, "TABLE_NOT_LOADED") if f.tool == "estimar")
    assert f.severity == "info" and "Es esperado" in f.message
