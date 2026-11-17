"""Contexto del proyecto: lee los conf_*.json y resuelve rutas y condiciones del catálogo.

Es la única pieza que 'entiende' los JSON del proyecto; el resto del agente trabaja con ResolvedPath.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import jpath
from .catalog import ToolRegistry
from .config import AgentConfig
from .models import Condition, PathRef, ToolSpec


def norm_path(p: Path | str) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


@dataclass
class ResolvedPath:
    tool: str
    role: str                    # input | output | modifies
    ref: PathRef
    cfg: Optional[str]           # conf del que salió (None si literal / parámetro)
    jpath: Optional[str]         # ruta concreta dentro del conf
    raw: str                     # texto tal como está en el conf
    path: Path
    assumed: bool = False        # el conf no existe: la ruta sale de los valores por defecto de su plantilla

    @property
    def key(self) -> str:
        return norm_path(self.path)


class ProjectContext:
    def __init__(self, cfg: AgentConfig, registry: ToolRegistry):
        self.cfg = cfg
        self.registry = registry
        self.root: Path = cfg.idw_root
        self.config_dir: Path = self.root / (registry.config_dir or cfg.config_dir)
        self.python: str = cfg.python
        self._cache: dict[str, tuple[tuple[int, int], Any, Optional[str]]] = {}

    # ------------------------------------------------------------------ conf_*.json
    def cfg_path(self, name: str) -> Path:
        return self.config_dir / name

    def load_cfg(self, name: str) -> tuple[Optional[dict], Optional[str]]:
        """(datos, error). Cachea por (mtime, tamaño) para no releer los 112 KB de conf_estimacion en cada chequeo."""
        p = self.cfg_path(name)
        try:
            st = p.stat()
        except OSError:
            return None, f"no existe {p}"
        sig = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(name)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            err = None if isinstance(data, dict) else "la raíz del JSON no es un objeto"
            data = data if err is None else None
        except (OSError, ValueError) as e:
            data, err = None, f"JSON inválido: {e}"
        self._cache[name] = (sig, data, err)
        return data, err

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------ condiciones
    def cond_met(self, cond: Condition, tool: ToolSpec) -> bool:
        name = cond.config or tool.config
        data = self.load_cfg(name)[0] if name else None
        found = jpath.get_all(data, cond.key) if data is not None else []
        present = bool(found)
        value = found[0][1] if present else cond.default
        if cond.op == "eq":
            return value == cond.value
        if cond.op == "ne":
            return value != cond.value
        if cond.op == "truthy":
            return bool(value)
        if cond.op == "falsy":
            return not bool(value)
        if cond.op == "exists":
            return present
        return not present          # missing

    def skip_reasons(self, tool: ToolSpec) -> list[str]:
        """Motivos por los que la configuración actual omite esta herramienta del flujo ([] = no se omite)."""
        out = []
        for c in tool.skip_when:
            if self.cond_met(c, tool):
                name = c.config or tool.config
                val = self._value(c, tool)
                out.append(f"{name}: {c.key} = {json.dumps(val, ensure_ascii=False)}")
        return out

    def _value(self, cond: Condition, tool: ToolSpec) -> Any:
        name = cond.config or tool.config
        data = self.load_cfg(name)[0] if name else None
        found = jpath.get_all(data, cond.key) if data is not None else []
        return found[0][1] if found else cond.default

    # ------------------------------------------------------------------ rutas
    def to_path(self, raw: str) -> Path:
        p = Path(os.path.expandvars(raw))
        return p if p.is_absolute() else self.root / p

    # Carpetas donde el agente acepta leer/escribir archivos del proyecto:
    #   sin carpetas elegidas  -> la raíz del proyecto;
    #   con carpetas elegidas  -> carpeta de ENTRADA + carpeta de SALIDA + Configuraciones (las herramientas siguen en la raíz).
    def allowed_roots(self) -> list[Path]:
        cfg = self.cfg
        roots = [self.config_dir, cfg.data_dir, cfg.results_dir] if cfg.has_workspace else [self.root]
        return [Path(r) for r in roots if r] + [Path(x) for x in cfg.safety.extra_allowed_roots]

    def expand_root(self, text: str) -> str:
        """Sustituye {root} en los valores por defecto del catálogo; con carpetas elegidas, 01_Data y 02_Resultados
        apuntan a ellas (no a la raíz del proyecto)."""
        if self.cfg.has_workspace:
            text = (text.replace("{root}/01_Data", str(self.cfg.data_dir))
                        .replace("{root}/02_Resultados", str(self.cfg.results_dir)))
        text = text.replace("{root}", str(self.root))
        return os.path.normpath(text) if len(text) > 2 and text[1] == ":" else text

    def anchor_dir(self, anchor: str) -> Path:
        """Dónde vive hoy la carpeta ancla ('01_Data' -> carpeta de entrada elegida, '02_Resultados' -> carpeta de salida)."""
        if self.cfg.has_workspace:
            if anchor == "01_Data":
                return self.cfg.data_dir                      # type: ignore[return-value]
            if anchor == "02_Resultados":
                return self.cfg.results_dir                   # type: ignore[return-value]
            if anchor == self.cfg.config_dir:
                return self.config_dir
        return self.root / anchor

    def inside_root(self, p: Path) -> bool:
        """¿Está `p` dentro de alguna carpeta permitida?"""
        pp = Path(norm_path(p))
        for base in self.allowed_roots():
            try:
                pp.relative_to(Path(norm_path(base)))
                return True
            except ValueError:
                continue
        return False

    def resolve(self, tool: ToolSpec, ref: PathRef, role: str, params: dict[str, Any],
                problems: Optional[list[str]] = None) -> list[ResolvedPath]:
        """Rutas concretas de una referencia. [] si no aplica (when) o no se pudo leer; en ese caso
        el motivo se añade a `problems` para que el preflight lo reporte."""
        if not all(self.cond_met(c, tool) for c in ref.when):
            return []
        sink = problems if problems is not None else []
        assumed = False
        raws: list[tuple[Optional[str], Optional[str], Optional[str], str]] = []   # (cfg, jpath, _, raw)
        if ref.param is not None:
            val = params.get(ref.param)
            if val in (None, ""):
                sink.append(f"parámetro '{ref.param}' sin valor")
                return []
            raws.append((None, None, None, str(val)))
        elif ref.literal is not None:
            raws.append((None, None, None, ref.literal))
        else:
            name = ref.config or tool.config
            data, err = self.load_cfg(name) if name else (None, "sin config")
            if data is None and err and err.startswith("no existe"):
                data = self.template_defaults(name)         # conf ausente: se infiere de su plantilla
                assumed = data is not None
            if data is None:
                sink.append(f"no se pudo leer {name}: {err}")
                return []
            found = jpath.get_all(data, ref.key or "")
            if not found:
                sink.append(f"{name}: falta la clave '{ref.key}'")
                return []
            for jp, val in found:
                if isinstance(val, str) and val.strip():
                    raws.append((name, jp, None, val))
                else:
                    sink.append(f"{name}: '{jp}' está vacío o no es texto")
        out = []
        for cfgname, jp, _, raw in raws:
            p = self.to_path(raw)
            if ref.join:
                p = p / ref.join
            out.append(ResolvedPath(tool.id, role, ref, cfgname, jp, raw, p, assumed))
        return out

    def template_defaults(self, cfg_name: str) -> Optional[dict]:
        """Valores por defecto de la plantilla del conf `cfg_name` (si alguna herramienta lo declara)."""
        from .schemas import default_config
        for t in self.registry:
            if t.config == cfg_name:
                d = default_config(t, self.root, self.expand_root)
                if d is not None:
                    return d
        return None

    def resolve_all(self, tool: ToolSpec, params: dict[str, Any], roles: tuple[str, ...] = ("input", "output", "modifies"),
                    problems: Optional[list[str]] = None) -> list[ResolvedPath]:
        out: list[ResolvedPath] = []
        groups = {"input": tool.inputs, "output": tool.outputs, "modifies": tool.modifies}
        for role in roles:
            for ref in groups[role]:
                out += self.resolve(tool, ref, role, params, problems)
        return out

    def creators_index(self, params: dict[str, Any]) -> dict[str, list[ResolvedPath]]:
        """ruta normalizada -> salidas (de cualquier herramienta) que la crean. Base de la inferencia de dependencias."""
        idx: dict[str, list[ResolvedPath]] = {}
        for t in self.registry:
            for rp in self.resolve_all(t, params, roles=("output",)):
                idx.setdefault(rp.key, []).append(rp)
        return idx

    # ------------------------------------------------------------------ comando
    def build_argv(self, tool: ToolSpec, params: dict[str, Any]) -> list[str]:
        """Línea de comando exacta: [python, script, *args]. Solo se sustituyen placeholders del catálogo;
        nunca se pasa por un shell."""
        py = self.python
        if tool.runner.kind == "gui":
            pyw = Path(py).with_name("pythonw.exe")
            py = str(pyw) if pyw.exists() else py
        script = str((self.root / tool.runner.script).resolve())
        args = []
        for a in tool.runner.args:
            a = a.replace("{root}", str(self.root)).replace("{config_dir}", str(self.config_dir))
            if tool.config:
                a = a.replace("{config}", str(self.cfg_path(tool.config)))
            for k, v in params.items():
                a = a.replace("{param." + k + "}", str(v))
            args.append(a)
        flags = ["-X", "utf8"]
        return [py, *flags, script, *args]

    def cwd_for(self, tool: ToolSpec) -> Path:
        if tool.runner.cwd:
            return self.root / tool.runner.cwd
        return (self.root / tool.runner.script).resolve().parent
