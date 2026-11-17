"""Ciclo completo sobre la demo ficticia: solicitud → verificación → faltante → plantilla → ejecución → informe."""

import json
import sys
from pathlib import Path

from agente.models import PlannerOutput
from agente.orchestrator import Agent
from agente.state import RunStore
from conftest import ScriptedIO


def codes(state):
    return {(f.tool, f.code) for f in state.findings}


def test_dependency_closure_infers_upstream_steps_even_with_missing_conf(demo_agent):
    st = demo_agent.plan("genera el informe de ventas")
    assert [s.tool_id for s in st.plan.steps] == ["demo_limpiar", "demo_resumir", "demo_reporte"]
    assert st.planner_source == "rules"          # llm.enabled = False
    assert "genera" in st.plan.steps[0].reason


def test_dry_run_detects_every_missing_input_and_writes_nothing_in_project(demo_agent, demo_root):
    before = sorted(p.relative_to(demo_root).as_posix() for p in demo_root.rglob("*"))
    st = demo_agent.plan("genera el informe de ventas")
    assert st.status == "blocked"
    assert ("demo_limpiar", "INPUT_MISSING") in codes(st)       # datos de entrada ausentes
    assert ("demo_resumir", "CFG_MISSING") in codes(st)          # conf ausente
    assert ("demo_reporte", "PARAM_MISSING") in codes(st)        # parámetro obligatorio
    after = sorted(p.relative_to(demo_root).as_posix() for p in demo_root.rglob("*"))
    assert before == after                                       # el dry-run NO toca el proyecto
    kinds = {Path(g.path).name for g in st.generated}
    assert kinds == {"ventas_crudas.plantilla.csv", "conf_resumir.plantilla.json"}


def test_generated_templates_are_explained_and_never_overwrite(demo_agent, demo_cfg, demo_root):
    st = demo_agent.plan("genera el informe de ventas")
    conf_t = next(g for g in st.generated if g.kind == "config")
    doc = json.loads(Path(conf_t.path).read_text(encoding="utf-8"))
    assert "_ayuda" in doc and doc["columna_grupo"] == "region"            # explicación + valores por defecto
    assert (demo_cfg.templates_dir / "ventas_crudas.plantilla.LEEME.txt").is_file()
    done = demo_agent.promote_templates(st, ("config",))
    promoted = json.loads(Path(done[0]).read_text(encoding="utf-8"))
    assert "_ayuda" not in promoted                                         # el conf real queda limpio
    assert demo_agent.promote_templates(st, ("config",)) == []              # no sobrescribe ni repite


