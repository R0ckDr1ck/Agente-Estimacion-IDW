"""Tablas de parámetros: CSV ⇄ clave de un conf_*.json.

La interfaz de escritorio original editaba estas tablas en pantalla y las guardaba en el conf. Aquí el flujo es:
  1. la interfaz web entrega una PLANTILLA CSV (mismas columnas que los ejemplos de 01_Data/04_Parametros_Entrada);
  2. la llenas en Excel;
  3. la subes (o la dejas en la carpeta de entrada) y «Aplicar» la carga al conf, con respaldo y verificación.

El mapeo vive en el catálogo (`param_tables`), no en el código: añadir una tabla nueva es escribir YAML.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import pathfix
from .context import ProjectContext
from .models import ParamTable, TableColumn
from .textutil import normalize


class TableError(Exception):
    pass


@dataclass
class TableLoad:
    """Resultado de leer un CSV contra una ParamTable (todavía sin escribir nada)."""
    table: ParamTable
    value: Any = None                                    # lo que quedaría en conf[target]
    n_rows: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.n_rows > 0


# ------------------------------------------------------------------------------------------------- plantilla
def render_table_template(pt: ParamTable, with_examples: bool = True) -> str:
    """CSV UTF-8 con BOM (se abre bien en Excel): cabecera + filas de ejemplo."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c.header for c in pt.columns])
    if with_examples:
        rows = pt.example_rows or [[c.example for c in pt.columns]]
        for r in rows:
            w.writerow(r)
    return "﻿" + buf.getvalue()


