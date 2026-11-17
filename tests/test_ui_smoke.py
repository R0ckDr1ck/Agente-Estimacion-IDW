"""Smoke tests de la interfaz Streamlit con AppTest (sin navegador).

La UI nunca debe tocar el proyecto real: todas las pruebas trabajan sobre copias en tmp_path y apuntan
AGENTE_WORKSPACE_FILE a un archivo temporal (para no pisar las carpetas recordadas del usuario)."""

import hashlib
import shutil

import pytest

from conftest import BASE, DEMO, DEMO_CATALOG, IDW_CATALOG

st_testing = pytest.importorskip("streamlit.testing.v1")
REAL = BASE / "Proyecto_IDW"


def _snapshot() -> dict:
    """Huella de lo que NUNCA debe tocar una prueba: los conf reales y las carpetas recordadas del usuario."""
    from agente.config import workspace_file
    snap = {str(p): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted((REAL / "Configuraciones").glob("conf_*.json"))} if REAL.is_dir() else {}
    snap["workspace.json"] = workspace_file().read_bytes() if workspace_file().exists() else None
    return snap


@pytest.fixture(autouse=True)
def _real_files_untouched():
    before = _snapshot()
    yield
    assert _snapshot() == before, "una prueba de la UI modificó los conf reales o config/workspace.json"


def _app(monkeypatch, tmp_path, root, catalog):
    monkeypatch.setenv("AGENTE_ROOT", str(root))
    monkeypatch.setenv("AGENTE_CATALOGS", str(catalog))
    monkeypatch.setenv("AGENTE_WORKSPACE_FILE", str(tmp_path / "ws.json"))
    monkeypatch.setenv("AGENTE_RUNS_DIR", str(tmp_path / "runs"))                 # que la UI no ensucie el historial del usuario
    monkeypatch.setenv("AGENTE_TEMPLATES_DIR", str(tmp_path / "plantillas"))
    monkeypatch.chdir(BASE)
    return st_testing.AppTest.from_file(str(BASE / "agente" / "ui_streamlit.py"), default_timeout=90)


def _enter(at, data, results):
    """Pasa la pantalla inicial de carpetas como lo haría el usuario."""
    at.text_input(key="in_dir").set_value(str(data))
    at.text_input(key="out_dir").set_value(str(results))
    next(b for b in at.button if b.label.startswith("Continuar")).click().run()


def _text(at) -> str:
    return " ".join(m.value for m in at.markdown)


