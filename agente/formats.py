"""Guía de formatos: «¿qué archivo necesito y cómo se llena?».

Para cada herramienta reúne TODO lo que el usuario debe aportar y lo entrega listo para descargar:
  · data   — CSV de datos (sondajes, compósitos, modelo de bloques, plan…): cabecera + filas de ejemplo.
  · table  — CSV de parámetros (sólidos, topes, pasadas…): se cargan al conf desde la pestaña «Parámetros».
  · config — conf_*.json: plantilla con valores por defecto y bloque «_ayuda», o la estructura de TU conf actual.
Todo sale del catálogo YAML y de los archivos reales de ejemplo de la carpeta de entrada; no hay texto escrito a mano.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .catalog import ToolRegistry
from .context import ProjectContext
from .tablas import explain_table, render_table_template
from .templates import TemplateError, render_config_template, render_data_template


@dataclass
class FormatItem:
    id: str
    kind: str                                   # data | table | config
    title: str
    filename: str                               # nombre sugerido de la descarga
    mime: str
    content: str                                # plantilla lista para guardar
    description: str = ""
    columns: list[tuple[str, str, str]] = field(default_factory=list)      # (columna, ejemplo, descripción)
    notes: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    dest: str = ""                              # dónde debe quedar el archivo para que el agente lo use
    real_example: Optional[str] = None          # primeras líneas de TU archivo de ejemplo (si existe en la carpeta de entrada)
    help_text: str = ""                         # explicación en texto plano (LEEME)
    table_id: str = ""                          # si kind == 'table': id de la ParamTable

    @property
    def help_filename(self) -> str:
        return Path(self.filename).stem + ".LEEME.txt"


def head_of(path: Path, n: int = 4, width: int = 220) -> Optional[str]:
    """Primeras n líneas de un archivo (lee unos pocos KB: sirve aunque pese gigas)."""
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
    except OSError:
        return None
    text = raw.decode("utf-8-sig", errors="replace")
    lines = [ln[:width] + ("…" if len(ln) > width else "") for ln in text.splitlines()[:n]]
    return "\n".join(lines) if lines else None


def _example_path(ctx: ProjectContext, rel: str) -> Optional[Path]:
    if not rel:
        return None
    base = ctx.cfg.data_dir if ctx.cfg.has_workspace else ctx.root / "01_Data"
    p = (base / rel) if base else None
    return p if p and p.is_file() else None


def build_guide(ctx: ProjectContext, reg: ToolRegistry, tool_ids: Optional[list[str]] = None) -> list[FormatItem]:
    """Elementos de formato de las herramientas pedidas (todas si no se indica), en el orden del flujo."""
    tools = [t for t in reg if not tool_ids or t.id in tool_ids]
    items: dict[str, FormatItem] = {}

    def add(item: FormatItem) -> None:
        if item.id in items:
            for t in item.tools:
                if t not in items[item.id].tools:
                    items[item.id].tools.append(t)
        else:
            items[item.id] = item

    base_in = (ctx.cfg.data_dir if ctx.cfg.has_workspace else ctx.root / "01_Data")
    for t in tools:
        # ---------------- archivos de datos
        for ref in t.inputs:
            ft = reg.file_templates.get(ref.template or "")
            if ft is None:
                continue
            text, expl = render_data_template(ft)
            real = _example_path(ctx, ft.example_file)
            add(FormatItem(
                id=f"data:{ft.id}", kind="data", title=ft.title or ft.id, filename=f"{ft.id}.csv", mime="text/csv",
                content="﻿" + text.replace("\n", "\r\n"), description=ft.description.strip(),
                columns=[(c.name, str(c.example), c.description) for c in ft.columns], notes=list(ft.notes), tools=[t.id],
                dest=str(base_in / ft.folder) if ft.folder and base_in else "",
                real_example=head_of(real) if real else None, help_text=expl))
        # ---------------- tablas de parámetros
        for pt in reg.param_tables.values():
            if pt.tool != t.id:
                continue
            add(FormatItem(
                id=f"table:{pt.id}", kind="table", title=pt.title, filename=pt.file, mime="text/csv",
                content=render_table_template(pt), description=pt.description.strip(),
                columns=[(c.header, str(c.example), c.description) for c in pt.columns], notes=list(pt.notes), tools=[t.id],
                dest=str(base_in / pt.folder) if base_in else "",
                real_example=head_of(base_in / pt.folder / pt.file) if base_in else None,
                help_text=explain_table(pt), table_id=pt.id))
        # ---------------- conf_*.json
        if t.config:
            add(_config_item(ctx, t))
    return list(items.values())


def _config_item(ctx: ProjectContext, t) -> FormatItem:
    name = t.config
    dest = str(ctx.cfg_path(name))
    cur = ctx.cfg_path(name)
    if t.config_template and t.config_template.fields:
        try:
            text, _ = render_config_template(t, ctx.root, ctx.expand_root)
            cols = [(f.name, str(f.default if f.default is not None else ""), f.description) for f in t.config_template.fields]
            return FormatItem(
                id=f"config:{name}", kind="config", title=f"Configuración de «{t.id}» ({name})", filename=name.replace(".json", ".plantilla.json"),
                mime="application/json", content=text,
                description=(f"Valores por defecto de {name}. El bloque «_ayuda» explica cada campo; "
                             "se elimina al copiarla a Configuraciones."),
                columns=cols, tools=[t.id], dest=dest, help_text="Plantilla de configuración generada desde el catálogo.\n")
        except TemplateError:
            pass
    # sin campos declarados: la mejor guía es la estructura de tu propio conf
    body, note = "", "No existe todavía: cópialo de otro proyecto."
    if cur.is_file():
        try:
            body = cur.read_text(encoding="utf-8-sig")
            note = ("Estructura de TU configuración actual. Las tablas largas (sólidos, topes, pasadas…) se llenan en CSV "
                    "desde la pestaña «Parámetros»; aquí cambias los valores simples.")
            json.loads(body)
        except (OSError, ValueError):
            note = "El archivo existe pero no es un JSON válido."
    return FormatItem(
        id=f"config:{name}", kind="config", title=f"Configuración de «{t.id}» ({name})", filename=name, mime="application/json",
        content=body, description=note, tools=[t.id], dest=dest, help_text=note + "\n")


def render_guide_markdown(items: list[FormatItem]) -> str:
    """Resumen en texto (para el CLI y el informe)."""
    if not items:
        return "No encontré formatos para esa herramienta."
    L = ["# Formatos de los archivos que necesitas", ""]
    for kind, label in (("data", "Archivos de datos (CSV)"), ("table", "Tablas de parámetros (CSV)"), ("config", "Configuraciones (JSON)")):
        group = [i for i in items if i.kind == kind]
        if not group:
            continue
        L += [f"## {label}", ""]
        for i in group:
            L.append(f"### {i.title} — `{i.filename}`  _(usado por: {', '.join(i.tools)})_")
            L.append(i.description)
            if i.columns:
                L += ["", "| Columna | Ejemplo | Qué es |", "|---|---|---|"]
                L += [f"| `{c}` | {e} | {d} |" for c, e, d in i.columns]
            for n in i.notes:
                L.append(f"> {n}")
            if i.dest:
                L.append(f"\nDónde guardarlo: `{i.dest}`")
            L.append("")
    return "\n".join(L)


_STOP = {"que", "para", "como", "los", "las", "del", "una", "uno", "con", "sin", "por", "formato", "formatos", "plantilla",
         "plantillas", "archivo", "archivos", "dame", "necesito", "quiero", "debo", "tengo", "llenar", "rellenar", "sobre",
         "cual", "cuales", "esta", "este", "ejemplo", "ejemplos", "mis", "sus", "tiene", "tienen", "saber", "hay"}


def rank_items(items: list[FormatItem], text: str) -> list[FormatItem]:
    """Ordena los formatos por coincidencia con la frase del usuario (títulos, nombres de archivo y de columna).
    Si ninguno coincide devuelve todos: mejor mostrar de más que dejarlo sin respuesta."""
    from .textutil import normalize
    import re
    words = {w for w in re.findall(r"[a-z0-9_]{4,}", normalize(text)) if w not in _STOP}
    stems = {w[:-1] if w.endswith("s") else w for w in words}
    if not stems:
        return items
    scored = []
    for it in items:
        hay = normalize(" ".join([it.title, it.filename, it.id, it.description, *[c for c, _, _ in it.columns]]))
        score = sum(1 for w in stems if w in hay)
        if score:
            scored.append((score, it))
    if not scored:
        return items
    scored.sort(key=lambda x: -x[0])
    top = scored[0][0]
    return [it for sc, it in scored if sc >= max(1, top - 1)]
