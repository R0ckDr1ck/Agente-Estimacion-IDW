"""v3 · al cambiar las carpetas de entrada/salida se reubican TODAS las rutas de TODOS los conf_*.json.

Los conf de `fixtures/confs_mixtos/` son los que dejó la v2 tras usarse con dos carpetas distintas: unas rutas apuntan a
Prueba_Agente_v3 y otras a Prueba_Agente_v2 (la reubicación antigua solo tocaba las del plan actual).
"""

import json
import shutil
from pathlib import Path

import pytest

from agente import pathfix
from agente.config import AgentConfig
from agente.orchestrator import Agent

FIX = Path(__file__).parent / "fixtures" / "confs_mixtos"
BASE = Path(__file__).resolve().parents[1]


def make_agent(tmp_path: Path, conf_src: Path = FIX, data: str = "in/01_Data", out: str = "out/02_Resultados") -> tuple[Agent, Path]:
    root = tmp_path / "idw"
    (root / "Configuraciones").mkdir(parents=True)
    for f in conf_src.glob("conf_*.json"):
        shutil.copy(f, root / "Configuraciones" / f.name)
    d, o = tmp_path / data, tmp_path / out
    d.mkdir(parents=True)
    o.mkdir(parents=True)
    cfg = AgentConfig.load(root=root, data_dir=d, results_dir=o)
    cfg.runs_dir, cfg.templates_dir, cfg.llm.enabled = tmp_path / "runs", tmp_path / "tpl", False
    return Agent(cfg), root


def abs_paths(agent: Agent) -> list[tuple[str, str, str]]:
    out = []
    for n in pathfix.conf_names(agent.ctx):
        data, _ = agent.ctx.load_cfg(n)
        out += [(n, jp, v) for jp, v in pathfix.iter_strings(data) if pathfix.is_abs_path_str(v)]
    return out


def test_every_absolute_path_of_every_conf_ends_up_in_the_chosen_folders(tmp_path):
    ag, root = make_agent(tmp_path)
    before = abs_paths(ag)
    assert {n for n, _, _ in before} >= {"conf_proyecto.json", "conf_categ.json", "conf_reporte.json", "conf_estimacion.json"}
    log, amb = ag.align_paths_to_workspace()
    assert amb == [] and log
    d, o = (tmp_path / "in" / "01_Data").resolve(), (tmp_path / "out" / "02_Resultados").resolve()
    after = abs_paths(ag)
    assert len(after) == len(before)
    for name, jp, v in after:
        p = Path(v).resolve()
        assert d in p.parents or o in p.parents or p in (d, o, d.parent), f"{name}:{jp} sigue fuera de las carpetas: {v}"
        assert "Prueba_Agente" not in v


def test_second_alignment_is_a_noop_and_files_are_only_changed_in_their_path_strings(tmp_path):
    ag, root = make_agent(tmp_path)
    originals = {f.name: f.read_bytes() for f in (root / "Configuraciones").glob("conf_*.json")}
    ag.align_paths_to_workspace()
    log, amb = ag.align_paths_to_workspace()
    assert log == [] and amb == []
    changed_paths = {(f.config_file, f.old) for f in pathfix.plan_relocation(ag.ctx)}
    assert changed_paths == set()
    for name, raw in originals.items():
        new = (root / "Configuraciones" / name).read_bytes()
        a, b = json.loads(raw.decode("utf-8-sig")), json.loads(new.decode("utf-8-sig"))
        sa = {jp: v for jp, v in pathfix.iter_strings(a)}
        sb = {jp: v for jp, v in pathfix.iter_strings(b)}
        assert sa.keys() == sb.keys()
        assert {k for k in sa if sa[k] != sb[k]} == {k for k in sa if pathfix.is_abs_path_str(sa[k])} - {k for k in sa if sa[k] == sb[k]}
        # lo que no es una ruta no cambia ni un carácter, y el BOM se conserva
        strip = lambda t: json.dumps({k: v for k, v in pathfix.iter_strings(t) if not pathfix.is_abs_path_str(v)}, sort_keys=True)  # noqa: E731
        assert strip(a) == strip(b)
        assert new.startswith(b"\xef\xbb\xbf") == raw.startswith(b"\xef\xbb\xbf")
        assert new.decode("utf-8-sig").count("\n") == raw.decode("utf-8-sig").count("\n")


def test_backup_holds_the_untouched_originals(tmp_path):
    ag, root = make_agent(tmp_path)
    originals = {f.name: f.read_bytes() for f in (root / "Configuraciones").glob("conf_*.json")}
    ag.align_paths_to_workspace()
    backups = list((root / "Configuraciones" / "_respaldo_agente").rglob("conf_*.json"))
    assert len(backups) == len(originals)
    assert all(b.read_bytes() == originals[b.name] for b in backups)


