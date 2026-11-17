"""Reporter: explica al usuario qué se hizo, qué faltó y qué sigue.

Principio: el informe es DETERMINISTA (se arma con hechos del estado). El SLM, si está disponible, solo agrega un
resumen en prosa y prioriza acciones; su salida se valida (las acciones deben existir en `acciones_permitidas`)
y, si falla, simplemente no se muestra. Así un modelo pequeño nunca puede introducir hechos falsos.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .catalog import ToolRegistry
from .llm import LLMClient, LLMError
from .models import Finding, RunState
from .planner import extract_json

ICON = {"ok": "✔", "error": "✘", "pending": "·", "running": "▶", "skipped": "–", "blocked": "■"}
SEV = {"error": "ERROR", "warn": "AVISO", "info": "info"}
ESTADO = {
    "info": "informativo", "needs_input": "necesito más información", "blocked": "BLOQUEADO (faltan prerrequisitos)",
    "ready": "listo para ejecutar (modo simulación: nada se ejecutó)", "running": "en ejecución",
    "failed": "FALLÓ (puede reanudarse)", "completed": "completado", "aborted": "abortado por el usuario",
}


def allowed_actions(state: RunState, agent_cmd: str = "python -m agente") -> list[dict[str, str]]:
    """Acciones que el agente SABE hacer a partir del estado. Es la única fuente para 'qué sigue'."""
    acts: list[dict[str, str]] = []
    rid = state.run_id
    if any(f.fix and f.fix.kind == "ask_param" for f in state.findings):
        acts.append({"id": "completar_parametros", "descripcion": "Aportar los parámetros que faltan",
                     "comando": f"{agent_cmd} exec {rid} --param NOMBRE=VALOR"})
    fixes = [x for x in state.path_fixes if x.new and not x.ambiguous]
    if fixes:
        nfiles = len({x.config_file for x in fixes})
        acts.append({"id": "corregir_rutas", "descripcion": f"Reubicar {len(fixes)} ruta(s) en {nfiles} conf para que usen tus carpetas (con respaldo)",
                     "comando": f"{agent_cmd} fix-paths --apply"})
    pend = [g for g in state.generated if not g.promoted]
    if any(g.kind == "config" for g in pend):
        acts.append({"id": "usar_plantillas", "descripcion": "Copiar las plantillas de configuración generadas a su destino (sin sobrescribir); revisa sus valores",
                     "comando": f"{agent_cmd} promote {rid} --kind config"})
    if any(g.kind == "data" for g in pend):
        acts.append({"id": "usar_datos_ejemplo", "descripcion": "Copiar los archivos de datos de EJEMPLO (ficticios; solo para probar el flujo, nunca para resultados reales)",
                     "comando": f"{agent_cmd} promote {rid} --kind all"})
    for f in state.findings:
        if f.fix and f.fix.kind == "install_dep" and f.fix.command:
            acts.append({"id": f"instalar_{f.fix.detail.get('module')}", "descripcion": f"Instalar {f.fix.detail.get('module')}",
                         "comando": f.fix.command})
    if state.status in ("ready", "failed", "aborted") and state.plan.steps:
        confirm = " --yes" if state.overwrites else ""
        acts.append({"id": "ejecutar", "descripcion": "Ejecutar (o reanudar) los pasos pendientes",
                     "comando": f"{agent_cmd} exec {rid}{confirm}"})
    if state.status == "blocked":
        acts.append({"id": "reverificar", "descripcion": "Volver a verificar tras resolver los faltantes",
                     "comando": f"{agent_cmd} exec {rid} --dry-run"})
    return acts


def render_tools(reg: ToolRegistry) -> str:
    out = ["# Herramientas disponibles", ""]
    out += ["| Orden | Id | Qué hace | Parámetros |", "|---|---|---|---|"]
    for t in reg:
        ps = ", ".join(f"`{p.name}`{'*' if p.required else ''}" for p in t.params) or "—"
        what = t.summary or t.description.strip().splitlines()[0]
        out.append(f"| {t.order if t.category == 'flujo' else '—'} | `{t.id}` | {what} | {ps} |")
    if reg.workflows:
        out += ["", "## Workflows", ""] + [f"- `{w.id}`: {w.description}" for w in reg.workflows.values()]
    out += ["", "(* = obligatorio)"]
    return "\n".join(out)


def render_formats(items) -> str:
    from .formats import render_guide_markdown
    return render_guide_markdown(items)


def render_explain(reg: ToolRegistry, ids: list[str]) -> str:
    out = []
    for tid in ids:
        t = reg.get(tid)
        out += [f"## `{t.id}` — {t.title}", "", (t.summary or t.description).strip(), ""]
        if t.config:
            out.append(f"- Configuración: `{t.config}`")
        if t.inputs:
            out.append("- Lee: " + ", ".join(f"`{r.name}`" for r in t.inputs))
        if t.outputs:
            out.append("- Genera: " + ", ".join(f"`{r.name}`" for r in t.outputs))
        if t.destructive:
            out.append(f"- ⚠ Destructivo: {t.destructive_reason or 'sobrescribe datos'} (pide confirmación)")
        out += [f"- Sugerencia: {h}" for h in t.hints]
        if t.examples:
            out.append("- Puedes pedirlo así: " + " · ".join(f"«{e}»" for e in t.examples[:3]))
        out.append("")
    return "\n".join(out)


class Reporter:
    def __init__(self, reg: ToolRegistry, prompts_dir, client: Optional[LLMClient] = None):
        self.reg = reg
        self.client = client
        self._prompt = ""
        try:
            raw = (prompts_dir / "reporter_system.md").read_text(encoding="utf-8")
            self._prompt = re.sub(r"<!--.*?-->\s*", "", raw, flags=re.S)
        except OSError:
            self.client = None

    # ------------------------------------------------------------------ resumen opcional del SLM
    def llm_summary(self, state: RunState, actions: list[dict[str, str]]) -> Optional[dict[str, Any]]:
        if self.client is None:
            return None
        facts = {
            "solicitud": state.request, "estado": state.status,
            "pasos": [{"id": s.tool_id, "estado": s.status, "motivo": s.reason, "error": s.diagnosis} for s in state.plan.steps],
            "hallazgos": [{"severidad": f.severity, "mensaje": f.message, "sugerencia": f.hint}
                          for f in state.findings if f.severity != "info"][:12],
            "acciones_permitidas": [{"id": a["id"], "descripcion": a["descripcion"]} for a in actions],
        }
        try:
            raw = self.client.chat([{"role": "system", "content": self._prompt},
                                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}])
            data = extract_json(raw)
            resumen = str(data.get("resumen", "")).strip()
            allowed = {a["id"] for a in actions}
            seguir = [x for x in data.get("que_sigue", []) if isinstance(x, str) and x in allowed][:3]
            if not resumen or len(resumen) > 900:
                return None
            return {"resumen": resumen, "que_sigue": seguir}
        except (LLMError, ValueError, AttributeError):
            return None

    # ------------------------------------------------------------------ informe
    def header_facts(self, state: RunState) -> list[tuple[str, str]]:
        """Datos de cabecera del informe como pares (etiqueta, valor): la interfaz los dibuja como tarjetas y el
        texto los muestra uno por línea (antes salían todos en una sola línea)."""
        facts = [("Ejecución", state.run_id), ("Solicitud", state.request), ("Estado", ESTADO.get(state.status, state.status))]
        if state.planner_source:
            src = {"slm": "SLM local", "rules": "REGLAS por palabras clave (fallback)",
                   "provided": "plan indicado explícitamente"}.get(state.planner_source, state.planner_source)
            if state.metrics and state.metrics.model and state.planner_source == "slm":
                src += f" · {state.metrics.model}"
            conf = f" · confianza {state.planner.confidence:.0%}" if state.planner and state.planner_source == "slm" else ""
            facts.append(("Interpretación", src + conf))
            if state.planner and state.planner.rationale:
                facts.append(("Entendí", state.planner.rationale))
        return facts

    def render(self, state: RunState, use_llm: bool = True, header: bool = True, show_next: bool = True) -> str:
        actions = allowed_actions(state)
        L: list[str] = []
        if header:
            L.append(f"# Informe · ejecución `{state.run_id}`")
            L.append("")
            for label, value in self.header_facts(state)[1:]:
                L.append(f"- **{label}:** {value}")
        for n in state.planner_notes:
            L += ["", f"> {n}"]
        prio: list[str] = []
        if use_llm:
            s = self.llm_summary(state, actions)
            if s:
                L += ["", f"**Resumen (SLM):** {s['resumen']}"]
                prio = s["que_sigue"]

        if state.question:
            L += ["", "## Necesito aclarar algo", state.question]

        if state.plan.steps:
            L += ["", "## Plan", "", "| # | Herramienta | Por qué está en el plan | Estado | Tiempo |", "|---|---|---|---|---|"]
            for i, s in enumerate(state.plan.steps, 1):
                t = f"{s.duration_s:.1f} s" if s.duration_s is not None else ""
                L.append(f"| {i} | `{s.tool_id}` | {s.reason} | {ICON.get(s.status, '?')} {s.status} | {t} |")
        if state.plan.omitted:
            L += ["", "**Omitidos:**"] + [f"- `{o.tool_id}`: {o.reason}" for o in state.plan.omitted]
        for n in state.plan.notes:
            L.append(f"> {n}")

        if state.findings:
            L += ["", "## Verificación previa"]
            for sev in ("error", "warn", "info"):
                group = [f for f in state.findings if f.severity == sev]
                if not group:
                    continue
                L.append(f"\n**{SEV[sev]} ({len(group)})**")
                for f in group:
                    L.append(f"- [{f.tool}] {f.message}" + (f"\n    → {f.hint}" if f.hint else ""))
        elif state.plan.steps:
            L += ["", "## Verificación previa", "Sin hallazgos: todo en orden."]

        if state.path_fixes:
            L += ["", "## Rutas que no coinciden con tus carpetas"]
            by_file: dict[str, list] = {}
            for x in state.path_fixes:
                by_file.setdefault(x.config_file, []).append(x)
            for name, group in by_file.items():
                ok = [x for x in group if x.new and not x.ambiguous]
                bad = [x for x in group if not (x.new and not x.ambiguous)]
                L.append(f"- `{name}`: {len(ok)} ruta(s) por reubicar" + (f", {len(bad)} sin equivalente" if bad else ""))
                for x in ok[:2]:
                    L.append(f"    · `{x.jpath}`: {x.old}\n      ⇒ {x.new}")
                if len(ok) > 2:
                    L.append(f"    · … y {len(ok) - 2} más")
                for x in bad[:3]:
                    L.append(f"    · ⚠ `{x.jpath}`: {x.old} — {x.reason}" + (": " + "; ".join(x.ambiguous) if x.ambiguous else ""))

        if state.generated:
            L += ["", "## Archivos de ejemplo generados"]
            for g in state.generated:
                L.append(f"- {g.path}\n    destino: {g.target}  {'(ya copiado)' if g.promoted else '(pendiente de copiar)'}")

        if state.overwrites:
            L += ["", "## ⚠ Se sobrescribirá (requiere confirmación explícita)"] + [f"- {p}" for p in state.overwrites]

        bad = [s for s in state.plan.steps if s.status == "error"]
        for s in bad:
            L += ["", f"## Falla en `{s.tool_id}`", s.diagnosis or "sin diagnóstico"]
            if s.tail:
                L += ["```", *s.tail[-12:], "```"]
            if s.log_file:
                L.append(f"Log completo: {s.log_file}")

        if actions and show_next:
            L += ["", "## Qué sigue"]
            for a in sorted(actions, key=lambda a: prio.index(a["id"]) if a["id"] in prio else len(prio)):
                star = " ⭐" if a["id"] in prio else ""
                L.append(f"- {a['descripcion']}{star}:  `{a['comando']}`")
        return "\n".join(L) + "\n"