def test_ui_asks_for_folders_first_and_then_plans(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(DEMO, root)
    data = tmp_path / "entrada"
    data.mkdir()
    at = _app(monkeypatch, tmp_path, root, DEMO_CATALOG)
    at.run()
    assert not at.exception, at.exception
    assert "Carpetas de" in _text(at)                                    # pantalla inicial
    assert not at.chat_input                                             # nada más hasta elegir las carpetas
    _enter(at, data, tmp_path / "salida")
    assert not at.exception, at.exception
    assert (tmp_path / "salida").is_dir()                                # la carpeta de salida se creó
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.chat_input[0].set_value("genera el informe de ventas").run()
    assert not at.exception, at.exception
    text = _text(at)
    assert "demo_limpiar" in text and "BLOQUEADO" in text               # plan + verificación visibles
    assert "Solicitud" in text and "Estado" in text and "Interpretación" in text     # cabecera en bloques separados
    assert not (root / "data").exists()                                  # la UI en simulación no escribe en el proyecto


def test_ui_offers_five_cloud_models_with_a_password_key_and_a_local_alternative(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    root = tmp_path / "proj"
    shutil.copytree(DEMO, root)
    data = tmp_path / "in"
    data.mkdir()
    at = _app(monkeypatch, tmp_path, root, DEMO_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    assert "Agente de Estimación IDW" in " ".join(t.value for t in at.title)                 # título nuevo
    sb = at.sidebar.selectbox[0]
    assert sb.label.startswith("Modelo (") and sum("Hugging Face" in o for o in sb.options) == 5      # 5 de la nube…
    assert any("Gemma 3 4B" in o for o in sb.options) and any("Qwen3-VL" in o for o in sb.options)
    assert any(o.startswith("💻 gemma3:4b") for o in sb.options)                                         # …y los locales, en la misma lista
    key_box = at.sidebar.text_input(key="hf_key")
    assert key_box.proto.type == 1 or "API key" in key_box.label                                  # campo de contraseña
    assert any("Falta la API key" in w.value for w in at.sidebar.warning)                          # sin clave: avisa y usa reglas
    key_box.set_value("hf_clave_de_prueba_ui").run()
    assert not any("Falta la API key" in w.value for w in at.sidebar.warning)
    # la clave nunca llega a disco: ni en las carpetas recordadas ni en el historial
    blob = " ".join(p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*.json"))
    assert "hf_clave_de_prueba_ui" not in blob
    sb = at.sidebar.selectbox[0]
    sb.set_value("ollama::gemma3:4b").run()                           # elegir uno local cambia de proveedor
    assert not at.exception
    assert not any(t.key == "hf_key" for t in at.sidebar.text_input)                              # y ya no pide la clave de la nube


def test_ui_chat_tab_answers_from_the_manual_and_cites_sources(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(DEMO, root)
    data = tmp_path / "in"
    data.mkdir()
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.text_input[[i for i, t in enumerate(at.text_input) if t.label == "Tu pregunta"][0]].set_value("qué pasa si subo la potencia del IDW")
    next(b for b in at.button if b.label == "Preguntar").click().run()
    assert not at.exception, at.exception
    text = _text(at)
    assert "Potencia del IDW" in text and any("Fuentes del manual" in e.label for e in at.expander)


def test_ui_data_tab_and_viability_request_look_at_all_the_data(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(DEMO, root)
    data = tmp_path / "in" / "01_Data"
    (data / "01_Database").mkdir(parents=True)
    (data / "01_Database" / "a.csv").write_text("id,este,norte,elevacion,longitud\n1,2,3,4,5\n", encoding="utf-8")
    (data / "01_Database" / "b.csv").write_text("id,from,to,cupct\n1,0,2,0.3\n", encoding="utf-8")
    (data / "01_Database" / "c.csv").write_text("id,depth,azimuth,dip\n1,0,10,-60\n", encoding="utf-8")
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    assert not at.exception, at.exception
    text = _text(at)
    assert "Datos y" in text and "Estimar desde sondajes" in " ".join(e.label for e in at.expander)
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.chat_input[0].set_value("¿es viable estimar con mis datos?").run()
    assert not at.exception, at.exception
    assert any(("Todavía no es viable" in x.value) or ("Sí es viable" in x.value) for x in list(at.warning) + list(at.success))


@pytest.fixture
def real_copy(tmp_path):
    """Copia ligera del proyecto real (scripts + confs + CSV de parámetros); sin los gigas de datos."""
    root = tmp_path / "idw"
    shutil.copytree(REAL / "Configuraciones", root / "Configuraciones", ignore=shutil.ignore_patterns("_respaldo_agente"))
    shutil.copytree(REAL / "Flujo_Estimacion", root / "Flujo_Estimacion", ignore=shutil.ignore_patterns("__pycache__"))
    data = tmp_path / "datos"
    (data / "04_Parametros_Entrada").mkdir(parents=True)
    for f in (REAL / "01_Data" / "04_Parametros_Entrada").glob("*.csv"):
        shutil.copy(f, data / "04_Parametros_Entrada" / f.name)
    return root, data


@pytest.mark.skipif(not REAL.is_dir(), reason="falta Proyecto_IDW")
def test_ui_formats_request_offers_templates_and_conf_paths_follow_the_chosen_folders(tmp_path, monkeypatch, real_copy):
    root, data = real_copy
    results = tmp_path / "mis_resultados"
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, results)
    assert not at.exception, at.exception
    # las rutas de salida de los conf ahora apuntan a la carpeta de salida elegida (con respaldo)
    conf = (root / "Configuraciones" / "conf_comp.json").read_text(encoding="utf-8-sig")
    assert str(results).replace("\\", "\\\\") in conf
    assert any((root / "Configuraciones" / "_respaldo_agente").iterdir())
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.chat_input[0].set_value("no sé el formato del archivo de collares").run()
    assert not at.exception, at.exception
    text = _text(at)
    assert "Archivos que necesitas" in text
    assert any("Collares de sondajes" in e.label for e in at.expander)
    assert len(at.get("download_button")) >= 2                          # plantilla + guía


@pytest.mark.skipif(not REAL.is_dir(), reason="falta Proyecto_IDW")
def test_ui_metrics_and_evaluation_tabs_render_without_a_model(tmp_path, monkeypatch, real_copy):
    root, data = real_copy
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.chat_input[0].set_value("verifica si puedo estimar").run()
    assert not at.exception, at.exception
    assert any("Métricas del" in m.value for m in at.markdown) and any("Evaluación del" in m.value for m in at.markdown)


@pytest.mark.skipif(not REAL.is_dir(), reason="falta Proyecto_IDW")
def test_ui_plan_evaluation_with_composites_offers_the_origin_button_instead_of_asking_for_assay(tmp_path, monkeypatch, real_copy):
    """Caso reportado: conf con origen «tablas», carpeta con compósitos + plan + modelo. Debe ofrecer el cambio de origen, no exigir assay."""
    import json
    root, _ = real_copy
    data = tmp_path / "in" / "01_Data"
    for rel, header in {"01_Database/assay_comp.csv": "Sondaje,Desde,Hasta,length,MIDX,MIDY,MIDZ,TOPX,TOPY,TOPZ,BOTX,BOTY,BOTZ,CUPCT,CUPCT_ACOT,DOMCU",
                        "02_Modelo-Bloques/mb.csv": "XC,YC,ZC,DX,DY,DZ,domcu,cu_idw,categ",
                        "07_Sondajes_Nuevos/Plan-DDH_collar.csv": "ID,X,Y,Z,Longitud",
                        "07_Sondajes_Nuevos/Plan-DDH_survey.csv": "ID,Depth,Az,Dip"}.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(header + "\n1,2,3\n", encoding="utf-8")
    (data / "03_Dominio").mkdir()
    (data / "03_Dominio" / "21_A.obj").write_text("o\n", encoding="utf-8")
    conf_path = root / "Configuraciones" / "conf_importar_bd.json"
    import re
    conf_path.write_text(re.sub(r'"origen":\s*"[^"]*"', '"origen": "tablas"', conf_path.read_text(encoding="utf-8-sig")), encoding="utf-8")   # la copia, no tu conf
    assert json.loads(conf_path.read_text(encoding="utf-8-sig"))["origen"] == "tablas"
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    # en la pestaña Datos la ruta del plan también trae su botón «Usar esta ruta»
    assert any(b.label == "Usar esta ruta en mi configuración" and "evaluar_plan_sondajes" in (b.key or "") for b in at.button)
    at.chat_input[0].set_value("evalúa el plan de sondajes y dime la ganancia de categoría").run()
    assert not at.exception, at.exception
    btn = next(b for b in at.button if "Usar origen" in b.label)
    assert "compositos" in btn.label
    assert any("sí puedes importar con origen" in i.value for i in list(at.info) + list(at.warning))
    btn.click().run()
    assert not at.exception, at.exception
    assert json.loads(conf_path.read_text(encoding="utf-8-sig"))["origen"] == "compositos"
    assert not any("Usar origen" in b.label for b in at.button)
    assert list((root / "Configuraciones" / "_respaldo_agente").rglob("conf_importar_bd.json"))


@pytest.mark.skipif(not REAL.is_dir(), reason="falta Proyecto_IDW")
def test_ui_warns_about_unloaded_parameter_tables_and_loads_them_on_click(tmp_path, monkeypatch, real_copy):
    """Un CSV de 04_Parametros_Entrada editado y no cargado se avisa antes de ejecutar, con botón para cargarlo (con respaldo)."""
    import json
    root, data = real_copy
    (data / "04_Parametros_Entrada" / "valorizacion_metales.csv").write_text(
        "Clave,Columna de ley,Expresión de recuperación\nzn,zn_idw,0.9\n", encoding="utf-8-sig")
    conf = root / "Configuraciones" / "conf_valorizacion.json"
    at = _app(monkeypatch, tmp_path, root, IDW_CATALOG)
    at.run()
    _enter(at, data, tmp_path / "out")
    next(t for t in at.sidebar.toggle if t.label.startswith("Usar modelo")).set_value(False)
    at.run()
    at.chat_input[0].set_value("valoriza el modelo").run()
    assert not at.exception, at.exception
    assert any("valorizacion_metales.csv" in w.value and "no coincide" in w.value for w in at.warning)
    next(b for b in at.button if b.label.startswith("📥 Cargar «valorizacion_metales.csv»")).click().run()
    assert not at.exception, at.exception
    assert json.loads(conf.read_text(encoding="utf-8-sig"))["metales"][0]["columna"] == "zn_idw"
    assert not any(b.label.startswith("📥 Cargar «valorizacion_metales.csv»") for b in at.button)


def test_ui_has_no_bare_conditional_expressions():
    """Un `st.warning(a) if c else st.info(b)` suelto lo «imprime» Streamlit (magic) y vuelca la ayuda del objeto en pantalla."""
    import ast
    tree = ast.parse((BASE / "agente" / "ui_streamlit.py").read_text(encoding="utf-8"))
    bad = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, (ast.IfExp, ast.BoolOp))]
    assert not bad, f"expresiones sueltas en ui_streamlit.py (usa if/else): líneas {bad}"
