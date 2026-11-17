"""Coherencia entre pasos (v3.1): alertas ANTES de ejecutar, no cuando el proceso ya va por la mitad.

Dos comprobaciones deterministas (sin modelo de lenguaje):

  1. Columnas. Cada paso que trabaja sobre el modelo de bloques declara en el catálogo (`columns`) qué columnas nombra su conf
     (la ley de cada metal, el dominio, la categoría anterior…) y cuáles agrega. Aquí se sigue el modelo paso a paso:
     el que ya existe en disco (se lee su cabecera) o el que construirá un paso previo del plan. Si un paso nombra una columna
     que su modelo de entrada no tendrá, se avisa («valorizar» usa «au_idw» pero la estimación solo produce «cu_idw»).
  2. Tablas sin cargar. Si en <entrada>/04_Parametros_Entrada hay un CSV de parámetros (metales de valorización, variables del
     modelo, metales del reporte…) cuyo contenido difiere del conf, es casi seguro que lo editaste y no lo cargaste.

Nada se cambia solo: cada alerta trae su sugerencia y, cuando existe, un botón que carga la tabla (con respaldo).
"""

from __future__ import annotations

import difflib
import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import geometria, jpath, pathfix, tablas
from .context import ProjectContext, norm_path
from .datos import read_header
from .models import ColumnAdd, ColumnRead, FixAction, Finding, ParamTable, PlanStep, ToolSpec

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# nombres que pueden aparecer en una expresión sin ser columnas
_EXPR_WORDS = {"math", "if", "else", "and", "or", "not", "in", "is", "abs", "min", "max", "round", "sum", "pow", "float", "int",
               "True", "False", "None", "log", "log10", "exp", "sqrt", "np", "numpy", "pd"}


# ---------------------------------------------------------------------------------------------- utilidades
def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def expand(template: str, data: Any) -> list[str]:
    """«pct_rec_{metales[*].clave}» -> una columna por metal. Si una clave falta o está vacía no se genera nada."""
    keys = _PLACEHOLDER.findall(template)
    if not keys:
        return [template] if template else []
    pools: list[list[str]] = []
    for k in keys:
        vals = [_text(v) for _, v in jpath.get_all(data, k) if isinstance(v, (str, int, float)) and _text(v)]
        if not vals:
            return []
        pools.append(vals)
    out = []
    for combo in itertools.product(*pools):
        name = template
        for k, v in zip(keys, combo):
            name = name.replace("{" + k + "}", v, 1)
        out.append(name)
    return list(dict.fromkeys(out))


def _met(ctx: ProjectContext, tool: ToolSpec, conds) -> bool:
    return all(ctx.cond_met(c, tool) for c in conds)


def _adds(ctx: ProjectContext, tool: ToolSpec, items: list[ColumnAdd], data: Any) -> list[str]:
    out: list[str] = []
    for it in items:
        if _met(ctx, tool, it.when):
            out += expand(it.name, data)
    return list(dict.fromkeys(out))


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", "."))
        return True
    except ValueError:
        return False


