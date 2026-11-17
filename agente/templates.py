"""Template Generator: crea archivos de ejemplo cuando falta un conf o un archivo de datos.

Las plantillas se escriben SIEMPRE en plantillas_generadas/ (carpeta del agente), nunca dentro del proyecto,
y nunca sobrescriben nada. Para usarlas hay que 'promoverlas' (copiar al destino) -> acción explícita.

  - conf JSON  : se genera desde config_template.fields del catálogo, con un bloque "_ayuda" que explica cada
                 campo (JSON no admite comentarios). Al promover, "_ayuda" se elimina.
  - conf YAML  : igual pero con comentarios '#'.
  - datos CSV  : cabecera + filas de ejemplo; la explicación va en un .txt hermano.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .context import ProjectContext
from .models import FileTemplate, GeneratedFile, ToolSpec
from .schemas import nest_defaults

AYUDA_KEY = "_ayuda"


class TemplateError(Exception):
    pass


def render_config_template(tool: ToolSpec, root: Path, expand=None) -> tuple[str, str]:
    """(texto, extensión) de la plantilla de conf de la herramienta."""
    t = tool.config_template
    if not t or not t.fields:
        raise TemplateError(f"'{tool.id}' no declara config_template.fields: no hay plantilla automática.")
    values, helps = nest_defaults(t.fields, root, expand)
    if t.module:
        values = {"__modulo__": t.module, "__version__": "1.0", **values}
    if t.format == "yaml":
        lines = ["# PLANTILLA generada por el agente. Revisa cada valor y copia el archivo a su destino."]
        for k, v in values.items():
            lines += _yaml_block(k, v, helps, prefix="")
        return "\n".join(lines) + "\n", ".yaml"
    doc = {AYUDA_KEY: {"nota": "Plantilla generada por el agente: revisa los valores. Esta sección se elimina al promover.",
                       **helps}, **values}
    return json.dumps(doc, ensure_ascii=False, indent=4) + "\n", ".json"


def _yaml_block(key: str, value: Any, helps: dict[str, str], prefix: str) -> list[str]:
    full = f"{prefix}{key}"
    out = []
    if isinstance(value, dict) and value:
        if full in helps:
            out.append(f"# {helps[full]}")
        out.append(f"{key}:")
        sub = []
        for k, v in value.items():
            sub += _yaml_block(k, v, helps, prefix=f"{full}.")
        out += ["  " + s for s in sub]
    else:
        if full in helps and helps[full]:
            out.append(f"# {helps[full]}")
        out.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")      # los escalares JSON son YAML válido
    return out


def render_data_template(ft: FileTemplate) -> tuple[str, str]:
    """(texto CSV, texto explicativo)."""
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c.name for c in ft.columns])
    for i in range(max(ft.n_rows, 1)):
        w.writerow([_example(c.example, i) for c in ft.columns])
    expl = [f"PLANTILLA DE DATOS: {ft.id}", ft.description, "", "Columnas:"]
    expl += [f"  - {c.name}: {c.description}" for c in ft.columns]
    if ft.notes:
        expl += ["", "Notas:"] + [f"  - {n}" for n in ft.notes]
    expl += ["", "Las filas de ejemplo son ficticias: reemplázalas por tus datos antes de usar el archivo."]
    return buf.getvalue(), "\n".join(expl) + "\n"


def _example(ex: Any, i: int) -> Any:
    if isinstance(ex, (int, float)) and not isinstance(ex, bool):
        return ex + i
    if isinstance(ex, str):
        m = re.match(r"^(.*?)(\d+)$", ex)                 # V001 -> V002, S_1 -> S_2, DDH_1 -> DDH_2
        if m:
            head, num = m.groups()
            return f"{head}{int(num) + i:0{len(num)}d}"
    return ex


class TemplateGenerator:
    def __init__(self, ctx: ProjectContext):
        self.ctx = ctx
        self.out_dir = ctx.cfg.templates_dir

    def for_config(self, tool: ToolSpec) -> GeneratedFile:
        assert tool.config
        text, ext = render_config_template(tool, self.ctx.root, self.ctx.expand_root)
        name = Path(tool.config).stem + ".plantilla" + ext
        path = self._write(name, text)
        return GeneratedFile(path=str(path), target=str(self.ctx.cfg_path(tool.config)), tool=tool.id, kind="config")

    def for_data(self, tool: ToolSpec, template_id: str, target: Path) -> GeneratedFile:
        ft = self.ctx.registry.file_templates.get(template_id)
        if ft is None:
            raise TemplateError(f"No existe la plantilla de datos '{template_id}'.")
        csv_text, expl = render_data_template(ft)
        stem = Path(target).stem
        path = self._write(f"{stem}.plantilla.csv", csv_text)
        self._write(f"{stem}.plantilla.LEEME.txt", expl)
        return GeneratedFile(path=str(path), target=str(target), tool=tool.id, kind="data")

    def _write(self, name: str, text: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        p = self.out_dir / name
        with open(p, "w", encoding="utf-8", newline="") as f:          # se regenera: es un archivo del agente
            f.write(text)
        return p

    # ------------------------------------------------------------------ promover
    def promote(self, gen: GeneratedFile) -> Path:
        """Copia la plantilla a su destino real. Nunca sobrescribe un archivo existente."""
        target = Path(gen.target)
        if target.exists():
            raise TemplateError(f"{target} ya existe; no se sobrescribe. Edita el archivo existente.")
        src = Path(gen.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".json":
            data = json.loads(src.read_text(encoding="utf-8"))
            data.pop(AYUDA_KEY, None)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
        else:
            target.write_bytes(src.read_bytes())
        gen.promoted = True
        return target
