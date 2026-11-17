"""v3.4 · el modelo de bloques debe llegar hasta el sólido con el que se recorta (aviso ANTES de correr)."""

import json
from pathlib import Path

import pytest

from agente import geometria
from agente.geometria import Box
from agente.models import PlannerOutput
from test_v3_coherencia import edit, make_agent

ESQ = {"origen_x": 360500.0, "origen_y": 8807800.0, "origen_z": 3949.0, "extension_x": 125.0, "extension_y": 88.0,
       "extension_z": 84.0, "x_size": 4.0, "y_size": 4.0, "z_size": 3.0, "bearing": 90.0}


def write_obj(path: Path, lo, hi) -> Path:
    """Sólido mínimo: solo importan los vértices (la caja)."""
    pts = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    path.write_text("# prueba\n" + "".join(f"v {x} {y} {z}\n" for x, y, z in pts) + "f 1 2 3\n", encoding="utf-8")
    return path


def chain(ag) -> None:
    """El flujo del usuario: el modelo lo crea «modelo» y «recortar» lee ese mismo CSV."""
    def proyecto(d):
        d["crear_modelo_bloques"] = True
    edit(ag, "conf_proyecto.json", proyecto)
    salida = json.loads(ag.ctx.cfg_path("conf_crear_mb.json").read_text(encoding="utf-8-sig"))["salida"]

    def recorte(d):
        d["modelo"] = salida
    edit(ag, "conf_recorte.json", recorte)


def flow(ag, *tools):
    chain(ag)
    return ag.plan_from(PlannerOutput(intent="run", tools=list(tools or ("modelo", "recortar"))), "recorte")


def point_solid(ag, obj: Path) -> None:
    def fn(d):
        d["solido"]["ruta"] = str(obj)
    edit(ag, "conf_recorte.json", fn)


def found(st, code):
    return [f for f in st.findings if f.code == code]


