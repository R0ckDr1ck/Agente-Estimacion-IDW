"""Orquestador: une Planner → plan → Preflight → plantillas/correcciones → Executor → Reporter, con State Store.

Flujo de decisión (resumen; el diagrama completo está en docs/ARQUITECTURA.md):

  solicitud ─► Planner (SLM→reglas) ─► intención
     list          → catálogo
     ask/explain   → chatbot: responde con el manual de parámetros (v3)
     viability     → qué datos hay, qué rutas son posibles y qué procesos se pueden ejecutar (v3)
     run/status    → build_plan (omisiones + cierre de dependencias + orden)
                   → Preflight ─► hallazgos ─► plantillas de ejemplo / propuesta de rutas / pedir parámetros
                   → [dry-run: se detiene aquí] ─► confirmación de pasos destructivos ─► Executor paso a paso
                   → estado guardado tras cada paso (reanudable) ─► Reporter
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from . import chat, coherencia, datos, pathfix, tablas
from .catalog import ToolRegistry
from .config import AgentConfig
from .context import ProjectContext
from .executor import backup_files, launch_detached, run_command
from .llm import LLMClient
from .manual import Manual, ManualError
from .models import (AnswerInfo, ExecutionPlan, FixAction, Finding, GeneratedFile, ParamSpec, PathFix, PlannerOutput, PlanStep,
                     RunState, ToolSpec, now_iso)
from .plan import build_plan
from .planner import Planner
from .preflight import run_preflight
from .reporter import Reporter
from .schemas import validate_params
from .state import RunStore, Trace
from .templates import TemplateError, TemplateGenerator

log = logging.getLogger("agente")


class AgentIO:
    """Canal con el usuario. La implementación base no interactúa: deniega confirmaciones y no responde preguntas."""

    def say(self, msg: str) -> None: ...
    def line(self, tool_id: str, line: str) -> None: ...
    def confirm(self, title: str, lines: list[str]) -> bool: return False
    def ask(self, spec: ParamSpec, tool: ToolSpec) -> Optional[str]: return None


class Agent:
    def __init__(self, cfg: AgentConfig, registry: Optional[ToolRegistry] = None, io: Optional[AgentIO] = None,
                 primary: Optional[LLMClient] = None, planner: Optional[Planner] = None):
        self.cfg = cfg
        self.registry = registry or ToolRegistry.load(cfg.catalogs)
        self.ctx = ProjectContext(cfg, self.registry)
        self.io = io or AgentIO()
        self.store = RunStore(cfg.runs_dir)
        self.planner = planner or Planner(cfg, self.registry, primary)
        self.templates = TemplateGenerator(self.ctx)
        client = self.planner.primary if (cfg.llm.enabled and cfg.llm.report_with_llm) else None
        self.reporter = Reporter(self.registry, cfg.prompts_dir, client)
        self._manual: Optional[Manual] = None

    # ================================================================== manual y chat (v3)
    @property
    def manual(self) -> Manual:
        if self._manual is None:
            self._manual = Manual.load(self.cfg.manual, {t.id: t.title for t in self.registry})
        return self._manual

    def ask(self, question: str, history: Optional[list[dict[str, str]]] = None, tools: Optional[list[str]] = None) -> AnswerInfo:
        """Responde una duda sobre procesos o parámetros con el manual. Con modelo disponible lo usa para redactar; si no,
        o si falla, devuelve las fichas del manual. Nunca ejecuta ni modifica nada."""
        try:
            manual = self.manual
        except ManualError as e:
            return AnswerInfo(question=question, text=f"No pude cargar el manual: {e}", warnings=[str(e)])
        client = self.planner.primary if self.cfg.llm.enabled else None
        return chat.answer(question, manual, self.ctx, client, history, tools, self.cfg.prompts_dir)

    def _manual_covers(self, tools: list[str]) -> bool:
        try:
            return all(self.manual.tool_entry(t) is not None for t in tools)
        except ManualError:
            return False

    def data_report(self) -> "datos.DataReport":
        """Qué datos hay (carpeta de entrada, base de datos, resultados) y qué se puede hacer con ellos. Solo lectura."""
        self.ctx.invalidate()
        return datos.analyze(self.ctx)

    # ================================================================== planificación
    def plan(self, request: str, params: Optional[dict[str, Any]] = None) -> RunState:
        """Interpreta la solicitud y deja un plan VERIFICADO (modo simulación: no ejecuta nada)."""
        state = RunState(run_id=self.store.new_id(), request=request)
        trace = Trace(self.store, state.run_id)
        trace.emit("request", text=request)
        res = self.planner.plan(request)
        state.planner, state.planner_source = res.output, res.source
        state.planner_notes, state.metrics = list(res.warnings), res.metrics
        trace.emit("planner", source=res.source, output=res.output.model_dump(), attempts=res.attempts,
                   warnings=res.warnings, agrees_with_rules=res.agrees_with_rules, metrics=res.metrics.model_dump())
        return self._after_planner(state, params, trace)

    def plan_from(self, out: PlannerOutput, request: str = "", params: Optional[dict[str, Any]] = None) -> RunState:
        """Plan con selección explícita (sin LLM): --tools / --workflow / --desde / --hasta."""
        state = RunState(run_id=self.store.new_id(), request=request or "(selección explícita)")
        trace = Trace(self.store, state.run_id)
        state.planner, state.planner_source = out, "provided"
        trace.emit("planner", source="provided", output=out.model_dump())
        return self._after_planner(state, params, trace)

    def _after_planner(self, state: RunState, params: Optional[dict[str, Any]], trace: Trace) -> RunState:
        out = state.planner
        assert out is not None
        state.params = {**out.params, **(params or {})}

        selected = bool(out.tools or out.workflow or out.from_tool or out.to_tool)
        if out.intent in ("list", "formats", "viability"):
            state.status = "info"
        elif out.intent in ("ask", "explain"):
            state.status = "info"
            if not (out.intent == "explain" and out.tools and not self._manual_covers(out.tools)):
                state.answer = self.ask(state.request, tools=out.tools)     # si el manual no cubre la herramienta, se usa su ficha del catálogo
        elif out.intent == "unknown" or (
                not selected and out.needs_clarification and out.intent not in ("status", "resume", "fix_paths")):
            state.status = "needs_input"
            state.question = out.needs_clarification or "No entendí la solicitud. ¿Qué herramienta o paso quieres usar?"
        elif out.intent == "resume":
            prev = self.store.latest(resumable_only=True)
            if prev is None:
                state.status = "needs_input"
                state.question = "No hay ejecuciones previas que reanudar."
            else:
                self.store.save(state)
                return prev
        elif out.intent == "fix_paths":
            state.path_fixes = self.propose_path_fixes(state.params)
            state.status = "info"
        else:
            if out.intent == "status" and not selected:
                flow = [t.id for t in self.registry.flow()]
                state.planner = out = out.model_copy(update={"from_tool": flow[0], "to_tool": flow[-1]})
            self.prepare(state)
        trace.emit("prepared", status=state.status, steps=[s.tool_id for s in state.plan.steps],
                   errors=len(state.errors()), warnings=sum(f.severity == "warn" for f in state.findings))
        self.store.save(state)
        return state

    # ================================================================== verificación
    def prepare(self, state: RunState) -> RunState:
        """(Re)calcula plan + preflight + plantillas + correcciones. Idempotente; no ejecuta nada."""
        assert state.planner is not None
        if not state.frozen:        # una vez iniciada la ejecución el plan se conserva (aunque haya salidas parciales)
            state.plan = build_plan(self.ctx, state.planner, state.params)
        steps = [s for s in state.plan.steps if s.status != "ok"]
        pf = run_preflight(self.ctx, ExecutionPlan(steps=steps), state.params)
        state.findings, state.overwrites = pf.findings, pf.overwrites
        state.findings += datos.origin_findings(self.ctx, [s.tool_id for s in steps], pf.findings)   # v3.1: mira todos los datos
        state.findings += coherencia.check(self.ctx, steps, state.params)       # v3.1: columnas entre pasos y tablas sin cargar
        state.findings += coherencia.workflow_findings(self.ctx, state.planner.workflow)   # conf que exige el flujo elegido
        broken = [f.fix.detail["old"] for f in state.findings if f.code == "MODEL_CHAIN_BROKEN" and f.severity == "error"]
        if broken:        # un único mensaje claro: el modelo que falta lo deja el paso anterior (no se ofrece un archivo de ejemplo falso)
            state.findings = [f for f in state.findings if not (f.code == "INPUT_MISSING" and any(b in f.message for b in broken))]
        self._generate_templates(state)
        state.path_fixes = pathfix.plan_relocation(self.ctx)          # v3: todas las rutas de todos los conf
        if not state.plan.steps:
            state.status = "needs_input"
            state.question = state.question or "Con la configuración actual no hay nada que ejecutar para esa solicitud."
        else:
            state.status = "blocked" if state.errors() else "ready"
        return state

    def _clean(self, tool_id: str, state: RunState) -> dict[str, Any]:
        """Parámetros validados; los de tipo ruta se pasan ABSOLUTOS (el script corre en otra carpeta de trabajo)."""
        tool = self.registry.get(tool_id)
        clean = validate_params(tool, state.params)[0] or dict(state.params)
        for p in tool.params:
            if p.type == "path" and clean.get(p.name):
                clean[p.name] = str(self.ctx.to_path(str(clean[p.name])))
        return clean

    def _generate_templates(self, state: RunState) -> None:
        keep = {g.target: g for g in state.generated if g.promoted}
        seen: set[str] = set()
        for f in list(state.findings):
            if not f.fix or f.fix.kind != "generate_template":
                continue
            d = f.fix.detail
            target = d["target"]
            if target in seen:
                continue
            seen.add(target)
            tool = self.registry.get(d["tool"])
            try:
                gen = (self.templates.for_config(tool) if d["kind"] == "config"
                       else self.templates.for_data(tool, d["template"], Path(target)))
            except TemplateError as e:
                state.findings.append(Finding(tool=tool.id, code="TEMPLATE_FAILED", severity="warn", message=str(e)))
                continue
            keep[target] = gen
        state.generated = list(keep.values())

    # ================================================================== rutas obsoletas
    def propose_path_fixes(self, params: Optional[dict[str, Any]] = None, tool_ids: Optional[list[str]] = None) -> list[PathFix]:
        """Rutas de TODOS los conf_*.json que no coinciden con las carpetas de trabajo (los argumentos se ignoran: se
        conservan por compatibilidad). Las que no se pueden reubicar vienen con `new` vacío."""
        return pathfix.plan_relocation(self.ctx)

    def apply_path_fixes(self, fixes: list[PathFix], run_id: Optional[str] = None) -> list[str]:
        log_lines = pathfix.apply_fixes(self.ctx, fixes)
        if run_id:
            Trace(self.store, run_id).emit("path_fixes_applied", fixes=[f.model_dump() for f in fixes if f.new], log=log_lines)
        return log_lines

    def apply_table_fix(self, fix: FixAction, run_id: Optional[str] = None) -> list[str]:
        """Carga al conf un CSV de parámetros de la carpeta de entrada (hallazgo `load_table` que el usuario aceptó)."""
        pt = self.registry.param_tables[fix.detail["table"]]
        load = tablas.load_table_file(pt, Path(fix.detail["path"]))
        log_lines = tablas.apply_table(self.ctx, load)
        if run_id:
            Trace(self.store, run_id).emit("table_loaded", table=pt.id, path=fix.detail["path"], log=log_lines)
        return log_lines

    def apply_conf_fix(self, fix: FixAction, run_id: Optional[str] = None) -> list[str]:
        """Aplica un hallazgo de tipo `edit_config` (p. ej. origen = compositos) que el usuario aceptó: textual, con respaldo."""
        d = fix.detail
        edits = d.get("edits") or [{"file": d["file"], "set": dict(d["values"])}]
        log_lines = datos.apply_edits(self.ctx, edits)
        coherencia.remember_defaults(self.ctx, edits)          # esas tablas diferirán de su CSV a propósito
        if run_id:
            Trace(self.store, run_id).emit("conf_fix_applied", edits=edits, log=log_lines)
        return log_lines

    def align_paths_to_workspace(self) -> tuple[list[str], list[PathFix]]:
        """Tras elegir las carpetas de entrada/salida: reubica en TODOS los conf_*.json todas las rutas que apuntan a otro
        lado (con respaldo). Devuelve (bitácora, rutas que NO se pudieron reubicar: ambiguas o sin equivalente)."""
        self.ctx.invalidate()
        fixes = self.propose_path_fixes()
        log_lines = pathfix.apply_fixes(self.ctx, fixes) if any(f.new and not f.ambiguous for f in fixes) else []
        return log_lines, [f for f in fixes if f.ambiguous or not f.new]

    def promote_templates(self, state: RunState, kinds: tuple[str, ...] = ("config",)) -> list[str]:
        done = []
        for g in state.generated:
            if g.kind in kinds and not g.promoted and not Path(g.target).exists():
                self.templates.promote(g)
                done.append(g.target)
        if done:
            self.ctx.invalidate()
            Trace(self.store, state.run_id).emit("templates_promoted", targets=done)
        return done

    # ================================================================== ejecución
    def execute(self, state: RunState, *, params: Optional[dict[str, Any]] = None, assume_yes: bool = False,
                accept_templates: Optional[str] = None, apply_path_fixes: bool = False,
                dry_run: bool = False, interactive: bool = True) -> RunState:
        """Ejecuta (o reanuda) los pasos pendientes. Se detiene en el primer error. Guarda el estado tras cada paso."""
        trace = Trace(self.store, state.run_id)
        if state.status in ("info", "needs_input") or state.planner is None:
            return state
        if params:
            state.params.update(params)
        if apply_path_fixes and state.path_fixes:
            for line in self.apply_path_fixes(state.path_fixes, state.run_id):
                self.io.say(line)
        if accept_templates:
            kinds = ("config", "data") if accept_templates == "all" else ("config",)
            for t in self.promote_templates(state, kinds):
                self.io.say(f"Plantilla copiada a {t}")
        self.prepare(state)
        if interactive:
            self._ask_missing_params(state)
        state.dry_run = dry_run
        if dry_run or state.status == "blocked":
            trace.emit("stopped", reason="dry_run" if dry_run else "blocked", errors=[f.code for f in state.errors()])
            self.store.save(state)
            return state

        # --- confirmación explícita de pasos destructivos
        if state.overwrites and self.cfg.safety.confirm_destructive and not state.confirmed:
            if not (assume_yes or self.io.confirm("Los siguientes archivos se sobrescribirán o modificarán", state.overwrites)):
                state.status = "aborted"
                trace.emit("aborted", reason="destructive_not_confirmed", overwrites=state.overwrites)
                self.store.save(state)
                return state
            state.confirmed = True
            trace.emit("confirmed", overwrites=state.overwrites)
        if self.cfg.safety.backup_overwritten and state.overwrites:
            made = backup_files(state.overwrites, self.cfg.safety.backup_max_mb)
            trace.emit("backup", files=made)

        # --- ejecución
        state.status, state.frozen = "running", True
        for step in state.plan.steps:
            if step.status == "ok":
                continue
            step.status, step.diagnosis, step.tail = "pending", "", []
            self._run_step(state, step, trace)
            self.store.save(state)
            if step.status == "error":
                state.status = "failed"
                break
        else:
            state.status = "completed"
        trace.emit("finished", status=state.status)
        self.store.save(state)
        return state

    def _run_step(self, state: RunState, step: PlanStep, trace: Trace) -> None:
        tool = self.registry.get(step.tool_id)
        params = self._clean(step.tool_id, state)
        argv = self.ctx.build_argv(tool, params)
        step.command = argv
        for rp in self.ctx.resolve_all(tool, params, roles=("output", "modifies")):
            folder = rp.path if rp.ref.kind == "dir" else rp.path.parent
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                step.status, step.diagnosis = "error", f"No se pudo crear la carpeta de salida {folder}: {e}"
                return
        step.status, step.started_at = "running", now_iso()
        self.store.save(state)
        trace.emit("step_start", tool=tool.id, command=argv)
        self.io.say(f"▶ {tool.id}: {tool.title}")

        if tool.runner.kind == "gui":
            pid = launch_detached(argv, self.ctx.cwd_for(tool))
            step.status, step.returncode, step.ended_at, step.duration_s = "ok", 0, now_iso(), 0.0
            step.diagnosis = f"Aplicación lanzada (pid {pid}); el agente no espera a que se cierre."
            trace.emit("step_end", tool=tool.id, status="ok", gui_pid=pid)
            return

        log_path = self.store.run_dir(state.run_id) / f"{step.order:03d}_{tool.id}.log"
        res = run_command(argv, self.ctx.cwd_for(tool), tool.runner.timeout_s or self.cfg.default_timeout_s, log_path,
                          on_line=lambda ln, t=tool.id: self.io.line(t, ln), python=self.ctx.python)
        step.returncode, step.duration_s, step.tail, step.log_file = res.returncode, res.duration_s, res.tail, res.log_file
        step.ended_at = now_iso()
        if res.returncode == 0 and not res.timed_out:
            step.status = "ok"
            missing = [str(rp.path) for rp in self.ctx.resolve_all(tool, params, roles=("output",))
                       if rp.ref.kind == "file" and not rp.path.exists()]
            if missing:
                step.diagnosis = "AVISO: terminó sin error pero no encuentro la salida declarada: " + "; ".join(missing)
        else:
            step.status, step.diagnosis = "error", res.diagnosis
        trace.emit("step_end", tool=tool.id, status=step.status, returncode=res.returncode,
                   duration_s=round(res.duration_s, 2), diagnosis=step.diagnosis, log=res.log_file)

    def _ask_missing_params(self, state: RunState) -> None:
        asked = False
        for f in list(state.findings):
            if f.fix and f.fix.kind == "ask_param" and f.code == "PARAM_MISSING":
                tool = self.registry.get(f.fix.detail["tool"])
                spec = next(p for p in tool.params if p.name == f.fix.detail["param"])
                val = self.io.ask(spec, tool)
                if val not in (None, ""):
                    state.params[spec.name] = val
                    asked = True
        if asked:
            self.prepare(state)

    # ================================================================== reanudar / informar
    def resume(self, run_id: Optional[str] = None) -> RunState:
        state = self.store.load(run_id) if run_id else self.store.latest(resumable_only=True)
        if state is None:
            raise FileNotFoundError("No hay ejecuciones para reanudar.")
        for s in state.plan.steps:
            if s.status in ("error", "running"):
                s.status = "pending"
        state.confirmed = False
        return state

    def format_items(self, state: RunState):
        """Formatos que pidió el usuario («no sé cómo llenar…»): los de las herramientas nombradas, ordenados por la frase."""
        from .formats import build_guide, rank_items
        tools = state.planner.tools if state.planner else []
        items = build_guide(self.ctx, self.registry, tools or None)
        return rank_items(items, state.request) if not tools else items

    def reporter_formats(self, state: RunState) -> str:
        from .reporter import render_formats
        return render_formats(self.format_items(state))

    def report(self, state: RunState, use_llm: Optional[bool] = None, **kw) -> str:
        from .reporter import render_explain, render_tools
        p = state.planner
        if p and state.status == "info" and p.intent == "list":
            return render_tools(self.registry)
        if p and state.status == "info" and p.intent in ("ask", "explain") and state.answer is not None:
            return chat.render_answer(state.answer)
        if p and state.status == "info" and p.intent == "explain":
            return render_explain(self.registry, p.tools)
        if p and state.status == "info" and p.intent == "formats":
            return self.reporter_formats(state)
        if p and state.status == "info" and p.intent == "viability":
            return datos.render_markdown(self.data_report(), self.registry, route_ids=self.routes_for(p.tools))
        text = self.reporter.render(state, use_llm=self.cfg.llm.report_with_llm if use_llm is None else use_llm, **kw)
        if p and p.intent == "status":
            text += "\n" + datos.render_markdown(self.data_report(), self.registry, compact=True, route_ids=self.routes_for(p.tools))
        return text

    def routes_for(self, tools: Optional[list[str]]) -> Optional[list[str]]:
        """Rutas que responden una pregunta sobre estas herramientas (None = todas)."""
        ids = [r.id for r in self.registry.routes if tools and set(tools) & set(r.targets)]
        return ids or None