def names_in_expression(expr: str) -> list[str]:
    """Nombres de columna que usa una expresión de valorización (admite math.*, if/else y f.<columna>)."""
    s = re.sub(r"\bmath\.[A-Za-z_]\w*", " ", expr)
    s = re.sub(r"\b(?:np|numpy)\.[A-Za-z_]\w*", " ", s)
    cols = [m for m in re.findall(r"\bf\.([A-Za-z_]\w*)", s)]
    s = re.sub(r"\bf\.[A-Za-z_]\w*", " ", s)
    s = re.sub(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", " ", s)                       # los números (1e-3) no son nombres
    cols += [m for m in _NAME.findall(s) if m not in _EXPR_WORDS]
    return list(dict.fromkeys(cols))


def _table_for(ctx: ProjectContext, cfg_name: str, key: str) -> Optional[ParamTable]:
    root = (jpath.parse(key) or [""])[0]
    return next((pt for pt in ctx.registry.param_tables.values() if pt.config == cfg_name and pt.target == root), None)


# ---------------------------------------------------------------------------------------------- 1 · columnas
@dataclass
class Miss:
    """Una columna que un paso nombra en su conf y que su modelo de entrada no tendrá."""
    tool: ToolSpec
    cfg: str
    jp: str
    col: str
    read: ColumnRead
    avail: list[str]
    src: str
    n: int = 1                                    # cuántas claves iguales hay (p. ej. 146 pasadas con el mismo bearing)
    new_model: bool = False                       # el modelo lo crea el propio flujo


def column_findings(ctx: ProjectContext, steps: list[PlanStep], params: Optional[dict[str, Any]] = None) -> list[Finding]:
    params = params or {}
    reg = ctx.registry
    flow: dict[str, tuple[Optional[list[str]], bool]] = {}   # modelo (ruta normalizada) -> (columnas que tendrá | None, ¿lo crea el flujo?)
    reach: dict[str, Optional[Cover]] = {}              # modelo (ruta normalizada) -> dónde llega la grilla (solo si se conoce)
    last: Optional[tuple[str, Path, str]] = None       # (clave, ruta, herramienta) del último modelo que deja un paso del plan
    out: list[Finding] = []
    misses: list[Miss] = []

    for step in steps:
        tool = reg.get(step.tool_id)
        spec = tool.columns
        if spec is None:
            continue
        cfg_name = tool.config
        data = ctx.load_cfg(cfg_name)[0] if cfg_name else None
        if data is None:
            continue
        ins = {rp.ref.name: rp for rp in ctx.resolve_all(tool, params, roles=("input",))}
        outs = {rp.ref.name: rp for rp in ctx.resolve_all(tool, params, roles=("output",))}
        avail: Optional[list[str]] = None
        is_new = False
        src = ""
        creates = _adds(ctx, tool, spec.creates, data)
        if creates:
            avail, src, is_new = creates, "el modelo que crea este paso", True
        elif spec.model_in and spec.model_in in ins:
            rp = ins[spec.model_in]
            if spec.chained and last is not None and rp.key != last[0] and rp.cfg and rp.jpath:
                out.append(_chain_finding(tool, rp, last))
            if rp.key in flow:
                (avail, is_new), src = flow[rp.key], f"el modelo «{rp.path.name}» que dejará un paso anterior del plan"
            elif rp.path.is_file():
                avail, src = read_header(rp.path) or None, f"el modelo «{rp.path.name}»"          # type: ignore[assignment]
        cover: Optional[Cover] = None
        if spec.grid and creates:
            g = geometria.grid_from_schema(jpath.get(data, spec.grid))
            if g is not None:
                cover = Cover(g.box(), g, cfg_name, spec.grid)
        elif spec.model_in in ins:
            cover = reach.get(ins[spec.model_in].key)
        if spec.solid:
            fnd, clipped = _cover_findings(ctx, tool, cfg_name, data, spec.solid, cover,
                                           ins[spec.model_in].path if spec.model_in in ins else None)
            out += fnd
            if any(sc.clip and _met(ctx, tool, sc.when) for sc in spec.solid):
                cover = Cover(clipped) if clipped is not None else None      # el modelo recortado ya no llega más allá del sólido
        if avail is not None:
            have = set(avail)
            added = _adds(ctx, tool, spec.adds, data)
            for r in spec.reads:
                for m in _check_read(ctx, tool, cfg_name, data, r, have, set(added), avail, src):
                    m.new_model = is_new
                    misses.append(m)
            result: Optional[list[str]] = list(dict.fromkeys(list(avail) + added))
        else:
            result = None
        if spec.model_out and spec.model_out in outs:
            flow[outs[spec.model_out].key] = (result, is_new)
            reach[outs[spec.model_out].key] = cover
            last = (outs[spec.model_out].key, outs[spec.model_out].path, tool.id)

    defaults = [m for m in misses if m.new_model and m.read.fallback]
    for m in misses:
        if m not in defaults:
            out.append(_column_finding(ctx, m))
    if defaults:
        out.append(_defaults_finding(ctx, defaults))
    return out


@dataclass
class Cover:
    """Hasta dónde llega un modelo: su caja y, si lo crea el flujo, la grilla y dónde se escribe su esquema (para ofrecer el ajuste)."""
    box: geometria.Box
    grid: Optional[geometria.Grid] = None
    cfg: str = ""
    key: str = ""


def _rng(lo: float, hi: float) -> str:
    return f"{lo:,.0f} a {hi:,.0f}".replace(",", " ")


def _cover_findings(ctx: ProjectContext, tool: ToolSpec, cfg_name: Optional[str], data: Any, solids: list, cover: Optional[Cover],
                    model_path: Optional[Path]) -> tuple[list[Finding], Optional[geometria.Box]]:
    """El modelo debe llegar hasta cada sólido con el que el paso elige bloques; si no, el paso falla (o trabaja con una parte).
    Devuelve también la caja que quedaría tras recortar con el sólido (None si no se cruzan o no se pudo saber)."""
    out: list[Finding] = []
    clipped: Optional[geometria.Box] = None
    for sc in solids:
        if not _met(ctx, tool, sc.when):
            continue
        raw = jpath.get(data, sc.key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        spath = Path(raw.strip())
        if not spath.is_file():
            continue                                              # la ruta mala la reporta la verificación de rutas
        sbox = geometria.obj_bbox(spath)
        if sbox is None:
            continue
        cv = cover
        if cv is None and model_path is not None and model_path.is_file():
            names = tuple((_text(jpath.get(data, f"{sc.fields}.{a}")) if sc.fields else "") or d for a, d in zip("xyz", ("XC", "YC", "ZC")))
            mb = geometria.csv_centroid_box(model_path, names)    # type: ignore[arg-type]
            cv = Cover(mb) if mb is not None else None
        if cv is None:
            continue
        touch, frac = geometria.overlap(cv.box, sbox)
        if sc.clip and all(touch):
            clipped = geometria.Box(tuple(max(cv.box.lo[i], sbox.lo[i]) for i in range(3)),         # type: ignore[arg-type]
                                    tuple(min(cv.box.hi[i], sbox.hi[i]) for i in range(3)))         # type: ignore[arg-type]
        if frac >= 0.95:
            continue
        what = sc.label or "sólido"
        det = []
        for i, a in enumerate("XYZ"):
            if cv.box.lo[i] > sbox.lo[i] + 1e-6 or cv.box.hi[i] < sbox.hi[i] - 1e-6:
                det.append(f"{a}: el modelo va de {_rng(cv.box.lo[i], cv.box.hi[i])} y el sólido de {_rng(sbox.lo[i], sbox.hi[i])}"
                           + ("" if touch[i] else " (no se cruzan)"))
        none = frac == 0.0
        msg = (f"El modelo de bloques no llega hasta el {what} «{spath.name}»"
               + (": no se cruzan, ningún bloque caería dentro y el paso se detendría con error. " if none
                  else f": solo cubre el {frac * 100:.0f} % de su volumen. ")
               + "  ".join(det))
        fix = None
        hint = "Usa otro modelo o otro sólido (¿mismo sistema de coordenadas?)."
        if cv.grid is not None and cv.cfg:
            new = geometria.fit_schema(cv.grid, sbox, sc.clip)
            g2 = geometria.grid_from_schema({**_esq_of(cv), **new})
            more = ""
            if g2 is not None and cv.grid.n_blocks:
                f = g2.n_blocks / cv.grid.n_blocks
                more = (f" Pasa de {cv.grid.n_blocks:,} a {g2.n_blocks:,} bloques padre"
                        + (f" (≈{f:.0f} veces más: crearlo tardará bastante más)." if f >= 3 else ".")).replace(",", " ")
            hint = (f"El modelo lo crea este mismo flujo con el esquema de {cv.cfg}: ajusta origen y extensión para que cubran el "
                    f"{what}.{more} También puedes agrandar el esquema a mano o usar un tamaño de bloque mayor.")
            fix = FixAction(kind="edit_config", detail={"file": cv.cfg, "values": {f"{cv.key}.{k}": v for k, v in new.items()},
                                                         "label": f"Ajustar el esquema del modelo al {what}"})
        out.append(Finding(tool=tool.id, code="MODEL_SOLID_NO_OVERLAP" if none else "MODEL_SOLID_PARTIAL",
                           severity="error" if none else "warn", message=msg, hint=hint, fix=fix))
    return out, clipped


def _esq_of(cv: Cover) -> dict[str, Any]:
    g = cv.grid
    assert g is not None
    return {"origen_x": g.origin[0], "origen_y": g.origin[1], "origen_z": g.origin[2], "x_size": g.size[0], "y_size": g.size[1],
            "z_size": g.size[2], "bearing": g.bearing, "extension_x": 0, "extension_y": 0, "extension_z": 0}


def _fmt_text(text: str, col: str, cdata: Any) -> str:
    """{col} = la columna que falta; {clave.del.conf} = valor actual del conf (p. ej. {densidad} -> 2.65)."""
    def sub(m: re.Match) -> str:
        k = m.group(1)
        if k == "col":
            return col
        v = jpath.get(cdata, k)
        return _text(v) if v is not None else m.group(0)
    return _PLACEHOLDER.sub(sub, text)


def _defaults_finding(ctx: ProjectContext, defaults: list[Miss]) -> Finding:
    """UN solo aviso: el modelo es nuevo, no trae columnas del modelo anterior y se usarían estos valores por defecto."""
    lines: list[str] = []
    edits: list[dict[str, Any]] = []
    seen_ops: set[str] = set()

    def add_op(op: dict[str, Any]) -> None:
        k = json.dumps(op, sort_keys=True, ensure_ascii=False)
        if k not in seen_ops:
            seen_ops.add(k)
            edits.append(op)

    for m in defaults:
        fb = m.read.fallback
        assert fb is not None
        cdata = ctx.load_cfg(m.cfg)[0]
        n = f" ({m.n} filas del conf)" if m.n > 1 else ""
        line = f"- **{m.tool.id}** · {_fmt_text(fb.text, m.col, cdata)}{n}"
        if line not in lines:
            lines.append(line)
        if fb.replace_with is not None:
            add_op({"file": m.cfg, "replace": {"key": m.read.key, "from": m.col, "to": fb.replace_with}})
        target = fb.config or m.cfg
        if fb.set:
            add_op({"file": target, "set": dict(fb.set)})
        if fb.append:
            item = {k: (v.replace("{col}", m.col) if isinstance(v, str) else v) for k, v in fb.append["item"].items()}
            add_op({"file": target, "append": {"key": fb.append["key"], "item": item, "unique": fb.append.get("unique")}})
    return Finding(
        tool="-", code="COLUMN_DEFAULTS", severity="warn",
        message=("El modelo de bloques es NUEVO y no trae columnas que tus conf esperan del modelo anterior. "
                 "Con el botón se usarían estos valores por defecto:\n\n" + "\n".join(lines)),
        hint=("Cambia solo los conf (con respaldo). Los CSV de 04_Parametros_Entrada no se tocan: si luego cargas uno, volverá su valor. "
              "Si prefieres otros valores, edítalos en la pestaña «Parámetros»."),
        fix=FixAction(kind="edit_config", detail={"edits": edits, "label": "Aplicar valores por defecto del modelo nuevo"}))


def _chain_finding(tool: ToolSpec, rp, last: tuple[str, Path, str]) -> Finding:
    """El paso lee un modelo distinto del que deja el paso anterior del plan: lo que este produce no se usaría."""
    return Finding(
        tool=tool.id, code="MODEL_CHAIN_BROKEN", severity="warn" if rp.path.is_file() else "error",
        message=(f"«{tool.id}» lee el modelo …/{rp.path.parent.name}/{rp.path.name} ({rp.cfg}: «{rp.jpath}»), pero el paso anterior del plan, "
                 f"«{last[2]}», deja …/{last[1].parent.name}/{last[1].name}; ese resultado no se usaría."),
        hint=f"Apunta «{rp.jpath}» de {rp.cfg} al modelo que deja «{last[2]}» (botón) o revisa qué pasos quieres encadenar.",
        fix=FixAction(kind="edit_config", detail={"file": rp.cfg, "values": {rp.jpath: str(last[1])}, "old": str(rp.path),
                                                   "label": f"Usar {last[1].name} como modelo de «{tool.id}»"}))


def _check_read(ctx: ProjectContext, tool: ToolSpec, cfg_name: Optional[str], data: Any, r: ColumnRead,
                have: set[str], added: set[str], avail: list[str], src: str) -> list[Miss]:
    if not _met(ctx, tool, r.when):
        return []
    name = r.config or cfg_name
    cdata = data if name == cfg_name else (ctx.load_cfg(name)[0] if name else None)
    if cdata is None:
        return []
    known = have | added if r.uses_added or r.expr else have
    res: list[Miss] = []
    by_col: dict[str, Miss] = {}
    for jp, v in jpath.get_all(cdata, r.key):
        if not isinstance(v, str) or not v.strip():
            continue
        if r.expr:
            wanted = names_in_expression(v)
        else:
            if r.numeric_ok and _is_number(v):
                continue
            wanted = [v.strip()]
        for col in wanted:
            if col in known:
                continue
            if col in by_col:                                     # la misma clave repetida en una lista (p. ej. 25 sólidos): una sola alerta
                by_col[col].n += 1
                continue
            by_col[col] = Miss(tool, name or "", jp, col, r, avail, src)
            res.append(by_col[col])
    return res


def _column_finding(ctx: ProjectContext, m: Miss) -> Finding:
    tool, cfg_name, jp, col, r, avail, src = m.tool, m.cfg, m.jp, m.col, m.read, m.avail, m.src
    derived = re.compile(r"_(nsamp|nholes|pass|distcart|distanis|nn|avgcart|avganis|anterior)$")
    near = difflib.get_close_matches(col, [c for c in avail if not derived.search(c)], n=2, cutoff=0.6)
    sugg = ""
    if near:
        sugg = f" ¿Quisiste decir {' o '.join('«' + n + '»' for n in near)}?"
    else:                                                          # sin parecido: se muestran las columnas «de ley» que sí hay
        leyes = [c for c in avail if re.search(r"(idw|_ok|_nn|ley|cu|au|ag|pb|zn)", c, re.I) and not derived.search(c)]
        if leyes:
            sugg = " Columnas de ley que sí hay: " + ", ".join(f"«{c}»" for c in leyes[:6]) + "."
    tbl = _table_for(ctx, cfg_name, jp)
    where = (f"Corrígelo en la tabla «{tbl.title}» ({tbl.file}) de la pestaña «Parámetros»" if tbl
             else f"Corrígelo en {cfg_name}") + f" (clave «{jp}»)."
    what = f" ({r.label})" if r.label else ""
    more = f" (y otras {m.n - 1} iguales)" if m.n > 1 else ""
    return Finding(
        tool=tool.id, code="COLUMN_MISSING", severity="warn",
        message=(f"{cfg_name}: «{jp}»{more} usa la columna «{col}»{what}, pero {src} no la tendrá cuando corra «{tool.id}»; "
                 "el paso se detendría con error."),
        hint=where + sugg)


# ---------------------------------------------------------------------------------------------- 2 · tablas sin cargar
def _same(a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    if _is_number(a) and _is_number(b):
        return abs(float(a.replace(",", ".")) - float(b.replace(",", "."))) < 1e-9
    if pathfix.is_abs_path_str(a) and pathfix.is_abs_path_str(b):
        return norm_path(a) == norm_path(b)
    return False


def diff_table(ctx: ProjectContext, pt: ParamTable, csv_path: Path) -> tuple[Optional[tablas.TableLoad], str]:
    """(lectura del CSV, resumen de lo que difiere del conf; '' si es igual o no se puede comparar)."""
    load = tablas.load_table_file(pt, csv_path)
    if not load.ok:
        return load, ""
    conf_rows = tablas.current_rows(ctx, pt)
    if conf_rows is None:
        return load, "el conf todavía no tiene esta tabla"
    value, _ = pathfix.relocate_value(ctx, load.value)
    csv_rows = tablas.rows_from_value(pt, value)
    heads = [c.header for c in pt.columns]
    if len(csv_rows) != len(conf_rows):
        return load, f"el CSV tiene {len(csv_rows)} fila(s) y el conf {len(conf_rows)}"
    diffs = [(i, j) for i, (ra, rb) in enumerate(zip(csv_rows, conf_rows)) for j in range(len(heads))
             if not _same(ra[j] if j < len(ra) else "", rb[j] if j < len(rb) else "")]
    if not diffs:
        return load, ""
    n_rows = len({i for i, _ in diffs})
    ex = "; ".join(f"fila {i + 1}, «{heads[j]}»: CSV «{csv_rows[i][j]}» vs conf «{conf_rows[i][j]}»" for i, j in diffs[:3])
    return load, f"{n_rows} fila(s) distintas ({ex}{'; …' if len(diffs) > 3 else ''})"


DEFAULTS_MARK = "valores_por_defecto.json"


def _mark_path(ctx: ProjectContext) -> Path:
    return ctx.config_dir / "_respaldo_agente" / DEFAULTS_MARK


def _marked(ctx: ProjectContext) -> dict[str, list[str]]:
    try:
        return json.loads(_mark_path(ctx).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_defaults(ctx: ProjectContext, edits: list[dict[str, Any]]) -> None:
    """Anota qué tablas de qué conf se cambiaron con los valores por defecto del modelo nuevo (replace/append): su CSV diferirá a propósito."""
    mark = _marked(ctx)
    for e in edits:
        key = (e.get("replace") or e.get("append") or {}).get("key")
        if key:
            root = (jpath.parse(key) or [""])[0]
            if root not in mark.setdefault(e["file"], []):
                mark[e["file"]].append(str(root))
    if mark:
        try:
            _mark_path(ctx).parent.mkdir(parents=True, exist_ok=True)
            _mark_path(ctx).write_text(json.dumps(mark, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def table_findings(ctx: ProjectContext, step_ids: list[str]) -> list[Finding]:
    data_dir = ctx.anchor_dir("01_Data")
    out: list[Finding] = []
    marked = _marked(ctx)
    for pt in ctx.registry.param_tables.values():
        csv_path = tablas.find_csv(data_dir, pt)
        if csv_path is None:
            continue
        load, summary = diff_table(ctx, pt, csv_path)
        if not summary or load is None:
            continue
        in_plan = pt.tool in step_ids
        on_purpose = pt.target in marked.get(pt.config, [])
        out.append(Finding(
            tool=pt.tool, code="TABLE_NOT_LOADED", severity="warn" if in_plan and not on_purpose else "info",
            message=(f"«{pt.file}» ({pt.title}) de tu carpeta de entrada no coincide con {pt.config}: {summary}."
                     + ("" if in_plan else " (este paso no está en el plan)")
                     + (" Es esperado: aplicaste los valores por defecto del modelo nuevo; si cargas el CSV se pierden." if on_purpose else "")),
            hint="Si el CSV es el correcto, cárgalo al conf (botón o pestaña «Parámetros»); si el correcto es el conf, actualiza el CSV.",
            fix=FixAction(kind="load_table", detail={"table": pt.id, "path": str(csv_path),
                                                       "label": f"Cargar «{pt.file}» a {pt.config}"})))
    return out


# ---------------------------------------------------------------------------------------------- 3 · conf que exige el flujo elegido
WORKFLOW_DEFAULTS = {"origen": "sondajes", "crear_modelo_bloques": True, "valorizar_modelo": True}      # valor si la clave no está


def workflow_findings(ctx: ProjectContext, workflow: Optional[str]) -> list[Finding]:
    """Un flujo (p. ej. «estimar desde sondajes con modelo recortado») necesita ciertos valores en los conf; si no están, el plan
    omitiría pasos en silencio. Se avisa como ERROR con botón para ajustarlos (con respaldo)."""
    reg = ctx.registry
    if not workflow or workflow not in reg.workflows:
        return []
    w = reg.workflows[workflow]
    out: list[Finding] = []
    for fname, wanted in w.conf.items():
        data = ctx.load_cfg(fname)[0]
        if data is None:
            continue
        diffs = {k: v for k, v in wanted.items() if jpath.get(data, k, WORKFLOW_DEFAULTS.get(k.split(".")[-1])) != v}
        if not diffs:
            continue
        desc = ", ".join(f"«{k}» = {json.dumps(jpath.get(data, k, WORKFLOW_DEFAULTS.get(k.split('.')[-1])), ensure_ascii=False)} "
                         f"(el flujo pide {json.dumps(v, ensure_ascii=False)})" for k, v in diffs.items())
        out.append(Finding(
            tool="-", code="WORKFLOW_CONF", severity="error",
            message=f"El flujo «{w.id}» necesita otro valor en {fname}: {desc}. Con el actual se omitirían pasos del flujo.",
            hint="Ajústalo con el botón (hace respaldo del conf) o edita el valor a mano.",
            fix=FixAction(kind="edit_config", detail={"file": fname, "values": diffs, "label": f"Ajustar {fname} para este flujo"})))
    return out


def check(ctx: ProjectContext, steps: list[PlanStep], params: Optional[dict[str, Any]] = None) -> list[Finding]:
    """Todas las alertas de coherencia para estos pasos (los ya terminados no se revisan)."""
    ids = [s.tool_id for s in steps]
    return table_findings(ctx, ids) + column_findings(ctx, steps, params)
