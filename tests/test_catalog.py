import pytest

from agente.catalog import CatalogError, ToolRegistry
from agente.jpath import exists, get, get_all, set_value
from conftest import DEMO_CATALOG, IDW_CATALOG, write_catalog


def test_real_catalog_loads_and_matches_canonical_flow():
    reg = ToolRegistry.load([IDW_CATALOG])
    flow = [t.id for t in reg.flow()]
    # orden canónico de EjecutarFlujo.py
    assert flow == ["importar", "codificar", "acotar", "compositar", "plan", "exportar", "modelo",
                    "recortar", "estimar", "valorizar", "categorizar", "reporte"]
    assert "esquema_bloques" in reg.ids() and "interfaz" not in reg.ids()      # la edición de parámetros ahora es web


def test_demo_catalog_loads():
    assert len(ToolRegistry.load([DEMO_CATALOG])) == 3


def test_function_schemas_are_valid_json_schema_shapes():
    reg = ToolRegistry.load([IDW_CATALOG])
    sch = {s["function"]["name"]: s["function"] for s in reg.function_schemas()}
    assert sch["esquema_bloques"]["parameters"]["required"] == ["csv"]
    assert sch["estimar"]["parameters"]["properties"] == {}


def test_unknown_field_in_catalog_fails_loudly(tmp_path):
    p = write_catalog(tmp_path, "tools:\n  - {id: a, title: A, description: d, runner: {script: a.py}, tipo_raro: 1}\n")
    with pytest.raises(CatalogError):
        ToolRegistry.load([p])


def test_dangling_template_reference_is_rejected(tmp_path):
    p = write_catalog(tmp_path, """
tools:
  - id: a
    title: A
    description: d
    runner: {script: a.py}
    inputs: [{name: x, literal: x.csv, template: no_existe}]
""")
    with pytest.raises(CatalogError, match="no_existe"):
        ToolRegistry.load([p])


def test_pathref_requires_exactly_one_source(tmp_path):
    p = write_catalog(tmp_path, """
tools:
  - id: a
    title: A
    description: d
    runner: {script: a.py}
    inputs: [{name: x, literal: x.csv, key: k}]
""")
    with pytest.raises(CatalogError):
        ToolRegistry.load([p])


def test_duplicate_ids_rejected(tmp_path):
    tool = "  - {id: a, title: A, description: d, runner: {script: a.py}}\n"
    with pytest.raises(CatalogError):
        ToolRegistry.load([write_catalog(tmp_path, "tools:\n" + tool + tool)])


def test_jpath_roundtrip():
    d = {"a": {"b": [{"c": 1}, {"c": 2}]}, "x": "y"}
    assert get(d, "a.b[1].c") == 2
    assert [v for _, v in get_all(d, "a.b[*].c")] == [1, 2]
    assert [p for p, _ in get_all(d, "a.b[*].c")] == ["a.b[0].c", "a.b[1].c"]
    assert not exists(d, "a.z")
    set_value(d, "a.b[0].c", 9)
    assert d["a"]["b"][0]["c"] == 9
