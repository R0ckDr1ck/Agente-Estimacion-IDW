"""Viabilidad con TODOS los datos (v3).

Problema de la v2: «¿mis datos sirven para estimar?» solo revisaba lo que el conf actual apuntaba (p. ej. si la importación
estaba en «compósitos», ni miraba collar, survey y assay). Aquí el agente:

  1. recorre la carpeta de ENTRADA y reconoce cada archivo por sus cabeceras (no por su nombre ni por lo que diga el conf);
  2. mira la base de datos del flujo (Database.db, solo lectura) y los resultados de pasos anteriores;
  3. con el catálogo (qué datos necesita y qué deja cada herramienta) calcula, SIN ejecutar nada:
       · qué rutas completas son posibles (p. ej. estimar desde compósitos / desde sondajes / evaluar un plan),
       · qué procesos puedes ejecutar hoy, cuáles quedan posibles tras otros pasos y cuáles no (y qué les falta);
  4. compara con la configuración actual y avisa si apunta a una ruta distinta de la que tus datos permiten.

Todo es determinista (sin SLM): el modelo solo decide que el usuario pregunta por viabilidad.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .catalog import ToolRegistry
from . import jpath, jsonedit
from .context import ProjectContext
from .models import DataKind, FixAction, Finding, Route
from .textutil import normalize

MAX_FILES = 4000          # tope de archivos que se miran en la carpeta de entrada
MAX_DEPTH = 6


# ---------------------------------------------------------------------------------------------- resultados
@dataclass
class Found:
    kind: str
    title: str
    files: list[str] = field(default_factory=list)
    detail: str = ""
    source: str = "entrada"                       # entrada | base de datos | resultados


@dataclass
class ToolStatus:
    tool_id: str
    title: str
    state: str                                    # listo | encadenado | falta
    via: str = ""                                 # alternativa que se usaría
    note: str = ""
    chain: list[str] = field(default_factory=list)       # pasos que deben correr antes
    missing: list[str] = field(default_factory=list)     # ids de tipos de dato que faltan (alternativa más cercana)
    other_ways: list[list[str]] = field(default_factory=list)   # lo que faltaría con las demás alternativas
    conf_skip: list[str] = field(default_factory=list)   # motivos por los que la configuración actual lo omite


@dataclass
class StepStatus:
    tool_id: str
    title: str
    state: str                                    # ok | omitido | falta | depende
    via: str = ""
    missing: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class RouteStatus:
    route: Route
    feasible: bool
    steps: list[StepStatus]
    missing_base: list[str]                       # lo que TÚ tendrías que aportar (tipos de dato con plantilla/CSV)
    conf_state: str = ""                          # actual | distinta | invalida | ""
    conf_detail: str = ""
    conf: dict[str, dict[str, Any]] = field(default_factory=dict)   # {conf: {clave: valor}} que corresponde a esta ruta con tus datos


@dataclass
class DataReport:
    found: dict[str, Found]
    tools: list[ToolStatus]
    routes: list[RouteStatus]
    conf_origin: str = ""
    notes: list[str] = field(default_factory=list)
    data_dir: str = ""
    results_dir: str = ""
    n_files: int = 0

    def kind_title(self, reg: ToolRegistry, kind: str) -> str:
        k = reg.data_kinds.get(kind)
        return k.title if k else kind


# ---------------------------------------------------------------------------------------------- lectura de archivos
def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def read_header(path: Path) -> list[str]:
    """Nombres de columna de un CSV (primera línea). Tolera BOM, coma, punto y coma y tabulación."""
    try:
        with open(path, "rb") as f:
            raw = f.readline(65536)
    except OSError:
        return []
    for enc in ("utf-8-sig", "latin-1"):
        try:
            line = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    line = line.strip("\r\n")
    delim = max((",", ";", "\t"), key=line.count)
    return [c.strip().strip('"').strip("'").strip() for c in line.split(delim) if c.strip()]


def roles_of(header: list[str], column_roles: dict[str, list[str]]) -> set[str]:
    rev: dict[str, set[str]] = {}
    for role, names in column_roles.items():
        for n in names:
            rev.setdefault(_squash(n), set()).add(role)
    out: set[str] = set()
    for col in header:
        out |= rev.get(_squash(col), set())
    return out


def _matches_csv(kind: DataKind, roles: set[str], label: str) -> bool:
    d = kind.detect
    if d is None or d.ext != ".csv":
        return False
    if not set(d.require) <= roles or (d.any_of and not (set(d.any_of) & roles)) or (set(d.exclude) & roles):
        return False
    lab = normalize(label)
    if d.name_has and not any(normalize(x) in lab for x in d.name_has):
        return False
    return not any(normalize(x) in lab for x in d.name_not)


def _matches_obj(kind: DataKind, label: str) -> bool:
    d = kind.detect
    if d is None or d.ext != ".obj":
        return False
    lab = normalize(label)
    if d.name_has and not any(normalize(x) in lab for x in d.name_has):
        return False
    return not any(normalize(x) in lab for x in d.name_not)


def scan_inputs(data_dir: Path, reg: ToolRegistry) -> tuple[dict[str, Found], int]:
    """Reconoce los archivos de la carpeta de entrada por sus cabeceras (CSV) o por su carpeta (.obj)."""
    found: dict[str, Found] = {}
    n = 0
    if not data_dir.is_dir():
        return found, 0
    kinds = [k for k in reg.data_kinds.values() if k.detect]
    base_depth = len(data_dir.parts)
    for dirpath, dirnames, filenames in os.walk(data_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "_"))]
        if len(Path(dirpath).parts) - base_depth > MAX_DEPTH:
            dirnames[:] = []
        for fn in sorted(filenames):
            n += 1
            if n > MAX_FILES:
                return found, n
            p = Path(dirpath) / fn
            ext = p.suffix.lower()
            if ext not in (".csv", ".obj"):
                continue
            label = str(p.relative_to(data_dir))
            if ext == ".csv":
                header = read_header(p)
                roles = roles_of(header, reg.column_roles)
                for k in kinds:
                    if _matches_csv(k, roles, label):
                        f = found.setdefault(k.id, Found(k.id, k.title))
                        f.files.append(str(p))
                        if not f.detail:
                            f.detail = "columnas: " + ", ".join(header[:12]) + ("…" if len(header) > 12 else "")
                        break
            else:
                for k in kinds:
                    if _matches_obj(k, label):
                        f = found.setdefault(k.id, Found(k.id, k.title))
                        f.files.append(str(p))
                        break
    for f in found.values():
        if f.kind in ("solids", "solid_target"):
            f.detail = f"{len(f.files)} archivo(s) .obj"
    return found, n


# ---------------------------------------------------------------------------------------------- base de datos y resultados
def _ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)


def inspect_db(path: Path, assay_table: str, comp_table: str, coded_field: str, capped_col: str) -> tuple[dict[str, str], list[str]]:
    """(tipos de dato presentes → detalle, avisos) leyendo la base en modo SOLO LECTURA."""
    got: dict[str, str] = {"db": f"{path.name} ({path.stat().st_size / 1e6:.1f} MB)"}
    notes: list[str] = []
    try:
        con = _ro_connect(path)
    except sqlite3.Error as e:
        return got, [f"No pude abrir {path.name} en modo lectura: {e}"]
    try:
        tables = {r[0].lower(): r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def rows(t: str) -> int:
            return int(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])

        def cols(t: str) -> set[str]:
            return {r[1].lower() for r in con.execute(f'PRAGMA table_info("{t}")')}

        at = tables.get(assay_table.lower())
        if at and (n := rows(at)) > 0:
            got["db_assay"] = f"tabla «{at}»: {n:,} filas"
            if coded_field and coded_field.lower() in cols(at):
                got["db_assay_coded"] = f"columna «{coded_field}» presente"
            if capped_col and capped_col.lower() in cols(at):
                got["db_assay_capped"] = f"columna «{capped_col}» presente"
        ct = tables.get(comp_table.lower())
        if ct and (n := rows(ct)) > 0:
            got["db_composites"] = f"tabla «{ct}»: {n:,} filas"
    except sqlite3.Error as e:
        notes.append(f"No pude leer las tablas de {path.name}: {e}")
    finally:
        con.close()
    return got, notes


def _cfg_value(ctx: ProjectContext, conf: str, key: str, default: str = "") -> str:
    data, _ = ctx.load_cfg(conf)
    v = data.get(key) if isinstance(data, dict) else None
    return v if isinstance(v, str) and v else default


def find_db(ctx: ProjectContext) -> Optional[Path]:
    """Database.db del flujo: la que declara el conf del paso «importar»; si no existe, la única .db de la carpeta de salida."""
    if "importar" in ctx.registry:
        for rp in ctx.resolve_all(ctx.registry.get("importar"), {}, roles=("output",)):
            if rp.path.is_file():
                return rp.path
    res = ctx.anchor_dir("02_Resultados")
    if res.is_dir():
        hits = sorted(res.rglob("*.db"))[:3]
        if len(hits) == 1:
            return hits[0]
    return None


def scan_results(ctx: ProjectContext, found: dict[str, Found]) -> list[str]:
    """Agrega lo que ya existe como resultado (base de datos, modelos intermedios, reporte…). Devuelve avisos."""
    reg = ctx.registry
    notes: list[str] = []
    db = find_db(ctx)
    if db is not None:
        got, nn = inspect_db(db, _cfg_value(ctx, "conf_comp.json", "tabla_assay", "assay"),
                             _cfg_value(ctx, "conf_comp.json", "tabla_composito", "assay_comp"),
                             _cfg_value(ctx, "conf_codificar.json", "campo", ""),
                             (_cfg_value(ctx, "conf_acotamiento.json", "variable", "") + "_ACOT") if _cfg_value(ctx, "conf_acotamiento.json", "variable", "") else "")
        notes += nn
        for kid, detail in got.items():
            if kid in reg.data_kinds:
                f = found.setdefault(kid, Found(kid, reg.data_kinds[kid].title, source="base de datos"))
                f.files.append(str(db))
                f.detail = f.detail or detail
                f.source = "base de datos"
    for tool in reg:
        rk = tool.data.result_kind if tool.data else None
        if not rk or rk == "db" or rk not in reg.data_kinds:
            continue
        outs = ctx.resolve_all(tool, {}, roles=("output",))
        if not outs:
            continue
        present: list[str] = []
        ok = True
        for rp in outs:
            p = rp.path
            if rp.ref.kind == "dir":
                if rk == "report":
                    hits = sorted(p.glob("*.xlsx")) if p.is_dir() else []
                else:
                    hits = [q for q in p.glob("*") if q.is_file()] if p.is_dir() else []
                if not hits:
                    ok = False
                    break
                present.append(str(hits[-1]))
            elif p.is_file() and p.stat().st_size > 0:
                present.append(str(p))
            else:
                ok = False
                break
        if ok and present:
            f = found.setdefault(rk, Found(rk, reg.data_kinds[rk].title, source="resultados"))
            f.files += present
            f.detail = f.detail or f"salida de «{tool.id}»"
            if f.source != "entrada":
                f.source = "resultados"
    return notes


# ---------------------------------------------------------------------------------------------- evaluación (pura)
def _is_base(reg: ToolRegistry, kind: str) -> bool:
    """Tipo de dato que TÚ aportas (se reconoce en la carpeta de entrada) frente a los que producen los pasos."""
    k = reg.data_kinds.get(kind)
    return bool(k and k.detect)


def _alts(reg: ToolRegistry, tool_id: str):
    t = reg.get(tool_id)
    return t.data.alternatives if t.data else []


def evaluate_tools(reg: ToolRegistry, have: set[str], skips: dict[str, list[str]]) -> list[ToolStatus]:
    """Estado de cada herramienta con los datos `have`: listo (ya hay todo), encadenado (posible tras otros pasos) o falta."""
    closure = set(have)
    produced_by: dict[str, str] = {}
    changed = True
    guard = 0
    while changed and guard < 20:
        changed, guard = False, guard + 1
        for t in reg:
            for a in _alts(reg, t.id):
                if all(n in closure for n in a.needs):
                    for g in a.gives:
                        if g not in closure:
                            closure.add(g)
                            produced_by[g] = t.id
                            changed = True

    def chain_for(missing: list[str], seen: set[str]) -> list[str]:
        out: list[str] = []
        for m in missing:
            tid = produced_by.get(m)
            if not tid or tid in seen:
                continue
            seen.add(tid)
            alt = next((a for a in _alts(reg, tid) if all(n in closure for n in a.needs)), None)
            need_more = [n for n in (alt.needs if alt else []) if n not in have]
            out += chain_for(need_more, seen) + [tid]
        return out

    res: list[ToolStatus] = []
    for t in reg:
        alts = _alts(reg, t.id)
        if not alts:
            continue
        sk = skips.get(t.id, [])
        direct = next((a for a in alts if all(n in have for n in a.needs)), None)
        if direct:
            res.append(ToolStatus(t.id, t.title, "listo", direct.id, direct.note, conf_skip=sk))
            continue
        chained = next((a for a in alts if all(n in closure for n in a.needs)), None)
        if chained:
            miss0 = [n for n in chained.needs if n not in have]
            res.append(ToolStatus(t.id, t.title, "encadenado", chained.id, chained.note,
                                  chain=list(dict.fromkeys(chain_for(miss0, set()))), missing=miss0, conf_skip=sk))
            continue
        ranked = sorted(alts, key=lambda a: len([n for n in a.needs if n not in closure]))
        best = ranked[0]
        res.append(ToolStatus(t.id, t.title, "falta", best.id, best.note,
                              missing=[n for n in best.needs if n not in closure],
                              other_ways=[[n for n in a.needs if n not in closure] for a in ranked[1:]], conf_skip=sk))
    return res


def evaluate_route(reg: ToolRegistry, route: Route, have: set[str], prefer: Optional[dict[str, str]] = None) -> RouteStatus:
    """`prefer` = {herramienta: alternativa}: si la ruta no fija la alternativa (`via`), se intenta primero esa (la de tu conf actual)."""
    prefer = prefer or {}
    S = set(have)
    steps: list[StepStatus] = []
    missing_base: list[str] = []
    for st in route.steps:
        tool = reg.get(st.tool)
        if st.unless and st.unless in S:
            steps.append(StepStatus(tool.id, tool.title, "omitido", note=f"ya hay «{reg.data_kinds[st.unless].title}»"))
            continue
        cand = [a for a in _alts(reg, tool.id) if not st.via or a.id == st.via]
        if not st.via and prefer.get(tool.id):
            cand.sort(key=lambda a: a.id != prefer[tool.id])              # estable: la preferida primero
        ok = next((a for a in cand if all(n in S for n in a.needs)), None)
        if ok:
            S |= set(ok.gives)
            steps.append(StepStatus(tool.id, tool.title, "ok", ok.id, note=ok.note))
            continue
        best = min(cand, key=lambda a: len([n for n in a.needs if n not in S])) if cand else None
        miss = [n for n in (best.needs if best else []) if n not in S]
        own = [m for m in miss if _is_base(reg, m)]
        steps.append(StepStatus(tool.id, tool.title, "falta" if own else "depende", best.id if best else "", miss,
                                best.note if best else ""))
        missing_base += [m for m in own if m not in missing_base]
    feasible = all(s.state in ("ok", "omitido") for s in steps)
    return RouteStatus(route, feasible, steps, missing_base)


# ---------------------------------------------------------------------------------------------- análisis completo
def conf_origin(ctx: ProjectContext) -> str:
    """Valor de «origen» de conf_importar_bd.json ('sondajes' si no está)."""
    data, _ = ctx.load_cfg("conf_importar_bd.json")
    if not isinstance(data, dict):
        return ""
    v = data.get("origen", "sondajes")
    return v if isinstance(v, str) else str(v)


def _route_conf(r: Route, rs: RouteStatus) -> dict[str, dict[str, Any]]:
    """Valores de conf que corresponden a la ruta: los que fija el catálogo (`conf`) o, si elige entre alternativas
    (`conf_via`), los de la alternativa que sus datos permiten (p. ej. el plan de sondajes: origen = compositos | sondajes)."""
    if r.conf:
        return {f: dict(v) for f, v in r.conf.items()}
    cv = r.conf_via
    if cv:
        st = next((s for s in rs.steps if s.tool_id == cv.get("tool") and s.state == "ok" and s.via), None)
        if st:
            return {cv["file"]: {cv["key"]: st.via}}
    return {}


def _conf_state(ctx: ProjectContext, conf: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """'actual' si el conf ya corresponde a la ruta, 'distinta' si apunta a otra, '' si la ruta no fija nada."""
    if not conf:
        return "", ""
    for fname, wanted in conf.items():
        data, _ = ctx.load_cfg(fname)
        if not isinstance(data, dict):
            return "distinta", f"{fname} no se pudo leer"
        for key, val in wanted.items():
            cur = data.get(key, "sondajes" if key == "origen" else None)
            if cur != val:
                return "distinta", f"{fname}: {key} = {cur!r} (esta ruta pide {val!r})"
    return "actual", ""


def analyze(ctx: ProjectContext) -> DataReport:
    """Mira todos los datos disponibles y evalúa herramientas y rutas. No ejecuta ni modifica nada."""
    reg = ctx.registry
    data_dir, res_dir = ctx.anchor_dir("01_Data"), ctx.anchor_dir("02_Resultados")
    found, n = scan_inputs(data_dir, reg)
    notes = scan_results(ctx, found)
    have = set(found)
    skips = {t.id: ctx.skip_reasons(t) for t in reg if t.skip_when}
    tools = evaluate_tools(reg, have, skips)
    origin = conf_origin(ctx)
    routes = []
    for r in reg.routes:
        prefer = {r.conf_via["tool"]: origin} if r.conf_via else {}        # entre alternativas válidas, la de tu conf actual primero
        rs = evaluate_route(reg, r, have, prefer)
        rs.conf = _route_conf(r, rs)
        rs.conf_state, rs.conf_detail = _conf_state(ctx, rs.conf)
        routes.append(rs)
    if origin not in ("sondajes", "compositos"):
        notes.append(f"conf_importar_bd.json tiene origen = «{origin}», que no es válido: usa «sondajes» o «compositos» "
                     "(si no, el paso «importar» se detiene con error).")
    if not found:
        notes.append(f"No reconocí ningún archivo de datos en {data_dir}. ¿Es la carpeta de entrada correcta?")
    return DataReport(found, tools, routes, origin, notes, str(data_dir), str(res_dir), n)


# ---------------------------------------------------------------------------------------------- aplicar la ruta al conf
def apply_route_conf(ctx: ProjectContext, route: "Route | RouteStatus | dict[str, dict[str, Any]]") -> list[str]:
    """Pone en los conf los valores que corresponden a la ruta (p. ej. origen = sondajes). Acepta la ruta del catálogo, su
    estado (con el conf que le corresponde según tus datos) o directamente {conf: {clave: valor}}. Con respaldo y verificación.
    Es una acción que pide el usuario; el agente nunca la hace solo."""
    if isinstance(route, RouteStatus):
        conf = route.conf or route.route.conf
    elif isinstance(route, Route):
        conf = route.conf
    else:
        conf = route
    return apply_edits(ctx, [{"file": f, "set": dict(w)} for f, w in conf.items()])


def _describe(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def apply_edits(ctx: ProjectContext, edits: list[dict[str, Any]]) -> list[str]:
    """Aplica ediciones a los conf, agrupadas por archivo, con respaldo y verificación (si el resultado no es el esperado no escribe).

    Cada edición: {"file": conf, "set": {"a.b": valor}}                                  fija valores (claves con puntos = anidadas)
                  {"file": conf, "replace": {"key": "lista[*].campo", "from": x, "to": y}}   cambia x por y en cada clave que coincida
                  {"file": conf, "append": {"key": "lista", "item": {...}, "unique": "campo"}}  agrega un elemento a una lista
    Si solo hay `set`, el cambio es textual (el formato del archivo queda intacto)."""
    log: list[str] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = ctx.config_dir / "_respaldo_agente" / stamp
    by_file: dict[str, list[dict[str, Any]]] = {}
    for e in edits:
        by_file.setdefault(e["file"], []).append(e)
    for fname, ops in by_file.items():
        path = ctx.cfg_path(fname)
        if not path.is_file():
            log.append(f"{fname}: no existe; no se cambió nada.")
            continue
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        before = json.loads(text)
        expected = copy.deepcopy(before)
        descs: list[str] = []
        textual = True
        sets: dict[str, Any] = {}
        for op in ops:
            for key, val in (op.get("set") or {}).items():
                parent = expected
                parts = key.split(".")
                for part in parts[:-1]:
                    parent = parent.setdefault(part, {})
                parent[parts[-1]] = val
                sets[key] = val
                descs.append(f"{key} = {_describe(val)}")
            rp = op.get("replace")
            if rp:
                textual = False
                n = 0
                for jp, v in jpath.get_all(expected, rp["key"]):
                    if v == rp["from"]:
                        jpath.set_value(expected, jp, rp["to"])
                        n += 1
                if n:
                    descs.append(f"{n} valor(es) de «{rp['key']}»: {rp['from']} → {rp['to']}")
            ap = op.get("append")
            if ap:
                textual = False
                lst = jpath.get(expected, ap["key"])
                if isinstance(lst, list):
                    uq = ap.get("unique")
                    if not (uq and any(isinstance(x, dict) and x.get(uq) == ap["item"].get(uq) for x in lst)):
                        lst.append(copy.deepcopy(ap["item"]))
                        descs.append(f"se agregó {ap['item'].get(uq, 'un elemento') if uq else 'un elemento'} a «{ap['key']}»")
        new_text: Optional[str] = text
        if textual:
            for key, val in sets.items():
                new_text = jsonedit.set_value_textual(new_text, key, val) if new_text is not None else None
        else:
            new_text = None
        if new_text is None:                                           # hay listas/reemplazos o JSON compacto: reescritura completa
            new_text = json.dumps(expected, ensure_ascii=False, indent=4) + "\n"
        after = json.loads(new_text)
        if after != expected:
            raise ValueError(f"{fname}: la edición no dio el resultado esperado; no se modificó nada.")
        if after == before:
            log.append(f"{fname}: ya estaba así.")
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / fname)
        payload = new_text.encode("utf-8")
        path.write_bytes((b"\xef\xbb\xbf" + payload) if bom else payload)
        log.append(f"{fname}: " + ", ".join(descs) + f"  (respaldo: {backup_dir / fname})")
    ctx.invalidate()
    return log


# ---------------------------------------------------------------------------------------------- verificación previa con todos los datos
VALID_ORIGINS = ("sondajes", "compositos")


def origin_findings(ctx: ProjectContext, step_ids: list[str], findings: list[Finding]) -> list[Finding]:
    """Hallazgos extra para el paso «importar», mirando TODOS los datos (no solo lo que apunta el conf).

    La verificación por herramienta solo ve las entradas del `origen` actual: con origen «sondajes» (o uno inválido como
    «tablas») pide collar/survey/assay aunque la carpeta tenga un CSV de compósitos que sirve igual. Aquí se avisa de eso y se
    ofrece (con un botón en la interfaz; nunca solo) cambiar el origen a una alternativa que los datos sí permiten."""
    reg = ctx.registry
    if "importar" not in step_ids or "importar" not in reg:
        return []
    origin = conf_origin(ctx)
    out: list[Finding] = []
    if origin not in VALID_ORIGINS:
        out.append(Finding(tool="importar", code="CFG_ORIGIN_INVALID", severity="error",
                           message=f"conf_importar_bd.json: origen = «{origin}» no es válido (solo «sondajes» o «compositos»); el paso se detendría con error.",
                           hint="Elige el origen que corresponde a tus datos (ver abajo) o corrígelo en el conf."))
    broken = origin not in VALID_ORIGINS or any(
        f.tool == "importar" and f.severity == "error" and f.code in ("INPUT_MISSING", "CHAIN_PRODUCER_SKIPPED", "CHAIN_ORDER")
        for f in findings)
    if not broken:
        return out
    have = set(scan_inputs(ctx.anchor_dir("01_Data"), reg)[0])
    fname = reg.get("importar").config or "conf_importar_bd.json"
    for a in _alts(reg, "importar"):
        if a.id == origin or a.id not in VALID_ORIGINS or not all(n in have for n in a.needs):
            continue
        out.append(Finding(
            tool="importar", code="ORIGIN_ALTERNATIVE", severity="warn",
            message=(f"Con los datos de tu carpeta de entrada sí puedes importar con origen «{a.id}» ({a.note}); "
                     f"tu {fname} usa «{origin}», por eso pide otros archivos."),
            hint=f"Cámbialo con el botón «Usar origen {a.id}» (hace respaldo del conf) o edita «origen» a «{a.id}» en {fname}.",
            fix=FixAction(kind="edit_config", detail={"file": fname, "values": {"origen": a.id}, "label": f"Usar origen «{a.id}»"})))
    return out


# ---------------------------------------------------------------------------------------------- texto
ICON = {"listo": "✔", "encadenado": "⛓", "falta": "✖", "ok": "✔", "omitido": "↷", "depende": "⛓"}
STATE_TXT = {"listo": "listo con tus datos", "encadenado": "posible tras otros pasos", "falta": "faltan datos"}


def headline(rep: DataReport, reg: ToolRegistry, route_ids: Optional[list[str]] = None) -> str:
    """Respuesta corta a «¿es viable?» considerando todas las rutas (o las indicadas)."""
    rs = [r for r in rep.routes if not route_ids or r.route.id in route_ids]
    ok = [r for r in rs if r.feasible]
    if ok:
        return "✔ **Sí es viable** con los datos que hay: " + "; ".join(f"«{r.route.title}»" for r in ok) + "."
    if not rs:
        return "No hay rutas definidas en el catálogo."
    near = min(rs, key=lambda r: len(r.missing_base))
    falta = ", ".join(rep.kind_title(reg, m) for m in near.missing_base) or "datos intermedios"
    return (f"✖ **Todavía no es viable** con los datos actuales. La ruta más cercana es «{near.route.title}»: "
            f"te falta {falta}.")


def render_markdown(rep: DataReport, reg: ToolRegistry, compact: bool = False, route_ids: Optional[list[str]] = None) -> str:
    L: list[str] = ["## Datos y procesos posibles" if not compact else "## Con tus datos actuales", "", headline(rep, reg, route_ids), ""]
    if not compact:
        L += [f"Carpeta de entrada: `{rep.data_dir}` ({rep.n_files} archivos revisados)", ""]
        L += ["### Datos encontrados", "", "| Dato | Estado | Dónde |", "|---|---|---|"]
        for kid, k in reg.data_kinds.items():
            f = rep.found.get(kid)
            if f is None:
                if _is_base(reg, kid):
                    L.append(f"| {k.title} | ✖ no encontrado | — |")
                continue
            where = Path(f.files[0]).name + (f" (+{len(f.files) - 1} más)" if len(f.files) > 1 else "") if f.files else ""
            L.append(f"| {k.title} | ✔ ({f.source}) | {where}{(' · ' + f.detail) if f.detail and f.source != 'entrada' else ''} |")
        L += [""]
    L += ["### Rutas para completar el trabajo", ""]
    for r in rep.routes:
        mark = "✔" if r.feasible else "✖"
        extra = ""
        if r.conf_state == "actual":
            extra = "  ← es la que usa tu configuración actual"
        elif r.conf_state == "distinta" and r.feasible:
            extra = f"  ← tu configuración apunta a otra ruta ({r.conf_detail})"
        L.append(f"- {mark} **{r.route.title}**{extra}")
        if not compact:
            L.append(f"    {r.route.description}")
        if not r.feasible:
            L.append("    falta: " + (", ".join(rep.kind_title(reg, m) for m in r.missing_base) or "pasos intermedios (ver abajo)"))
    if not compact:
        L += ["", "### Qué puedes ejecutar con estos datos", "", "| Paso | Estado | Detalle |", "|---|---|---|"]
        for t in rep.tools:
            det = ""
            if t.state == "encadenado":
                det = "primero: " + " → ".join(f"`{c}`" for c in t.chain) if t.chain else ""
            elif t.state == "falta":
                det = "falta: " + ", ".join(rep.kind_title(reg, m) for m in t.missing)
                for other in t.other_ways:
                    det += " · o bien: " + ", ".join(rep.kind_title(reg, m) for m in other)
            elif t.note:
                det = t.note
            if t.conf_skip:
                det += (" · " if det else "") + "tu conf actual lo omite del flujo (puedes pedirlo igual)"
            L.append(f"| `{t.tool_id}` {t.title} | {ICON[t.state]} {STATE_TXT[t.state]} | {det} |")
    for n in rep.notes:
        L += ["", f"> ⚠ {n}"]
    return "\n".join(L) + "\n"
