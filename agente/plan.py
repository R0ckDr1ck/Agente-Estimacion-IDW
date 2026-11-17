"""Expansión de la intención del usuario a un plan ejecutable.

El SLM solo elige *qué* quiere el usuario (herramientas objetivo / workflow / rango). Este módulo, de forma
determinista, decide el resto:
  - qué pasos omite la configuración actual (skip_when, igual que EjecutarFlujo.py),
  - qué pasos previos faltan (cierre de dependencias por rutas: si la entrada de A no existe y la salida de B
    es exactamente esa ruta, B entra al plan),
  - el orden canónico.
Así el modelo no puede 'olvidar' un prerrequisito ni inventar un orden.
"""

from __future__ import annotations

from typing import Any

from .context import ProjectContext
from .models import ExecutionPlan, OmittedTool, PlanStep, PlannerOutput


def build_plan(ctx: ProjectContext, out: PlannerOutput, params: dict[str, Any]) -> ExecutionPlan:
    reg = ctx.registry
    plan = ExecutionPlan()
    selected: dict[str, PlanStep] = {}
    omitted: dict[str, str] = {}

    def candidate(tid: str, reason: str, forced: bool) -> None:
        tool = reg.get(tid)
        why_skip = ctx.skip_reasons(tool)
        if why_skip and not forced:
            omitted.setdefault(tid, "omitido por la configuración (" + "; ".join(why_skip) + ")")
            return
        if tid in selected:
            if forced:
                selected[tid].forced = True
            return
        step = PlanStep(tool_id=tid, order=tool.order, reason=reason, forced=forced)
        if why_skip:
            step.reason += f" (la configuración lo omitiría: {'; '.join(why_skip)}; se ejecuta porque lo pediste)"
        selected[tid] = step

    flow_ids = [t.id for t in reg.flow()]

    # 1) workflow, rango y herramientas pedidas explícitamente
    if out.workflow:
        w = reg.workflows[out.workflow]
        for tid in (flow_ids if w.all_flow else w.tools):
            candidate(tid, f"workflow '{w.id}'", forced=False)
    if out.from_tool or out.to_tool:
        a = flow_ids.index(out.from_tool) if out.from_tool in flow_ids else 0
        b = flow_ids.index(out.to_tool) if out.to_tool in flow_ids else len(flow_ids) - 1
        if a > b:
            plan.notes.append(f"El rango '{out.from_tool}' → '{out.to_tool}' está invertido respecto del flujo; se ignoró.")
        else:
            for tid in flow_ids[a:b + 1]:
                candidate(tid, f"rango {flow_ids[a]} → {flow_ids[b]}", forced=False)
    for tid in out.tools:
        candidate(tid, "solicitado", forced=True)

    # 2) cierre de dependencias por rutas
    excluded = set(reg.workflows[out.workflow].exclude) if out.workflow else set()
    changed = True
    guard = 0
    while changed and guard < 50:
        changed, guard = False, guard + 1
        creators = ctx.creators_index(params)
        produced = {rp.key for tid in selected for rp in ctx.resolve_all(reg.get(tid), params, roles=("output",))}
        for tid in list(selected):
            tool = reg.get(tid)
            for rp in ctx.resolve_all(tool, params, roles=("input",)):
                if rp.path.exists() or rp.key in produced:
                    continue
                for c in creators.get(rp.key, []):
                    if c.tool == tid:
                        continue
                    ctool = reg.get(c.tool)
                    if c.tool in excluded and c.tool not in selected:         # el flujo elegido no lo incluye (p. ej. el plan de sondajes)
                        omitted.setdefault(c.tool, f"produce {rp.path.name} (lo usa '{tid}'), pero el flujo «{out.workflow}» no lo incluye")
                        continue
                    why_skip = ctx.skip_reasons(ctool)
                    if why_skip:
                        omitted.setdefault(
                            c.tool, f"produce {rp.path.name} (lo necesita '{tid}') pero la configuración lo omite "
                                    f"({'; '.join(why_skip)})")
                        continue
                    if c.tool not in selected:
                        selected[c.tool] = PlanStep(
                            tool_id=c.tool, order=ctool.order,
                            reason=f"genera {rp.path.name}, que necesita '{tid}' y todavía no existe")
                        changed = True
                    produced.add(rp.key)

    plan.steps = sorted(selected.values(), key=lambda s: (s.order, s.tool_id))
    plan.omitted = [OmittedTool(tool_id=k, reason=v) for k, v in omitted.items() if k not in selected]
    return plan
