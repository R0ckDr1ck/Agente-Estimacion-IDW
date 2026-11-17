"""v4: asistente de «usuario nuevo» (crear las carpetas de entrada y salida). Todo en tmp_path; nada toca los conf reales."""

import hashlib

import pytest

from conftest import BASE, DEMO, DEMO_CATALOG, IDW_CATALOG
from agente import nuevo_usuario as nu
from agente.catalog import ToolRegistry
from agente.workspace import check_dirs

REAL = BASE / "Proyecto_IDW"


@pytest.fixture(autouse=True)
def _confs_intactos():
    def snap():
        d = REAL / "Configuraciones"
        return {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in sorted(d.glob("conf_*.json"))} if d.is_dir() else {}
    before = snap()
    yield
    assert snap() == before, "una prueba modificó los conf reales"


def test_crea_estructura_completa(tmp_path):
    r = nu.crear_estructura(tmp_path, "Proy")
    assert r.ok and r.data_dir == tmp_path / "Proy" / "01_Data" and r.results_dir == tmp_path / "Proy" / "02_Resultados"
    for c in nu.GUIA_ENTRADA:
        assert (r.data_dir / c.nombre).is_dir() and (r.data_dir / c.nombre / "LEEME.txt").is_file()
    for c in nu.GUIA_SALIDA:
        assert (r.results_dir / c.nombre).is_dir()
    assert {c.nombre for c in nu.GUIA_ENTRADA} == {"01_Database", "02_Modelo-Bloques", "03_Dominio", "04_Parametros_Entrada",
                                                   "05_Objetivo", "06_Topografia", "07_Sondajes_Nuevos"}
    # el resultado es aceptado por la pantalla de carpetas del agente
    chk = check_dirs(str(r.data_dir), str(r.results_dir))
    assert chk.ok, chk.errors
    assert "01_Data" in nu.resumen_arbol(r) and "└──" in nu.resumen_arbol(r)


def test_es_idempotente_y_no_sobrescribe(tmp_path):
    r1 = nu.crear_estructura(tmp_path, "Proy")
    mio = r1.data_dir / "01_Database" / "ddh_collar.csv"
    mio.write_text("mis,datos\n1,2\n", encoding="utf-8")
    leeme = r1.data_dir / "01_Database" / "LEEME.txt"
    leeme.write_text("editado por el usuario", encoding="utf-8")
    r2 = nu.crear_estructura(tmp_path, "Proy", plantillas=False)
    assert r2.ok and not r2.creadas and r2.existentes
    assert mio.read_text(encoding="utf-8") == "mis,datos\n1,2\n" and leeme.read_text(encoding="utf-8") == "editado por el usuario"


@pytest.mark.parametrize("nombre", ["", "  ", "..", "a/b", "a\\b", "x:y", "CON", "nul.txt", "termina.", "a" * 81, 'com"illa'])
def test_nombres_invalidos(tmp_path, nombre):
    r = nu.crear_estructura(tmp_path, nombre)
    assert not r.ok and r.errores
    assert list(tmp_path.iterdir()) == []                      # no creó nada


def test_destino_relativo_o_archivo(tmp_path):
    assert not nu.crear_estructura("carpeta_relativa", "P").ok
    f = tmp_path / "archivo.txt"
    f.write_text("x", encoding="utf-8")
    r = nu.crear_estructura(f, "P")
    assert not r.ok and "archivo" in r.errores[0]


def test_plantillas_van_fuera_de_la_entrada(tmp_path):
    reg = ToolRegistry.load([IDW_CATALOG])
    r = nu.crear_estructura(tmp_path, "Proy", plantillas=True, registry=reg)
    assert r.ok and r.plantillas
    pl = tmp_path / "Proy" / nu.PLANTILLAS
    nombres = {p.name for p in pl.iterdir()}
    assert {"ddh_collar.csv", "block_model.csv", "estimacion_pasadas.csv", "LEEME.txt"} <= nombres
    assert not list(r.data_dir.rglob("*.csv")), "una plantilla quedó dentro de 01_Data: se confundiría con datos reales"
    assert (pl / "ddh_collar.csv").read_text(encoding="utf-8").splitlines()[0] == "id,este,norte,elevacion,longitud"


def test_plantillas_sin_catalogo_avisa(tmp_path):
    r = nu.crear_estructura(tmp_path, "Proy", plantillas=True, registry=None)
    assert not r.ok and "catálogo" in r.errores[0]