# ------------------------------------------------------------------------------------------------ geometría
def test_obj_bbox_reads_vertices_only(tmp_path):
    obj = write_obj(tmp_path / "s.obj", (1, 2, 3), (4, 5, 6))
    assert geometria.obj_bbox(obj) == Box((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
    assert geometria.obj_bbox(tmp_path / "no_existe.obj") is None


def test_grid_box_uses_whole_blocks_and_bearing_90_is_not_rotated():
    g = geometria.grid_from_schema(ESQ)
    assert g.n == (32, 22, 28) and g.n_blocks == 19712
    assert g.box() == Box((360500.0, 8807800.0, 3949.0), (360628.0, 8807888.0, 4033.0))
    assert geometria.grid_from_schema({"origen_x": 1}) is None


def test_rotated_grid_box_is_bigger_than_the_plan_view_of_the_grid():
    g = geometria.grid_from_schema({**ESQ, "bearing": 45.0})
    b = g.box()
    assert b.hi[0] - b.lo[0] > 128 and b.hi[1] - b.lo[1] > 88


def test_overlap_reports_each_axis_and_the_covered_fraction():
    m = Box((0, 0, 0), (10, 10, 10))
    assert geometria.overlap(m, Box((2, 2, 2), (4, 4, 4))) == ([True, True, True], 1.0)
    touch, frac = geometria.overlap(m, Box((2, 2, 20), (4, 4, 30)))
    assert touch == [True, True, False] and frac == 0.0
    touch, frac = geometria.overlap(m, Box((5, 0, 0), (15, 10, 10)))
    assert all(touch) and frac == pytest.approx(0.5)


@pytest.mark.parametrize("bearing", [90.0, 30.0, 135.0])
def test_fit_covers_the_solid_in_the_grid_frame(bearing):
    g = geometria.grid_from_schema({**ESQ, "bearing": bearing})
    solid = Box((360520.0, 8807830.0, 4072.0), (360921.0, 8808150.0, 4195.0))
    new = geometria.fit_schema(g, solid, clip=True)
    g2 = geometria.grid_from_schema({**ESQ, **new, "bearing": bearing})
    _, frac = geometria.overlap(g2.box(), solid)
    assert frac == pytest.approx(1.0)
    # la fase de la malla se conserva: el desplazamiento del origen es un múltiplo del tamaño del bloque (en Z, claro)
    assert (new["origen_z"] - ESQ["origen_z"]) % 3.0 == pytest.approx(0.0)
    assert new["extension_z"] % 3.0 == 0.0 and new["extension_x"] % 4.0 == 0.0


def test_fit_without_clip_keeps_what_was_already_covered():
    g = geometria.grid_from_schema(ESQ)
    new = geometria.fit_schema(g, Box((360500.0, 8807800.0, 4072.0), (360700.0, 8807900.0, 4195.0)), clip=False)
    assert new["origen_z"] == 3949.0 and new["extension_z"] == 246.0


# ------------------------------------------------------------------------------------------------ alerta en el plan
def test_model_that_does_not_reach_the_solid_is_an_error_before_running_with_a_fix(tmp_path):
    ag = make_agent(tmp_path)
    point_solid(ag, write_obj(tmp_path / "OXIDOS.obj", (360500, 8807800, 4071.6), (360921.75, 8808150, 4194.8)))
    st = flow(ag)
    f = found(st, "MODEL_SOLID_NO_OVERLAP")[0]
    assert f.severity == "error" and f.tool == "recortar" and "no se cruzan" in f.message and "Z:" in f.message
    assert f.fix.kind == "edit_config" and f.fix.detail["file"] == "conf_crear_mb.json"
    assert f.fix.detail["values"]["esquema.origen_z"] == 4069.0
    assert "19 712" in f.hint and "391 776" in f.hint                      # cuánto crece el modelo
    assert st.status == "blocked"


def test_the_fix_makes_the_alert_disappear_and_unblocks_the_plan(tmp_path):
    ag = make_agent(tmp_path)
    point_solid(ag, write_obj(tmp_path / "OXIDOS.obj", (360500, 8807800, 4071.6), (360921.75, 8808150, 4194.8)))
    st = flow(ag)
    ag.apply_conf_fix(found(st, "MODEL_SOLID_NO_OVERLAP")[0].fix, st.run_id)
    esq = json.loads(ag.ctx.cfg_path("conf_crear_mb.json").read_text(encoding="utf-8-sig"))["esquema"]
    assert (esq["origen_z"], esq["extension_x"], esq["extension_y"], esq["extension_z"]) == (4069.0, 424.0, 352.0, 126.0)
    assert esq["bearing"] == 90.0 and esq["x_size"] == 4.0                    # el resto del esquema no cambia
    st = ag.prepare(st)
    assert not found(st, "MODEL_SOLID_NO_OVERLAP") and not found(st, "MODEL_SOLID_PARTIAL")


def test_partial_cover_is_a_warning_with_the_percentage(tmp_path):
    ag = make_agent(tmp_path)
    point_solid(ag, write_obj(tmp_path / "S.obj", (360500, 8807800, 3950), (360756, 8807888, 4030)))   # la mitad en X
    st = flow(ag)
    f = found(st, "MODEL_SOLID_PARTIAL")[0]
    assert f.severity == "warn" and "50 %" in f.message and not found(st, "MODEL_SOLID_NO_OVERLAP")


def test_solid_inside_the_model_gives_no_alert(tmp_path):
    ag = make_agent(tmp_path)
    point_solid(ag, write_obj(tmp_path / "S.obj", (360510, 8807810, 3960), (360600, 8807860, 4000)))
    st = flow(ag)
    assert not [f for f in st.findings if f.code.startswith("MODEL_SOLID")]


def test_the_report_does_not_repeat_the_alert_after_a_clip_with_the_same_solid(tmp_path):
    ag = make_agent(tmp_path)
    obj = write_obj(tmp_path / "OXIDOS.obj", (360500, 8807800, 4071.6), (360921.75, 8808150, 4194.8))
    point_solid(ag, obj)

    def fn(d):
        d["solido"]["activo"], d["solido"]["ruta"] = True, str(obj)
    edit(ag, "conf_reporte.json", fn)
    st = flow(ag, "modelo", "recortar", "estimar", "reporte")
    assert [f.tool for f in found(st, "MODEL_SOLID_NO_OVERLAP")] == ["recortar"]


def test_existing_model_on_disk_is_checked_from_its_centroids_without_offering_a_grid_fix(tmp_path):
    ag = make_agent(tmp_path)
    point_solid(ag, write_obj(tmp_path / "OXIDOS.obj", (360500, 8807800, 4072), (360921, 8808150, 4195)))
    model = Path(json.loads(ag.ctx.cfg_path("conf_recorte.json").read_text(encoding="utf-8-sig"))["modelo"])
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_text("XC,YC,ZC\n360502,8807802,3950.5\n360626,8807886,4031.5\n", encoding="utf-8")
    st = ag.plan_from(PlannerOutput(intent="run", tools=["recortar"]), "x")
    f = found(st, "MODEL_SOLID_NO_OVERLAP")[0]
    assert f.severity == "error" and f.fix is None and "otro modelo" in f.hint