def explain_table(pt: ParamTable) -> str:
    L = [f"TABLA DE PARÁMETROS: {pt.title}", pt.description.strip(), "",
         f"Archivo: {pt.file}   (carpeta sugerida: <entrada>/{pt.folder})",
         f"Se carga a: {pt.config} → «{pt.target}»", "", "Columnas:"]
    L += [f"  - {c.header}: {c.description}" + (f"  [ejemplo: {c.example}]" if c.example != "" else "") for c in pt.columns]
    if pt.notes:
        L += ["", "Notas:"] + [f"  - {n}" for n in pt.notes]
    L += ["", "Las filas de ejemplo son orientativas: reemplázalas por tus valores."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------- lectura
def decode_csv(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_csv(text: str) -> tuple[list[str], list[list[str]]]:
    text = text.lstrip("﻿")                                  # BOM de Excel: no debe pegarse al nombre de la primera columna
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") > first.count(",") else ("\t" if first.count("\t") > first.count(",") else ",")
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise TableError("El CSV está vacío.")
    return [h.strip() for h in rows[0]], [[c.strip() for c in r] for r in rows[1:]]


def _num(txt: str) -> float:
    return float(str(txt).strip().replace(",", "."))


def load_table(pt: ParamTable, header: list[str], rows: list[list[str]]) -> TableLoad:
    """Casa las columnas por su título (sin tildes ni mayúsculas) y convierte las filas al formato del conf."""
    res = TableLoad(pt)
    idx: dict[str, int] = {}
    wanted = {normalize(c.header): c for c in pt.columns}
    for k, h in enumerate(header):
        n = normalize(h)
        if n in wanted and n not in idx:
            idx[n] = k
        else:
            res.warnings.append(f"Columna «{h}» no se reconoce y se ignora.")
    missing = [c.header for c in pt.columns if normalize(c.header) not in idx]
    if len(missing) == len(pt.columns):
        res.errors.append("Ninguna columna coincide con las de la tabla. Se esperan: " + ", ".join(c.header for c in pt.columns) + ".")
        return res
    if missing:
        res.warnings.append("El CSV no trae las columnas: " + ", ".join(missing) + " (quedan con su valor por defecto o vacías).")

    def cell(row: list[str], c: TableColumn) -> str:
        k = idx.get(normalize(c.header))
        return row[k].strip() if k is not None and k < len(row) else ""

    req = [normalize(x) for x in pt.required_cols]
    records: list[dict[str, Any]] = []
    for n, row in enumerate(rows, start=2):                        # n = número de línea del CSV
        raw = {c.header: cell(row, c) for c in pt.columns}
        if not any(raw.values()):
            continue
        if any(not raw[c.header] for c in pt.columns if normalize(c.header) in req):
            res.warnings.append(f"Línea {n}: falta {', '.join(c.header for c in pt.columns if normalize(c.header) in req and not raw[c.header])}; se omite.")
            continue
        rec: dict[str, Any] = {}
        for c in pt.columns:
            txt = raw[c.header] if raw[c.header] != "" else ("" if c.default is None else str(c.default))
            key = c.key or c.header
            try:
                if c.type == "integer":
                    rec[key] = int(_num(txt or "1"))
                elif c.type == "number":
                    rec[key] = _num(txt) if txt != "" else None
                else:
                    rec[key] = txt
            except ValueError:
                res.errors.append(f"Línea {n}: «{c.header}» debe ser un número (se leyó «{txt}»).")
                rec[key] = None
        records.append(rec)

    if pt.shape == "topes":
        topes: dict[str, dict[str, dict[str, Any]]] = {}
        k_var, k_dom, k_top, k_met, k_par = [c.key or c.header for c in pt.columns[:5]]
        for r in records:
            if r[k_top] is None:
                res.errors.append(f"Tope vacío en {r[k_var]}/{r[k_dom]}.")
                continue
            info: dict[str, Any] = {"tope": r[k_top], "metodo": r[k_met] or "manual"}
            if r[k_par] is not None:
                info["parametro"] = r[k_par]
            topes.setdefault(str(r[k_var]), {})[str(r[k_dom])] = info
        res.value, res.n_rows = topes, sum(len(v) for v in topes.values())
    else:
        res.value, res.n_rows = records, len(records)
    if res.n_rows == 0 and not res.errors:
        res.errors.append("El CSV no trae filas con datos.")
    return res


def load_table_file(pt: ParamTable, path: Path) -> TableLoad:
    try:
        header, rows = parse_csv(decode_csv(Path(path).read_bytes()))
    except (OSError, TableError) as e:
        r = TableLoad(pt)
        r.errors.append(str(e))
        return r
    return load_table(pt, header, rows)


# ------------------------------------------------------------------------------------------------- escritura
def apply_table(ctx: ProjectContext, load: TableLoad, backup_root: Path | None = None) -> list[str]:
    """Escribe `load.value` en conf[target]. Respaldo previo; verifica que el JSON resultante sea válido."""
    pt = load.table
    if not load.ok:
        raise TableError("; ".join(load.errors) or "La tabla no tiene filas.")
    path = ctx.cfg_path(pt.config)
    if not path.is_file():
        raise TableError(f"No existe {path}. Genera primero el conf (pestaña «Formatos y plantillas»).")
    original = path.read_bytes()
    has_bom = original.startswith(b"\xef\xbb\xbf")
    try:
        data = json.loads(original.decode("utf-8-sig"))
    except ValueError as e:
        raise TableError(f"{pt.config} no es un JSON válido ({e}); corrígelo antes de cargar la tabla.") from e
    if not isinstance(data, dict):
        raise TableError(f"{pt.config}: la raíz del JSON no es un objeto.")
    value, n_moved = pathfix.relocate_value(ctx, load.value)       # las rutas del CSV (p. ej. sólidos) siguen a tus carpetas actuales
    data[pt.target] = value                                       # reemplaza en su sitio (conserva el orden de las claves)
    text = json.dumps(data, ensure_ascii=False, indent=4) + "\n"
    json.loads(text)                                              # verificación
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = (backup_root or (ctx.config_dir / "_respaldo_agente")) / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / pt.config)
    payload = text.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" + payload) if has_bom else payload)
    ctx.invalidate()
    out = [f"{pt.config}: «{pt.target}» actualizado con {load.n_rows} fila(s) desde {pt.file}."]
    if n_moved:
        out.append(f"{n_moved} ruta(s) del CSV apuntaban a otra carpeta y se reubicaron a tus carpetas de entrada/salida actuales.")
    return out + [f"Respaldo del conf anterior: {backup_dir / pt.config}"]


# ------------------------------------------------------------------------------------------------- estado / exportar
def current_rows(ctx: ProjectContext, pt: ParamTable) -> list[list[str]] | None:
    """Filas que hoy tiene el conf para esta tabla (None si no existe el conf o la clave)."""
    data, _ = ctx.load_cfg(pt.config)
    if not data or pt.target not in data:
        return None
    return rows_from_value(pt, data[pt.target])


def rows_from_value(pt: ParamTable, val: Any) -> list[list[str]]:
    """Valor de conf[target] -> filas de texto en el orden de columnas de la tabla (la inversa de `load_table`)."""
    out: list[list[str]] = []
    if pt.shape == "topes" and isinstance(val, dict):
        for var, doms in val.items():
            for dom, info in doms.items():
                out.append([var, dom, _fmt(info.get("tope")), info.get("metodo", ""),
                            _fmt(info["parametro"]) if info.get("parametro") is not None else ""])
    elif isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                out.append([_fmt(item.get(c.key or c.header)) for c in pt.columns])
    return out


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v)) if abs(v) < 1e15 else str(v)
    return str(v)


