"""Tool Registry: carga y valida el catálogo declarativo (YAML) y lo expone al resto del agente.

Agregar una herramienta = añadir una entrada a un YAML de catalog/. Ningún código del agente cambia.
La validación cruzada (ids únicos, referencias a plantillas, workflows) falla al cargar, con mensaje claro.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import Catalog, DataKind, FileTemplate, ParamTable, Route, ToolSpec, WorkflowSpec


class CatalogError(Exception):
    """El catálogo es inválido (se informa qué archivo y qué campo)."""


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec], workflows: list[WorkflowSpec],
                 file_templates: dict[str, FileTemplate], config_dir: str | None, sources: list[str],
                 param_tables: dict[str, ParamTable] | None = None, column_roles: dict[str, list[str]] | None = None,
                 data_kinds: list[DataKind] | None = None, routes: list[Route] | None = None):
        dup = sorted({t.id for t in tools if [x.id for x in tools].count(t.id) > 1})
        if dup:
            raise CatalogError(f"Catálogo inconsistente: ids de herramienta duplicados: {', '.join(dup)}")
        self._tools = {t.id: t for t in sorted(tools, key=lambda t: (t.order, t.id))}
        self.workflows = {w.id: w for w in workflows}
        self.file_templates = file_templates
        self.param_tables = param_tables or {}
        self.column_roles = column_roles or {}                      # v3: viabilidad con todos los datos
        self.data_kinds = {k.id: k for k in (data_kinds or [])}
        self.routes = list(routes or [])
        self.config_dir = config_dir
        self.sources = sources
        self._validate()

    # ------------------------------------------------------------------ carga
    @classmethod
    def load(cls, paths: list[Path]) -> "ToolRegistry":
        tools: list[ToolSpec] = []
        workflows: list[WorkflowSpec] = []
        templates: dict[str, FileTemplate] = {}
        tables: dict[str, ParamTable] = {}
        roles: dict[str, list[str]] = {}
        kinds: list[DataKind] = []
        routes: list[Route] = []
        config_dir: str | None = None
        for p in paths:
            try:
                raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
                cat = Catalog.model_validate(raw)
            except FileNotFoundError:
                raise CatalogError(f"No existe el catálogo: {p}") from None
            except (yaml.YAMLError, ValidationError) as e:
                raise CatalogError(f"Catálogo inválido ({p}):\n{e}") from e
            tools += cat.tools
            workflows += cat.workflows
            config_dir = config_dir or cat.config_dir
            for tid, ft in cat.file_templates.items():
                templates[tid] = ft.model_copy(update={"id": tid})
            for tid, pt in cat.param_tables.items():
                tables[tid] = pt.model_copy(update={"id": tid})
            roles.update(cat.column_roles)
            kinds += cat.data_kinds
            routes += cat.routes
        return cls(tools, workflows, templates, config_dir, [str(p) for p in paths], tables, roles, kinds, routes)

    def _validate(self) -> None:
        problems: list[str] = []
        for t in self._tools.values():
            for ref in t.inputs + t.outputs + t.modifies:
                if ref.template and ref.template not in self.file_templates:
                    problems.append(f"{t.id}: la plantilla '{ref.template}' (ref '{ref.name}') no existe en file_templates")
                if ref.param and ref.param not in {p.name for p in t.params}:
                    problems.append(f"{t.id}: la ref '{ref.name}' usa el parámetro '{ref.param}' que no está declarado")
                if ref.key and not (ref.config or t.config):
                    problems.append(f"{t.id}: la ref '{ref.name}' usa key sin config")
            if t.runner.kind == "script" and not t.runner.script.endswith(".py"):
                problems.append(f"{t.id}: runner.script debe ser un .py")
        for w in self.workflows.values():
            for tid in w.tools + w.exclude:
                if tid not in self._tools:
                    problems.append(f"workflow {w.id}: herramienta desconocida '{tid}'")
        for k in self.data_kinds.values():
            if k.template and k.template not in self.file_templates:
                problems.append(f"data_kind {k.id}: la plantilla '{k.template}' no existe en file_templates")
            if k.detect:
                bad = [r for r in k.detect.require + k.detect.any_of + k.detect.exclude if r not in self.column_roles]
                if bad and k.detect.ext == ".csv":
                    problems.append(f"data_kind {k.id}: roles de columna sin definir en column_roles: {', '.join(sorted(set(bad)))}")
        for t in self._tools.values():
            if t.data:
                for alt in t.data.alternatives:
                    unknown = [x for x in alt.needs + alt.gives if x not in self.data_kinds]
                    if unknown:
                        problems.append(f"{t.id}: data.alternatives usa tipos de dato desconocidos: {', '.join(unknown)}")
                if t.data.result_kind and t.data.result_kind not in self.data_kinds:
                    problems.append(f"{t.id}: data.result_kind '{t.data.result_kind}' no existe en data_kinds")
        for r in self.routes:
            if r.conf_via:
                cv_tool = r.conf_via.get("tool", "")
                cv_data = self._tools[cv_tool].data if cv_tool in self._tools else None
                if not cv_data or not r.conf_via.get("file") or not r.conf_via.get("key"):
                    problems.append(f"route {r.id}: conf_via necesita tool (con data.alternatives), file y key")
                elif not any(s.tool == cv_tool for s in r.steps):
                    problems.append(f"route {r.id}: conf_via usa '{cv_tool}', que no es un paso de la ruta")
            for st in r.steps:
                if st.tool not in self._tools:
                    problems.append(f"route {r.id}: herramienta desconocida '{st.tool}'")
                    continue
                td = self._tools[st.tool].data
                if st.via and not (td and any(a.id == st.via for a in td.alternatives)):
                    problems.append(f"route {r.id}: la herramienta '{st.tool}' no tiene la alternativa '{st.via}'")
                if st.unless and st.unless not in self.data_kinds:
                    problems.append(f"route {r.id}: 'unless' usa un tipo de dato desconocido '{st.unless}'")
        for pt in self.param_tables.values():
            if pt.tool not in self._tools:
                problems.append(f"param_table {pt.id}: herramienta desconocida '{pt.tool}'")
            if pt.shape == "topes" and len(pt.columns) < 4:
                problems.append(f"param_table {pt.id}: la forma 'topes' necesita 4-5 columnas (variable, dominio, tope, método, parámetro)")
        # a qué herramientas sirve cada plantilla de datos (para la guía de formatos)
        for t in self._tools.values():
            for ref in t.inputs + t.outputs + t.modifies:
                ft = self.file_templates.get(ref.template or "")
                if ft is not None and t.id not in ft.tools:
                    ft.tools.append(t.id)
        if problems:
            raise CatalogError("Catálogo inconsistente:\n  - " + "\n  - ".join(problems))

    # ------------------------------------------------------------------ acceso
    def __contains__(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def get(self, tool_id: str) -> ToolSpec:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"Herramienta desconocida: '{tool_id}'") from None

    def ids(self) -> list[str]:
        return list(self._tools)

    def flow(self) -> list[ToolSpec]:
        """Herramientas del flujo canónico, en orden."""
        return [t for t in self._tools.values() if t.category == "flujo"]

    # ------------------------------------------------------------------ vista para el LLM
    def llm_catalog(self) -> str:
        """Catálogo compacto (una línea por herramienta) para el prompt del SLM."""
        lines = []
        for t in self._tools.values():
            ps = ", ".join(f"{p.name}{'*' if p.required else ''}" for p in t.params)
            desc = " ".join(t.description.split())
            first = desc.split(". ")[0]
            if len(first) > 150:
                first = first[:147].rstrip() + "..."
            lines.append(f"- {t.id}: {first}" + (f" [parámetros: {ps}]" if ps else ""))
        return "\n".join(lines)

    def llm_workflows(self) -> str:
        if not self.workflows:
            return "(ninguno)"
        return "\n".join(f"- {w.id}: {w.description}" for w in self.workflows.values())

    def function_schemas(self) -> list[dict[str, Any]]:
        """Esquema estilo function-calling (JSON Schema) de cada herramienta, para clientes que lo soporten
        (OpenAI-compatible, Ollama tools) y para documentación. El planner del agente NO ejecuta lo que el
        modelo diga: solo toma ids y parámetros, y los valida contra este mismo catálogo."""
        out = []
        for t in self._tools.values():
            props: dict[str, Any] = {}
            for p in t.params:
                js: dict[str, Any] = {"type": {"path": "string", "integer": "integer", "number": "number",
                                               "boolean": "boolean"}.get(p.type, "string"),
                                      "description": (p.description + (f" Por qué: {p.why}" if p.why else "")).strip()}
                if p.choices:
                    js["enum"] = p.choices
                props[p.name] = js
            out.append({
                "type": "function",
                "function": {
                    "name": t.id,
                    "description": t.description.strip(),
                    "parameters": {"type": "object", "properties": props,
                                   "required": [p.name for p in t.params if p.required],
                                   "additionalProperties": False},
                },
            })
        return out

    def dump_schemas(self) -> str:
        return json.dumps(self.function_schemas(), ensure_ascii=False, indent=2)

    def suggest_ids(self, bad: str, n: int = 3) -> list[str]:
        import difflib
        return difflib.get_close_matches(bad, self.ids(), n=n, cutoff=0.4)
