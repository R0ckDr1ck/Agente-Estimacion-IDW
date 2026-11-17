"""Funciones nuevas de la v2: tablas CSV→conf, guía de formatos, carpetas de trabajo, lector del reporte, métricas, evaluación.

Todo trabaja sobre COPIAS en tmp_path (los conf, scripts y CSV reales solo se leen)."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from agente import evaluation, formats, metrics, results, rules, tablas, workspace
from agente.catalog import ToolRegistry
from agente.context import ProjectContext
from agente.llm import MockLLM
from agente.orchestrator import Agent
from agente.planner import Planner
from conftest import BASE, IDW_CATALOG, ScriptedIO, make_cfg

REAL = BASE / "Proyecto_IDW"
real_only = pytest.mark.skipif(not (REAL / "Configuraciones").is_dir(), reason="falta Proyecto_IDW")


@pytest.fixture
def proj(tmp_path):
    """Proyecto IDW ligero: conf + scripts reales copiados; carpetas de entrada/salida vacías aparte."""
    root = tmp_path / "idw"
    shutil.copytree(REAL / "Configuraciones", root / "Configuraciones", ignore=shutil.ignore_patterns("_respaldo_agente"))
    shutil.copytree(REAL / "Flujo_Estimacion", root / "Flujo_Estimacion", ignore=shutil.ignore_patterns("__pycache__"))
    data, out = tmp_path / "datos", tmp_path / "salida"
    (data / "04_Parametros_Entrada").mkdir(parents=True)
    out.mkdir()
    return root, data, out


def make_agent(tmp_path, root, data=None, out=None) -> Agent:
    cfg = make_cfg(tmp_path, root, IDW_CATALOG)
    if data:
        cfg.set_workspace(data, out)
    return Agent(cfg, io=ScriptedIO())


# ====================================================================================== tablas CSV → conf
@real_only
def test_every_param_table_roundtrips_the_real_conf_exactly(proj, tmp_path):
    """Exportar la tabla desde el conf real y volver a cargarla debe reproducir EXACTAMENTE el valor original:
    prueba que el mapeo del catálogo equivale al de la interfaz de escritorio que reemplaza."""
    root, _, _ = proj
    agent = make_agent(tmp_path, root)
    assert len(agent.registry.param_tables) == 8
    for pt in agent.registry.param_tables.values():
        original = agent.ctx.load_cfg(pt.config)[0][pt.target]
        text = tablas.export_current(agent.ctx, pt)
        assert text, pt.id
        header, rows = tablas.parse_csv(text)
        load = tablas.load_table(pt, header, rows)
        assert load.ok and not load.errors, (pt.id, load.errors)
        assert load.value == original, pt.id


def test_load_table_reports_bad_numbers_missing_columns_and_unknown_columns(tmp_path):
    reg = ToolRegistry.load([IDW_CATALOG])
    pt = reg.param_tables["acotamiento_topes"]
    load = tablas.load_table(pt, ["Variable", "Dominio", "Tope", "Extra"], [["CUPCT", "21", "abc", "x"], ["CUPCT", "22", "3,5", "y"]])
    assert any("«Tope» debe ser un número" in e for e in load.errors) and not load.ok
    assert any("«Extra»" in w for w in load.warnings) and any("Método" in w for w in load.warnings)
    ok = tablas.load_table(pt, ["variable", "DOMINIO", "tope"], [["CUPCT", "22", "3,5"]])          # coma decimal, sin tildes/mayúsculas
    assert ok.ok and ok.value == {"CUPCT": {"22": {"tope": 3.5, "metodo": "manual"}}}
    none = tablas.load_table(pt, ["a", "b"], [["1", "2"]])
    assert none.errors and "Ninguna columna coincide" in none.errors[0]


def test_rows_missing_required_columns_are_skipped_with_a_warning():
    reg = ToolRegistry.load([IDW_CATALOG])
    pt = reg.param_tables["codificacion_solidos"]
    load = tablas.load_table(pt, ["Ruta del sólido (.obj)", "Valor", "Prioridad"], [["a.obj", "21", ""], ["", "31", "1"]])
    assert load.value == [{"ruta": "a.obj", "valor": "21", "prioridad": 1}] and any("Línea 3" in w for w in load.warnings)


def test_templates_are_loadable_by_their_own_loader():
    """La plantilla que se descarga debe poder cargarse sin errores (cabecera + filas de ejemplo)."""
    reg = ToolRegistry.load([IDW_CATALOG])
    for pt in reg.param_tables.values():
        h, rows = tablas.parse_csv(tablas.decode_csv(tablas.render_table_template(pt).encode("utf-8-sig")))
        assert h == [c.header for c in pt.columns]
        assert tablas.load_table(pt, h, rows).ok, pt.id


@real_only
def test_apply_table_backs_up_preserves_other_keys_and_bom(proj, tmp_path):
    root, _, _ = proj
    agent = make_agent(tmp_path, root)
    pt = agent.registry.param_tables["valorizacion_metales"]
    path = agent.ctx.cfg_path(pt.config)
    before = json.loads(path.read_text(encoding="utf-8-sig"))
    h, r = tablas.parse_csv("Clave;Columna de ley;Expresión de recuperación\ncu;cu_idw;0.85\nag;ag_idw;0.5\n")     # punto y coma, como Excel regional
    load = tablas.load_table(pt, h, r)
    log = tablas.apply_table(agent.ctx, load)
    after = json.loads(path.read_text(encoding="utf-8-sig"))
    assert [m["clave"] for m in after["metales"]] == ["cu", "ag"] and after["metales"][0]["exp_recuperacion"] == "0.85"
    assert {k: v for k, v in after.items() if k != "metales"} == {k: v for k, v in before.items() if k != "metales"}
    backups = list((root / "Configuraciones" / "_respaldo_agente").rglob(pt.config))
    assert backups and json.loads(backups[0].read_text(encoding="utf-8-sig")) == before and "Respaldo" in log[1]
    with pytest.raises(tablas.TableError):
        tablas.apply_table(agent.ctx, tablas.load_table(pt, ["Clave"], []))                          # sin filas: no escribe


@real_only
def test_save_conf_edit_keeps_tables_and_refuses_to_change_the_module(proj, tmp_path):
    root, _, _ = proj
    agent = make_agent(tmp_path, root)
    ctx = agent.ctx
    hidden = {"solidos"}
    view = tablas.editable_view(ctx, "conf_codificar.json", hidden)
    assert "solidos" not in view and view["campo"]
    n_before = len(ctx.load_cfg("conf_codificar.json")[0]["solidos"])
    view["valor_defecto"] = "-77"
    tablas.save_conf_edit(ctx, "conf_codificar.json", json.dumps(view), hidden)
    now = ctx.load_cfg("conf_codificar.json")[0]
    assert now["valor_defecto"] == "-77" and len(now["solidos"]) == n_before            # la tabla se conservó
    with pytest.raises(tablas.TableError, match="no es válido"):
        tablas.save_conf_edit(ctx, "conf_codificar.json", "{ roto", hidden)
    view["__modulo__"] = "OtraCosa"
    with pytest.raises(tablas.TableError, match="__modulo__"):
        tablas.save_conf_edit(ctx, "conf_codificar.json", json.dumps(view), hidden)
    assert tablas.save_conf_edit(ctx, "conf_codificar.json", json.dumps({**view, "__modulo__": now["__modulo__"]}), hidden)[0].endswith("sin cambios.")


# ====================================================================================== guía de formatos
def _header(path: Path) -> list[str]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


@real_only
def test_format_templates_match_the_example_files_the_user_provided():
    """Las columnas de cada plantilla deben ser las de los archivos de ejemplo reales de 01_Data."""
    reg = ToolRegistry.load([IDW_CATALOG])
    data = REAL / "01_Data"
    checked = 0
    for ft in reg.file_templates.values():
        ex = data / ft.example_file
        if ft.example_file and ex.is_file():
            assert [c.name for c in ft.columns] == _header(ex), ft.id
            checked += 1
    for pt in reg.param_tables.values():
        ex = data / pt.folder / pt.file
        if ex.is_file():
            assert [c.header for c in pt.columns] == _header(ex), pt.id
            checked += 1
    if checked == 0:
        pytest.skip("no hay archivos de ejemplo en Proyecto_IDW/01_Data (los datos no se suben al repositorio)")
    assert checked >= 12


@real_only
def test_guide_lists_data_tables_and_configs_with_downloadable_content(proj, tmp_path):
    root, data, out = proj
    agent = make_agent(tmp_path, root, data, out)
    items = formats.build_guide(agent.ctx, agent.registry)
    kinds = {i.kind for i in items}
    assert kinds == {"data", "table", "config"}
    assert {i.id for i in items} >= {"data:ddh_collar", "data:block_model", "table:estimacion_pasadas", "config:conf_estimacion.json"}
    for i in items:
        if i.kind != "config" or i.content:
            assert i.content.strip() and i.filename and i.help_text, i.id
    cfgs = [i for i in items if i.kind == "config" and "plantilla" in i.filename]
    assert cfgs and all(json.loads(i.content)["_ayuda"] for i in cfgs)                    # plantillas con bloque de ayuda
    only = formats.build_guide(agent.ctx, agent.registry, ["acotar"])
    assert {i.id for i in only} == {"table:acotamiento_topes", "config:conf_acotamiento.json"}
    assert "acotamiento_topes.csv" in formats.render_guide_markdown(only)


@real_only
def test_config_template_defaults_follow_the_chosen_folders(proj, tmp_path):
    root, data, out = proj
    agent = make_agent(tmp_path, root, data, out)
    tool = agent.registry.get("compositar") if False else agent.registry.get("plan")
    items = [i for i in formats.build_guide(agent.ctx, agent.registry, ["plan"]) if i.id == "config:conf_plan.json"]
    doc = json.loads(items[0].content)
    assert doc["collar"]["archivo"].startswith(str(data)) and doc["salida"].startswith(str(out))


def test_rank_items_prefers_what_the_sentence_mentions():
    reg = ToolRegistry.load([IDW_CATALOG])
    cfg = make_cfg(Path("."), BASE / "examples" / "demo_project", IDW_CATALOG)
    items = formats.build_guide(ProjectContext(cfg, reg), reg)
    top = formats.rank_items(items, "no sé el formato del archivo de collares")
    assert {i.id for i in top} >= {"data:ddh_collar"} and len(top) < len(items)
    assert len(formats.rank_items(items, "qué se yo")) == len(items)                        # sin coincidencias: todo


# ====================================================================================== intención «formats»
@pytest.mark.parametrize("text", ["no sé el formato del archivo de collares", "dame una plantilla para llenar las pasadas",
                                  "qué columnas necesita el modelo de bloques", "necesito un ejemplo de los archivos que debo llenar"])
def test_rules_detect_the_formats_intent(text):
    reg = ToolRegistry.load([IDW_CATALOG])
    assert rules.suggest(text, reg).intent == "formats"
    assert rules.suggest("estima el modelo", reg).intent == "run"                           # no secuestra solicitudes normales


def test_model_saying_run_is_corrected_to_formats_by_the_lexicon(tmp_path):
    reg = ToolRegistry.load([IDW_CATALOG])
    cfg = make_cfg(tmp_path, BASE / "examples" / "demo_project", IDW_CATALOG)
    cfg.llm.enabled = True
    ans = json.dumps({"intent": "run", "tools": ["importar"], "confidence": 0.9, "params": {}})
    r = Planner(cfg, reg, primary=MockLLM([ans])).plan("no sé el formato del archivo de collares")
    assert r.output.intent == "formats"


@real_only
def test_formats_request_produces_an_info_state_with_the_guide(proj, tmp_path):
    root, data, out = proj
    agent = make_agent(tmp_path, root, data, out)
    st = agent.plan("no sé el formato del archivo de collares")
    assert st.status == "info" and st.planner.intent == "formats" and not st.plan.steps
    text = agent.report(st)
    assert "Collares de sondajes" in text and "ddh_collar.csv" in text


# ====================================================================================== carpetas de trabajo
def test_check_dirs_validates_creates_and_warns(tmp_path):
    data = tmp_path / "in"
    data.mkdir()
    (data / "x.csv").write_text("a", encoding="utf-8")
    assert not workspace.check_dirs("", "").ok
    assert any("no existe" in e for e in workspace.check_dirs(str(tmp_path / "nada"), str(tmp_path / "o")).errors)
    assert any("distintas" in e for e in workspace.check_dirs(str(data), str(data)).errors)
    assert any("rutas completas" in e for e in workspace.check_dirs("relativa", "otra").errors)
    out = tmp_path / "nuevo" / "salida"
    chk = workspace.check_dirs(str(data), str(out))
    assert chk.ok and out.is_dir() and chk.created and chk.data_dir == data.resolve()
    assert not workspace.check_dirs(str(data), str(tmp_path / "fuera"), create_results=False).ok
    assert workspace.inventory(data) == [("(archivos sueltos)", "1 .csv")]
    inside = workspace.check_dirs(str(data), str(data / "res"))
    assert inside.ok and any("dentro de la de entrada" in w for w in inside.warnings)


@real_only
def test_choosing_folders_relocates_conf_paths_with_backup_and_is_idempotent(proj, tmp_path):
    root, data, out = proj
    agent = make_agent(tmp_path, root, data, out)
    # un archivo de entrada en la carpeta elegida (con el mismo sufijo relativo que usa el conf)
    (data / "01_Database").mkdir()
    (data / "01_Database" / "assay_comp.csv").write_text("Sondaje,Desde,Hasta\nA,0,2\n", encoding="utf-8")
    log, ambiguous = agent.align_paths_to_workspace()
    assert log and any("Respaldo" in ln for ln in log)
    comp = json.loads((root / "Configuraciones" / "conf_comp.json").read_text(encoding="utf-8-sig"))
    assert comp["proyecto"].startswith(str(out)) and comp["proyecto"].endswith("Database.db")       # salida → carpeta de salida
    imp = json.loads((root / "Configuraciones" / "conf_importar_bd.json").read_text(encoding="utf-8-sig"))
    assert imp["compositos"]["archivo"] == str(data / "01_Database" / "assay_comp.csv")              # entrada → carpeta de entrada
    again, _ = agent.align_paths_to_workspace()
    assert again == []                                                                               # segunda vez: nada que cambiar
    assert (root / "Configuraciones" / "_respaldo_agente").is_dir()


@real_only
def test_path_params_must_stay_inside_the_chosen_folders(proj, tmp_path):
    root, data, out = proj
    agent = make_agent(tmp_path, root, data, out)
    assert agent.ctx.inside_root(data / "a.csv") and agent.ctx.inside_root(out / "x" / "y.db")
    assert not agent.ctx.inside_root(root / "Flujo_Estimacion" / "EjecutarFlujo.py")        # las herramientas no son datos
    st = agent.plan_from(__import__("agente.models", fromlist=["PlannerOutput"]).PlannerOutput(tools=["esquema_bloques"]),
                         "x", {"csv": str(tmp_path / "otro" / "m.csv")})
    assert any(f.code == "PARAM_FORBIDDEN_PATH" for f in st.findings)


# ====================================================================================== reporte final
def _fake_report(path: Path) -> Path:
    resumen = [["Reporte de Recursos", None, None, None], ["Unidad: BVN", None, None, None], ["Fecha: 15/11/2026", None, None, None],
               ["Resumen Recursos", None, None, "Cut-off (Cu): 0 pct"], [None] * 4,
               ["Dominio", "Categoria", "Tonelaje", "Cu"], [None, None, "t", "pct"],
               [61, "Medido", 100.0, 1.0], [None, "Indicado", 300.0, 2.0], [None, "Medido & Indicado", 400.0, 1.75], [None, "Inferido", 50.0, 0.5],
               [62, "Medido", 10.0, 3.0], [None, "Indicado", 20.0, 3.0], [None, "Medido & Indicado", 30.0, 3.0], [None, "Inferido", 0.0, 0.0],
               ["Total", "Medido", 110.0, 1.18], [None, "Indicado", 320.0, 2.06], [None, "Medido & Indicado", 430.0, 1.84], [None, "Inferido", 50.0, 0.5]]
    comp = [["Comparación de Categoría", None, None, None, None, None], ["Unidad: BVN", None, None, None, None, None], ["Fecha: x", None, None, None, None, None],
            ["Tonelaje por categoría", None, None, None, None, None], [None] * 6, ["Total", None, None, None, None, None],
            ["Dominio", "Categoria", "Anterior (t)", "Actual (t)", "Diferencia (t)", "Variación (%)"],
            ["Total", "Medido", 100.0, 110.0, 10.0, 0.1], [None, "Indicado", 300.0, 320.0, 20.0, 0.0667], [None, "Inferido", 60.0, 50.0, -10.0, -0.1667]]
    gan = [["Ganancia", None, None, None, None], ["Unidad: BVN", None, None, None, None], ["Fecha: x", None, None, None, None], ["x", None, None, None, None],
           [None] * 5, ["Plan de sondajes", None, None, None, None], ["N° de sondajes", 20, None, None, None], ["Longitud total del plan (m)", 4630, None, None, None],
           [None] * 5, ["Ganancia de recursos", None, None, None, None],
           ["Categoria", "Anterior (t)", "Actual (t)", "Ganancia (t)", "Variación (%)"],
           ["Medido & Indicado", 400.0, 430.0, 30.0, 0.075]]
    with pd.ExcelWriter(path) as xw:
        for name, rows in (("Resumen", resumen), ("Comparacion_Categ", comp), ("Ganancia_Plan", gan)):
            pd.DataFrame(rows).to_excel(xw, sheet_name=name, header=False, index=False)
    return path


def test_report_reader_extracts_totals_comparison_and_plan_gain(tmp_path):
    xlsx = _fake_report(tmp_path / "Recursos_test.xlsx")
    assert results.list_reports(tmp_path) == [xlsx]
    b = results.load_report(xlsx)
    assert b.meta == {"unidad": "BVN", "fecha": "15/11/2026", "cutoff": "Cut-off (Cu): 0 pct"}
    table = results.resource_table(b)
    assert set(table["Dominio"]) == {"61", "62", "Total"} and table.attrs["metal"] == "Cu"
    tot = results.totals_by_category(table).set_index("Categoría")
    assert tot.loc["Medido", "Tonelaje (t)"] == 110.0 and tot.loc["Indicado", "Tonelaje (t)"] == 320.0   # usa la fila Total (no duplica)
    sin_total = table[table["Dominio"] != "Total"]
    t2 = results.totals_by_category(sin_total).set_index("Categoría")                                  # sin fila Total: suma ponderada
    assert t2.loc["Medido", "Tonelaje (t)"] == 110.0 and round(t2.loc["Medido", "Ley Cu"], 4) == round((100 * 1 + 10 * 3) / 110, 4)
    comp = results.comparison_total(b)
    assert list(comp["Categoría"]) == ["Medido", "Indicado", "Inferido"] and comp.loc[0, "Actual (t)"] == 110.0
    plan, gain = results.gain_tables(b)
    assert plan["N° de sondajes"] == 20 and gain.loc[0, "Ganancia (t)"] == 30.0


def test_report_reader_tolerates_missing_sheets(tmp_path):
    xlsx = tmp_path / "vacio.xlsx"
    with pd.ExcelWriter(xlsx) as xw:
        pd.DataFrame([["nada"]]).to_excel(xw, sheet_name="Otra", header=False, index=False)
    b = results.load_report(xlsx)
    assert results.resource_table(b) is None and results.comparison_total(b) is None and results.gain_tables(b) == ({}, None)
    assert results.list_reports(tmp_path / "no_existe") == []
    assert results.fmt_t(2_500_000) == "2.50 Mt" and results.fmt_t(1500) == "1.5 kt" and results.fmt_t(float("nan")) == "n/d"


# ====================================================================================== métricas y evaluación
def test_planner_metrics_flow_into_the_run_state_and_the_metrics_frames(tmp_path):
    cfg = make_cfg(tmp_path, BASE / "examples" / "demo_project", BASE / "catalog" / "tools_demo.yaml")
    cfg.llm.enabled = True
    ans = json.dumps({"intent": "run", "tools": ["demo_limpiar"], "confidence": 0.9, "params": {}})
    agent = Agent(cfg, io=ScriptedIO(), primary=MockLLM(["no json", ans]))
    st = agent.plan("limpia los datos")
    m = st.metrics
    assert m and m.calls == 2 and m.retries == 1 and m.json_first is False and m.tokens_out > 0 and m.wall_seconds >= 0
    again = agent.store.load(st.run_id)
    assert again.metrics.tokens_in == m.tokens_in                                              # se guardó en state.json
    rf = metrics.runs_frame([again])
    assert rf.loc[0, "Reintentos"] == 1 and rf.loc[0, "Fuente"] == "slm"
    tot = metrics.totals([again])
    assert tot.n_requests == 1 and tot.tokens_in == m.tokens_in and tot.api_cost_usd == pytest.approx(metrics.api_cost(m.tokens_in, m.tokens_out))
    assert metrics.api_cost(1_000_000, 1_000_000) == pytest.approx(metrics.API_ENTRADA_1M + metrics.API_SALIDA_1M)


def test_rule_based_requests_have_empty_model_metrics(tmp_path):
    cfg = make_cfg(tmp_path, BASE / "examples" / "demo_project", BASE / "catalog" / "tools_demo.yaml")
    st = Agent(cfg, io=ScriptedIO()).plan("limpia los datos")
    assert st.planner_source == "rules" and st.metrics.calls == 0 and st.metrics.tokens_in == 0


def test_evaluation_modes_and_baseline_on_the_development_cases(tmp_path):
    reg = ToolRegistry.load([IDW_CATALOG])
    cfg = make_cfg(tmp_path, BASE / "examples" / "demo_project", IDW_CATALOG)
    cases = evaluation.load_cases(BASE / "eval" / "cases_idw.yaml")
    assert len(cases) >= 40 and any(c["expect"].get("intent") == "formats" for c in cases)
    rows = evaluation.run_cases(evaluation.make_planner(cfg, reg, "rules"), cases)
    s = evaluation.summarize(rows)["global"]
    assert s["exact"] == 1.0 and s["unsafe"] == 0.0                                          # piso determinista del catálogo
    raw = evaluation.make_planner(cfg, reg, "raw", "qwen3-vl:4b-instruct", provider="ollama")
    assert raw.cfg.llm.enabled and not raw.cfg.llm.arbitrate_with_rules and raw.cfg.llm.primary.model == "qwen3-vl:4b-instruct"
    cloud = evaluation.make_planner(cfg, reg, "agent", "ibm-granite/granite-4.2-3b")                # v3: por defecto la nube
    assert cloud.cfg.llm.provider == "huggingface" and cloud.cfg.llm.cloud.model == "ibm-granite/granite-4.2-3b"
    assert cfg.llm.cloud.model != "ibm-granite/granite-4.2-3b"                                      # la config original no se toca
    assert evaluation.make_planner(cfg, reg, "agent").cfg.llm.arbitrate_with_rules
    assert cfg.llm.primary.model == "gemma3:4b"                                                # make_planner no altera la config original
    seen = []
    evaluation.run_cases(evaluation.make_planner(cfg, reg, "rules"), cases[:3], lambda i, n, r: seen.append((i, n)))
    assert seen == [(1, 3), (2, 3), (3, 3)]