def test_project_root_without_anchor_follows_the_new_workspace(tmp_path):
    ag, root = make_agent(tmp_path)
    ag.align_paths_to_workspace()
    data, _ = ag.ctx.load_cfg("conf_proyecto.json")
    assert Path(data["raiz"]).resolve() == (tmp_path / "in").resolve()
    assert Path(data["base_datos"]).resolve() == (tmp_path / "out" / "02_Resultados" / "01_Database" / "Database.db").resolve()


def test_an_output_is_never_redirected_to_an_input_with_the_same_name(tmp_path):
    """mb_smeb.csv existe en 01_Data (entrada del usuario) y también es la SALIDA de «modelo» en 02_Resultados."""
    ag, root = make_agent(tmp_path)
    src = tmp_path / "in" / "01_Data" / "02_Modelo-Bloques"
    src.mkdir(parents=True)
    (src / "mb_smeb.csv").write_text("XC,YC,ZC,DX,DY,DZ\n", encoding="utf-8")
    ag.align_paths_to_workspace()
    data, _ = ag.ctx.load_cfg("conf_crear_mb.json")
    assert Path(data["salida"]).resolve() == (tmp_path / "out" / "02_Resultados" / "03_Modelo_Bloques" / "mb_smeb.csv").resolve()


def test_input_found_in_another_subfolder_is_used_if_unique(tmp_path):
    ag, root = make_agent(tmp_path)
    other = tmp_path / "in" / "01_Data" / "otra_carpeta"
    other.mkdir(parents=True)
    (other / "Plan-DDH_collar.csv").write_text("ID,X,Y,Z,Longitud\n", encoding="utf-8")
    ag.align_paths_to_workspace()
    data, _ = ag.ctx.load_cfg("conf_plan.json")
    assert Path(data["collar"]["archivo"]).resolve() == (other / "Plan-DDH_collar.csv").resolve()


def test_foreign_paths_without_equivalent_are_reported_not_touched(tmp_path):
    src = tmp_path / "confs"
    src.mkdir()
    (src / "conf_x.json").write_text(json.dumps({"externo": "Z:\\Otro\\Programa\\tool.exe", "ruta": "D:\\Viejo\\01_Data\\a.csv"}),
                                     encoding="utf-8")
    ag, root = make_agent(tmp_path, src)
    fixes = {f.jpath: f for f in ag.propose_path_fixes()}
    assert fixes["externo"].new == "" and "sin equivalente" in fixes["externo"].reason
    ag.align_paths_to_workspace()
    data, _ = ag.ctx.load_cfg("conf_x.json")
    assert data["externo"] == "Z:\\Otro\\Programa\\tool.exe"                      # intacto
    assert data["ruta"].endswith("a.csv") and "Viejo" not in data["ruta"]


def test_planning_something_else_still_offers_all_relocations(tmp_path):
    from agente.models import PlannerOutput
    ag, _ = make_agent(tmp_path)
    st = ag.plan_from(PlannerOutput(tools=["reporte"]), "solo el reporte")
    files = {f.config_file for f in st.path_fixes}
    assert {"conf_proyecto.json", "conf_codificar.json", "conf_importar_bd.json"} <= files      # no solo los del plan


def test_real_project_confs_are_untouched_by_this_suite():
    """Guardián: ninguna prueba debe haber reescrito los conf reales del proyecto (ver gotchas de la v2)."""
    real = BASE / "Proyecto_IDW" / "Configuraciones"
    assert real.is_dir()
    stamp = list((real / "_respaldo_agente").glob("*"))
    from agente.coherencia import DEFAULTS_MARK                           # (la marca de «valores por defecto» la escribe el botón, no las pruebas)
    assert all(p.is_dir() or p.name == DEFAULTS_MARK for p in stamp)


def test_parameter_tables_loaded_from_csv_have_their_paths_relocated_too(tmp_path):
    """Los CSV de ejemplo traen rutas absolutas de otro proyecto: al cargarlos al conf no deben reintroducir rutas viejas."""
    from agente import tablas
    ag, root = make_agent(tmp_path)
    sol = tmp_path / "in" / "01_Data" / "03_Dominio"
    sol.mkdir(parents=True)
    (sol / "21_A.obj").write_text("o\n", encoding="utf-8")
    (sol / "31_B.obj").write_text("o\n", encoding="utf-8")
    csv_text = "\n".join(["Ruta del sólido (.obj),Valor,Prioridad",
                          "D:/Otro/Proyecto/01_Data/03_Dominio/21_A.obj,21,1",
                          "D:/Otro/Proyecto/01_Data/03_Dominio/31_B.obj,31,2"]) + "\n"
    pt = ag.registry.param_tables["codificacion_solidos"]
    h, r = tablas.parse_csv(csv_text)
    ld = tablas.load_table(pt, h, r)
    log = tablas.apply_table(ag.ctx, ld)
    data, _ = ag.ctx.load_cfg("conf_codificar.json")
    assert [Path(x["ruta"]).resolve() for x in data["solidos"]] == [(sol / "21_A.obj").resolve(), (sol / "31_B.obj").resolve()]
    assert any("2 ruta(s) del CSV" in line for line in log)