def export_current(ctx: ProjectContext, pt: ParamTable) -> str | None:
    rows = current_rows(ctx, pt)
    if rows is None:
        return None
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([c.header for c in pt.columns])
    w.writerows(rows)
    return "﻿" + buf.getvalue()


def find_csv(data_dir: Path | None, pt: ParamTable) -> Path | None:
    """CSV de esta tabla dentro de la carpeta de entrada (<entrada>/<folder>/<file>), si existe."""
    if data_dir is None:
        return None
    for cand in (data_dir / pt.folder / pt.file, data_dir / pt.file):
        if cand.is_file():
            return cand
    return None


# ------------------------------------------------------------------------------------------------- editor de valores simples
def editable_view(ctx: ProjectContext, name: str, hidden: set[str]) -> dict | None:
    """El conf SIN las claves que se editan como tabla CSV (listas largas de sólidos, pasadas, topes…)."""
    data, _ = ctx.load_cfg(name)
    if data is None:
        return None
    return {k: v for k, v in data.items() if k not in hidden}


def save_conf_edit(ctx: ProjectContext, name: str, text: str, hidden: set[str],
                   backup_root: Path | None = None) -> list[str]:
    """Guarda el JSON editado a mano y reinserta las claves ocultas (tablas). Valida antes de escribir y respalda."""
    path = ctx.cfg_path(name)
    try:
        edited = json.loads(text)
    except ValueError as e:
        raise TableError(f"El JSON no es válido: {e}") from e
    if not isinstance(edited, dict):
        raise TableError("La raíz del JSON debe ser un objeto { … }.")
    original_bytes = path.read_bytes()
    has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
    original = json.loads(original_bytes.decode("utf-8-sig"))
    if "__modulo__" in original and edited.get("__modulo__") != original["__modulo__"]:
        raise TableError(f"«__modulo__» debe seguir siendo {original['__modulo__']!r}: identifica a qué herramienta pertenece el conf.")
    merged: dict = {}
    for k, v in original.items():
        if k in hidden:
            merged[k] = v                                       # las tablas se cambian solo desde su CSV
        elif k in edited:
            merged[k] = edited[k]
    for k, v in edited.items():
        if k not in merged and k not in hidden:
            merged[k] = v
    out = json.dumps(merged, ensure_ascii=False, indent=4) + "\n"
    json.loads(out)
    if merged == original:
        return [f"{name}: sin cambios."]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = (backup_root or (ctx.config_dir / "_respaldo_agente")) / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_dir / name)
    payload = out.encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" + payload) if has_bom else payload)
    ctx.invalidate()
    changed = sorted(k for k in merged if merged.get(k) != original.get(k))
    return [f"{name}: guardado (claves modificadas: {', '.join(changed)}).", f"Respaldo del conf anterior: {backup_dir / name}"]
