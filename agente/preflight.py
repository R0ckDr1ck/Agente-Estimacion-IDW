"""Preflight Validator: todo lo que puede comprobarse ANTES de ejecutar, sin tocar el proyecto.

Por cada paso del plan verifica: intérprete y script, conf (existe / JSON válido / módulo / claves / tipos),
parámetros, dependencias Python, entradas (existen / las produce un paso previo / apuntan fuera de la raíz),
salidas (carpeta, sobrescritura) y coherencia de la cadena. Cada hallazgo trae un `fix` accionable.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import ProjectContext, ResolvedPath, norm_path
from .models import ExecutionPlan, FixAction, Finding, ToolSpec
from .schemas import validate_config, validate_params

_DEP_CACHE: dict[tuple[str, tuple[str, ...]], list[str]] = {}


@dataclass
class PreflightResult:
    findings: list[Finding] = field(default_factory=list)
    overwrites: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]


def missing_modules(python: str, modules: list[str]) -> list[str]:
    """Módulos que NO se pueden importar con el intérprete de las herramientas (no necesariamente el del agente)."""
    key = (python, tuple(modules))
    if key in _DEP_CACHE:
        return _DEP_CACHE[key]
    if not modules:
        return []
    if norm_path(python) == norm_path(sys.executable):
        miss = [m for m in modules if importlib.util.find_spec(m.split(".")[0]) is None]
    else:
        code = ("import importlib.util as u,json,sys;"
                "print(json.dumps([m for m in sys.argv[1:] if u.find_spec(m.split('.')[0]) is None]))")
        try:
            r = subprocess.run([python, "-I", "-c", code, *modules], capture_output=True, text=True, timeout=60)
            miss = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else list(modules)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            miss = list(modules)
    _DEP_CACHE[key] = miss
    return miss


def will_overwrite(p: Path) -> bool:
    if p.is_file():
        return True
    if p.is_dir():
        try:
            return any(p.iterdir())
        except OSError:
            return False
    return False


def run_preflight(ctx: ProjectContext, plan: ExecutionPlan, params: dict[str, Any]) -> PreflightResult:
    res = PreflightResult()
    seen: set[tuple[str, str, str]] = set()

    def add(tool: str, code: str, sev: str, msg: str, hint: str = "", fix: FixAction | None = None) -> None:
        k = (tool, code, msg)
        if k in seen:
            return
        seen.add(k)
        res.findings.append(Finding(tool=tool, code=code, severity=sev, message=msg, hint=hint, fix=fix))  # type: ignore[arg-type]

    if not Path(ctx.python).exists():
        add("-", "PY_MISSING", "error", f"No existe el intérprete configurado: {ctx.python}",
            "Corrige 'python' en config/agent.yaml (o la variable AGENTE_PYTHON).")
    if not ctx.root.is_dir():
        add("-", "ROOT_MISSING", "error", f"No existe la raíz del proyecto: {ctx.root}",
            "Corrige 'idw_root' en config/agent.yaml (o usa --root).")
        return res

    if ctx.cfg.has_workspace:
        for label, d in (("de entrada", ctx.cfg.data_dir), ("de salida", ctx.cfg.results_dir)):
            if d is not None and not d.is_dir():
                add("-", "WORKSPACE_MISSING", "error", f"No existe la carpeta {label}: {d}",
                    "Elige otra carpeta en la pantalla inicial de la interfaz.")

    creators = ctx.creators_index(params)
    produced: set[str] = set()
    plan_ids = {s.tool_id for s in plan.steps}

    for step in plan.steps:
        tool = ctx.registry.get(step.tool_id)
        tid = tool.id

        # ---- script
        script = ctx.root / tool.runner.script
        if not script.is_file():
            add(tid, "SCRIPT_MISSING", "error", f"No existe el script {script}",
                "Verifica 'runner.script' del catálogo o restaura el archivo.")

        # ---- omisión por configuración
        why_skip = ctx.skip_reasons(tool)
        if why_skip:
            add(tid, "SKIPPED_FORCED", "warn",
                f"'{tid}' estaría omitido por la configuración ({'; '.join(why_skip)}), pero se ejecutará porque se pidió.",
                "Si no era lo que querías, quítalo de la solicitud.")

        # ---- conf
        _check_configs(ctx, tool, add)

        # ---- parámetros
        clean, perrs = validate_params(tool, params)
        for name, kind, msg in perrs:
            spec = next(p for p in tool.params if p.name == name)
            add(tid, "PARAM_MISSING" if kind == "missing" else "PARAM_INVALID", "error", msg,
                f"{spec.description} {('Por qué: ' + spec.why) if spec.why else ''} "
                f"{('Ejemplo: ' + spec.example) if spec.example else ''}".strip(),
                FixAction(kind="ask_param", detail={"tool": tid, "param": name, "type": spec.type,
                                                    "why": spec.why, "example": spec.example,
                                                    "description": spec.description}))
        for spec in tool.params:
            if spec.type == "path" and spec.name in clean:
                p = ctx.to_path(str(clean[spec.name]))
                if not ctx.inside_root(p):
                    add(tid, "PARAM_FORBIDDEN_PATH", "error",
                        f"El parámetro '{spec.name}' apunta fuera de las carpetas permitidas: {p}",
                        "Usa un archivo de tu carpeta de entrada/salida o agrega la carpeta a safety.extra_allowed_roots.")

        # ---- dependencias Python
        if tool.requires_python:
            miss = missing_modules(ctx.python, [d.module for d in tool.requires_python])
            for d in tool.requires_python:
                if d.module in miss:
                    cmd = f'"{ctx.python}" -m pip install {d.pip or d.module}'
                    add(tid, "DEP_MISSING", "error", f"Falta el paquete Python '{d.module}' en {ctx.python}",
                        f"Instálalo con:  {cmd}", FixAction(kind="install_dep", detail={"module": d.module}, command=cmd))

        # ---- entradas
        problems: list[str] = []
        inputs = ctx.resolve_all(tool, clean or params, roles=("input",), problems=problems)
        for pb in problems:
            if "falta la clave" in pb:
                add(tid, "CFG_KEY_MISSING", "error", pb, "Completa la clave en el conf (pestaña «Parámetros» de la interfaz).")
            elif "vacío" in pb:
                add(tid, "PATH_EMPTY", "error", pb, "El conf tiene una ruta vacía.")
        for rp in inputs:
            _check_input(ctx, rp, produced, creators, plan_ids, add)

        # ---- salidas / modificaciones
        problems = []
        for rp in ctx.resolve_all(tool, clean or params, roles=("output", "modifies"), problems=problems):
            _check_output(ctx, tool, rp, produced, res, add)

        for rp in ctx.resolve_all(tool, clean or params, roles=("output",)):
            produced.add(rp.key)

    return res


# ---------------------------------------------------------------------------------------------------
def _within(p: Path, base: Path) -> bool:
    try:
        Path(norm_path(p)).relative_to(Path(norm_path(base)))
        return True
    except ValueError:
        return False


def _check_configs(ctx: ProjectContext, tool: ToolSpec, add) -> None:
    names = ([tool.config] if tool.config else []) + list(tool.also_configs)
    for name in names:
        data, err = ctx.load_cfg(name)
        if data is None:
            if err and err.startswith("no existe"):
                has_t = name == tool.config and tool.config_template and tool.config_template.fields
                if has_t:
                    add(tool.id, "CFG_MISSING", "error", f"Falta el archivo de configuración {ctx.cfg_path(name)}",
                        "Se generó/propone una plantilla con valores por defecto para que la completes.",
                        FixAction(kind="generate_template", detail={"tool": tool.id, "kind": "config",
                                                                    "target": str(ctx.cfg_path(name))}))
                else:
                    add(tool.id, "CFG_MISSING", "error", f"Falta el archivo de configuración {ctx.cfg_path(name)}",
                        "No hay plantilla automática para este conf: descarga el formato en la pestaña «Formatos y plantillas» "
                        "o cópialo de otro proyecto.",
                        FixAction(kind="manual", detail={"target": str(ctx.cfg_path(name))}))
            else:
                add(tool.id, "CFG_INVALID", "error", f"{name}: {err}", "Corrige el JSON (comas, comillas, llaves).")
            continue
        if name != tool.config:
            continue
        if tool.config_template and tool.config_template.module:
            got = data.get("__modulo__")
            if got != tool.config_template.module:
                add(tool.id, "CFG_MODULE_MISMATCH", "warn",
                    f"{name}: __modulo__ es {got!r}, el motor espera {tool.config_template.module!r}",
                    "Puede ser un conf de otra herramienta.")
        from . import jpath
        for key in tool.required_keys:
            if not jpath.exists(data, key):
                add(tool.id, "CFG_KEY_MISSING", "error", f"{name}: falta la clave obligatoria '{key}'",
                    "Complétala en el conf.")
        for pb in validate_config(tool, data):
            add(tool.id, "CFG_SCHEMA", "warn", f"{name}: {pb}", "El valor no coincide con el esquema declarado en el catálogo.")


def _check_input(ctx: ProjectContext, rp: ResolvedPath, produced: set[str],
                 creators: dict[str, list[ResolvedPath]], plan_ids: set[str], add) -> None:
    tid, ref, p = rp.tool, rp.ref, rp.path
    where = f"{rp.cfg}:{rp.jpath}" if rp.cfg else ref.name
    if p.exists():
        if p.is_file() and p.stat().st_size == 0:
            add(tid, "INPUT_EMPTY", "warn", f"El archivo de entrada está vacío: {p}", f"Referencia: {where}")
        if not ctx.inside_root(p):
            add(tid, "STALE_PATH", "warn",
                f"'{where}' apunta fuera de la raíz del proyecto: {p}",
                "Probablemente es una ruta de otra versión del proyecto. Usa 'fix-paths' para reubicarla.")
        return
    if rp.key in produced:
        add(tid, "INPUT_WILL_BE_PRODUCED", "info", f"{p.name} aún no existe; lo generará un paso previo del plan.")
        return
    sev = "warn" if ref.optional else "error"
    producers = creators.get(rp.key, [])
    if producers:
        names = ", ".join(sorted({c.tool for c in producers}))
        fix = None
        flags = [c for c in ref.when if c.op == "truthy"]
        if not (set(c.tool for c in producers) & plan_ids) and len(ref.when) == 1 and len(flags) == 1:
            cond = flags[0]                                  # la entrada solo se exige si un interruptor del conf está activo
            fname = cond.config or ctx.registry.get(tid).config
            fix = FixAction(kind="edit_config", detail={"file": fname, "values": {cond.key: False},
                                                         "label": f"Desactivar «{cond.key}» en {fname}"})
        add(tid, "CHAIN_PRODUCER_SKIPPED" if not (set(c.tool for c in producers) & plan_ids) else "CHAIN_ORDER", sev,
            f"Falta {p} ({where}); lo produce '{names}', que no está en el plan.",
            f"Incluye '{names}' en la solicitud o revisa por qué la configuración lo omite."
            + (f" Si no lo necesitas, desactiva «{flags[0].key}» (botón): el paso lo omitirá." if fix else ""),
            fix)
        return
    if ref.template:
        add(tid, "INPUT_MISSING", sev, f"Falta el archivo de entrada {p} ({where}).",
            "Se generó/propone un archivo de ejemplo con las columnas esperadas.",
            FixAction(kind="generate_template", detail={"tool": tid, "kind": "data", "ref": ref.name,
                                                        "template": ref.template, "target": str(p)}))
        return
    hint = "Ninguna herramienta del catálogo produce esa ruta con la configuración actual."
    others = {c.path.name: c for lst in creators.values() for c in lst if c.tool != tid}
    alt = difflib.get_close_matches(p.name, list(others), n=1, cutoff=0.75)
    if alt:
        c = others[alt[0]]
        hint += f" ¿Quisiste apuntar a {c.path} (salida de '{c.tool}')? Revisa el conf de '{tid}'."
    add(tid, "INPUT_MISSING", sev, f"Falta {p} ({where}).", hint,
        FixAction(kind="manual", detail={"target": str(p), "where": where}))


def _check_output(ctx: ProjectContext, tool: ToolSpec, rp: ResolvedPath, produced: set[str],
                  res: PreflightResult, add) -> None:
    p = rp.path
    folder = p if rp.ref.kind == "dir" else p.parent
    if not folder.exists():
        anc = folder
        while not anc.exists() and anc.parent != anc:
            anc = anc.parent
        if not anc.exists():
            add(tool.id, "OUTPUT_DIR_UNREACHABLE", "error", f"No se puede crear la carpeta de salida {folder} (no existe la unidad/base).",
                "Revisa la ruta de salida en el conf.")
        else:
            add(tool.id, "OUTPUT_DIR_CREATE", "info", f"Se creará la carpeta de salida {folder}.")
    if not ctx.inside_root(p) and rp.role == "output":
        add(tool.id, "STALE_PATH", "warn", f"La salida '{rp.cfg}:{rp.jpath}' apunta fuera de la raíz del proyecto: {p}",
            "Usa 'fix-paths' para reubicarla dentro del proyecto.")
    if rp.key in produced:
        return
    if tool.destructive and will_overwrite(p):
        s = str(p)
        if s not in res.overwrites:
            res.overwrites.append(s)
        add(tool.id, "OVERWRITE", "warn", f"Se sobrescribirá/modificará {p}",
            tool.destructive_reason or "Requiere confirmación explícita.")