def test_guardar_subido(tmp_path):
    r = nu.crear_estructura(tmp_path, "Proy")
    ok, msg = nu.guardar_subido(r.data_dir, "01_Database", "ddh_collar.csv", b"id,este\nA,1\n")
    assert ok == r.data_dir / "01_Database" / "ddh_collar.csv" and ok.read_bytes() == b"id,este\nA,1\n"
    dup, msg = nu.guardar_subido(r.data_dir, "01_Database", "ddh_collar.csv", b"otro")
    assert dup is None and "no se sobrescribe" in msg and ok.read_bytes() == b"id,este\nA,1\n"
    assert nu.guardar_subido(r.data_dir, "01_Database", "ddh_collar.csv", b"nuevo", sobrescribir=True)[0] is not None
    # recorrido de rutas: se queda solo el nombre simple, siempre dentro de la subcarpeta
    p, _ = nu.guardar_subido(r.data_dir, "03_Dominio", "..\\..\\fuera.obj", b"o")
    assert p == r.data_dir / "03_Dominio" / "fuera.obj" and not (tmp_path / "fuera.obj").exists()
    assert nu.guardar_subido(r.data_dir, "no_existe", "a.csv", b"x")[0] is None
    assert nu.guardar_subido(r.data_dir, "../..", "a.csv", b"x")[0] is None
    assert nu.guardar_subido(r.data_dir, "01_Database", "virus.exe", b"x")[0] is None
    assert nu.guardar_subido(r.data_dir, "01_Database", "a:b.csv", b"x")[0] is None
    assert nu.subcarpetas_entrada(r.data_dir) == [c.nombre for c in nu.GUIA_ENTRADA]


def test_cli_init(tmp_path, capsys, monkeypatch):
    from agente import cli
    monkeypatch.setenv("AGENTE_WORKSPACE_FILE", str(tmp_path / "ws.json"))
    assert cli.main(["init", str(tmp_path / "base"), "--nombre", "P1", "--plantillas", "--usar"]) == 0
    out = capsys.readouterr().out
    assert "ENTRADA" in out and (tmp_path / "base" / "P1" / "01_Data" / "07_Sondajes_Nuevos").is_dir()
    from agente.config import load_saved_workspace
    assert load_saved_workspace(tmp_path / "ws.json")["data_dir"].endswith("01_Data")
    assert cli.main(["init", str(tmp_path / "base"), "--nombre", "a/b"]) == 1          # nombre inválido -> código 1


# ------------------------------------------------------------------------------------------------ interfaz
st_testing = pytest.importorskip("streamlit.testing.v1")


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTE_ROOT", str(DEMO))
    monkeypatch.setenv("AGENTE_CATALOGS", str(DEMO_CATALOG))
    monkeypatch.setenv("AGENTE_WORKSPACE_FILE", str(tmp_path / "ws.json"))
    monkeypatch.setenv("AGENTE_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("AGENTE_TEMPLATES_DIR", str(tmp_path / "plantillas"))
    monkeypatch.chdir(BASE)
    return st_testing.AppTest.from_file(str(BASE / "agente" / "ui_streamlit.py"), default_timeout=90)


def test_ui_crear_carpetas_rellena_las_rutas(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path).run()
    assert not at.exception
    assert any("usuario nuevo" in e.label for e in at.expander)
    at.text_input(key="nu_dest").set_value(str(tmp_path / "mis_proyectos"))
    at.text_input(key="nu_name").set_value("Nuevo")
    at.button(key="nu_create").click().run()
    assert not at.exception
    base = tmp_path / "mis_proyectos" / "Nuevo"
    assert (base / "01_Data" / "01_Database").is_dir() and (base / "02_Resultados" / "04_Reporte").is_dir()
    assert at.text_input(key="in_dir").value == str(base / "01_Data")
    assert at.text_input(key="out_dir").value == str(base / "02_Resultados")
    assert any("Listo" in s.value for s in at.success)


def test_ui_nombre_invalido_muestra_error_y_no_crea(monkeypatch, tmp_path):
    at = _app(monkeypatch, tmp_path).run()
    at.text_input(key="nu_dest").set_value(str(tmp_path / "x"))
    at.text_input(key="nu_name").set_value("a/b")
    at.button(key="nu_create").click().run()
    assert any("nombre" in e.value.lower() for e in at.error)
    assert not (tmp_path / "x").exists()