def test_full_cycle_executes_and_reports(demo_agent, demo_root):
    st = demo_agent.plan("genera el informe de ventas", {"titulo": "Ventas Q1"})
    st = demo_agent.execute(st, accept_templates="all", assume_yes=True)
    assert st.status == "completed", demo_agent.report(st)
    assert all(s.status == "ok" for s in st.plan.steps)
    informe = (demo_root / "out" / "informe.md").read_text(encoding="utf-8")
    assert informe.startswith("# Ventas Q1") and "Norte" in informe
    text = demo_agent.report(st)
    assert "completado" in text and "✔" in text
    # trazabilidad: estado + traza JSONL + un log por paso
    run_dir = demo_agent.cfg.runs_dir / st.run_id
    events = [json.loads(l)["event"] for l in (run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[:3] == ["request", "planner", "prepared"] and events.count("step_end") == 3
    assert len(list(run_dir.glob("*.log"))) == 3


def test_destructive_step_requires_explicit_confirmation(demo_agent, demo_cfg, demo_root):
    st = demo_agent.plan("genera el informe de ventas", {"titulo": "A"})
    demo_agent.execute(st, accept_templates="all", assume_yes=True)
    (demo_root / "out" / "informe.md").write_text("MIO", encoding="utf-8")        # ya existe -> se sobrescribiría

    io = ScriptedIO(confirm=False)
    agent = Agent(demo_cfg, io=io)
    st2 = agent.plan("genera el informe de ventas", {"titulo": "B"})
    assert st2.overwrites and str(demo_root / "out" / "informe.md") in st2.overwrites
    st2 = agent.execute(st2)
    assert st2.status == "aborted" and io.confirmations                     # preguntó y se le negó
    assert (demo_root / "out" / "informe.md").read_text(encoding="utf-8") == "MIO"   # nada se tocó

    agent.io = ScriptedIO(confirm=True)
    st3 = agent.execute(agent.resume(st2.run_id))
    assert st3.status == "completed"
    assert (demo_root / "out" / "informe.md").read_text(encoding="utf-8").startswith("# B")


def test_missing_param_is_asked_with_context_and_then_runs(demo_cfg, demo_root):
    io = ScriptedIO(confirm=True, answers={"titulo": "Preguntado"})
    agent = Agent(demo_cfg, io=io)
    st = agent.plan("genera el informe de ventas")
    st = agent.execute(st, accept_templates="all", assume_yes=True)
    assert io.asked == ["titulo"] and st.status == "completed"
    assert (demo_root / "out" / "informe.md").read_text(encoding="utf-8").startswith("# Preguntado")


def test_failure_is_loud_diagnosed_and_resumable_from_failed_step(demo_agent, demo_root):
    st = demo_agent.plan("genera el informe de ventas", {"titulo": "T"})
    demo_agent.promote_templates(st, ("config", "data"))
    (demo_root / "data" / "ventas_crudas.csv").write_text("id,region,valor\nV1,N,5\n", encoding="utf-8")   # columna 'monto' ausente
    st = demo_agent.execute(st, assume_yes=True)
    assert st.status == "failed"
    bad = next(s for s in st.plan.steps if s.status == "error")
    assert bad.tool_id == "demo_limpiar" and "monto" in bad.diagnosis       # KeyError traducido a mensaje accionable
    assert [s.status for s in st.plan.steps] == ["error", "pending", "pending"]
    # el usuario corrige el insumo y reanuda desde el punto de fallo
    (demo_root / "data" / "ventas_crudas.csv").write_text("id,region,monto\nV1,N,5\nV2,S,7\n", encoding="utf-8")
    again = demo_agent.execute(demo_agent.resume(st.run_id), assume_yes=True)
    assert again.status == "completed" and [s.status for s in again.plan.steps] == ["ok"] * 3


def test_resume_does_not_rerun_completed_steps(demo_agent, demo_root):
    st = demo_agent.plan("genera el informe de ventas", {"titulo": "T"})
    st = demo_agent.execute(st, accept_templates="all", assume_yes=True)
    first_start = st.plan.steps[0].started_at
    (demo_root / "out" / "informe.md").unlink()
    st.plan.steps[2].status = "error"                                         # simula caída en el último paso
    st.status = "failed"
    demo_agent.store.save(st)
    st = demo_agent.execute(demo_agent.resume(st.run_id), assume_yes=True)
    assert st.status == "completed" and st.plan.steps[0].started_at == first_start


def test_state_store_roundtrip_and_atomic_save(demo_agent, demo_cfg):
    st = demo_agent.plan("genera el informe de ventas")
    store = RunStore(demo_cfg.runs_dir)
    loaded = store.load(st.run_id)
    assert loaded.model_dump() == st.model_dump()
    assert not list((demo_cfg.runs_dir / st.run_id).glob("*.tmp"))
    assert store.latest().run_id == st.run_id


def test_explicit_selection_without_llm(demo_agent):
    st = demo_agent.plan_from(PlannerOutput(tools=["demo_resumir"]), "check", {})
    assert [s.tool_id for s in st.plan.steps] == ["demo_limpiar", "demo_resumir"]
    assert st.planner_source == "provided"


def test_ambiguous_request_asks_instead_of_guessing(demo_agent):
    st = demo_agent.plan("hazlo")
    assert st.status == "needs_input" and st.question and not st.plan.steps


def test_list_and_explain_are_informational(demo_agent):
    assert "demo_limpiar" in demo_agent.report(demo_agent.plan("qué herramientas hay"))
    st = demo_agent.plan("explica qué hace limpiar")
    assert st.status == "info" and "Descarta" in demo_agent.report(st)
