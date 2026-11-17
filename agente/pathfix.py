"""Reubicación de rutas en los conf_*.json (v3: TODAS las rutas de TODOS los conf, no solo las del plan actual).

Problema de la v2: solo se corregían las rutas de las herramientas del plan y solo si el archivo no existía; los
conf quedaban «mezclados» (unas rutas en la carpeta nueva y otras en la anterior) y la corrida fallaba al cambiar
de configuración. Ahora se recorre cada texto de cada conf_*.json y, si es una ruta absoluta, se lleva a las carpetas
elegidas (entrada = 01_Data, salida = 02_Resultados, Configuraciones). Reglas, en este orden:

  1. La ruta contiene una carpeta ANCLA (01_Data, 02_Resultados, Configuraciones): se conserva todo lo que sigue a la
     ancla y se cuelga de la carpeta equivalente. No se exige que el archivo exista (una entrada puede ser la salida de
     otro paso). Si es una entrada de datos que no existe en esa posición pero hay UN único archivo con ese nombre
     dentro de la misma carpeta, se usa ese.
  2. No tiene ancla pero cuelga de la «raíz antigua» del proyecto (el prefijo que comparten las rutas con ancla,
     p. ej. conf_proyecto.json → raiz): se cuelga de la raíz nueva.
  3. No tiene ancla ni raíz conocida y es una ENTRADA declarada en el catálogo: se busca un único archivo con el mismo
     nombre en las carpetas de trabajo (si hay varios queda «ambigua» y no se toca).
  4. Si nada de lo anterior aplica, la ruta se deja igual y se informa («sin equivalente»), nunca se adivina.

Aplicar los cambios es una edición TEXTUAL del JSON (solo cambian las cadenas de las rutas): el formato, el orden y
la codificación (BOM) de tu archivo se conservan. Siempre hay respaldo previo y verificación posterior.
"""

from __future__ import annotations

import glob
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .context import ProjectContext, ResolvedPath, norm_path
from .models import PathFix


class PathFixError(Exception):
    pass


_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/])")


def is_abs_path_str(raw: str) -> bool:
    """¿El texto es una ruta absoluta de Windows (D:\\..., D:/... o \\\\servidor\\...)?"""
    return bool(_ABS_RE.match(raw.strip())) if isinstance(raw, str) else False


def iter_strings(data: Any, prefix: str = "") -> Iterator[tuple[str, str]]:
    """(ruta_json, texto) de TODOS los valores de texto de un JSON. Mismo formato que jpath ('a.b', 'lista[0].x')."""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from iter_strings(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from iter_strings(v, f"{prefix}[{i}]")
    elif isinstance(data, str):
        yield prefix, data


def conf_names(ctx: ProjectContext) -> list[str]:
    """Nombres de los conf_*.json de la carpeta de configuraciones (no las subcarpetas de respaldo)."""
    try:
        return sorted(p.name for p in ctx.config_dir.glob("conf_*.json") if p.is_file())
    except OSError:
        return []


def _ci_in(part: str, anchors: Iterable[str]) -> Optional[str]:
    low = part.casefold()
    for a in anchors:
        if a.casefold() == low:
            return a
    return None


def _split_at_anchor(raw: str, anchors: Iterable[str]) -> Optional[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """(partes antes de la ancla, ancla, partes después) con la PRIMERA ancla de izquierda a derecha."""
    parts = Path(raw).parts
    for i, part in enumerate(parts):
        a = _ci_in(part, anchors)
        if a:
            return parts[:i], a, parts[i + 1:]
    return None


def new_project_root(ctx: ProjectContext) -> Path:
    """Carpeta que hace de «raíz del proyecto» para rutas sin ancla (p. ej. conf_proyecto.json → raiz)."""
    cfg = ctx.cfg
    if cfg.has_workspace and cfg.data_dir is not None:
        return Path(cfg.data_dir).parent
    return ctx.root


def _input_roles(ctx: ProjectContext) -> dict[tuple[str, str], str]:
    """(conf, ruta_json) → 'input' | 'output' | 'modifies', según el catálogo. Solo rutas que salen de un conf."""
    roles: dict[tuple[str, str], str] = {}
    for tool in ctx.registry:
        for rp in ctx.resolve_all(tool, {}):
            if rp.cfg and rp.jpath:
                key = (rp.cfg, rp.jpath)
                cur = roles.get(key)
                if cur is None or (cur == "input" and rp.role != "input"):
                    roles[key] = rp.role            # si alguna herramienta la escribe, cuenta como salida (más prudente)
    return roles


def _unique_by_name(base: Path, name: str, cap: int = 20000) -> tuple[Optional[Path], list[Path]]:
    """Único archivo `name` dentro de `base` (recursivo). (ruta, [candidatos si hay varios])."""
    hits: list[Path] = []
    if not base.is_dir() or not name:
        return None, hits
    try:
        for n, h in enumerate(base.rglob(glob.escape(name))):
            if h.is_file():
                hits.append(h)
            if len(hits) > 5 or n > cap:
                break
    except OSError:
        return None, hits
    return (hits[0], []) if len(hits) == 1 else (None, hits)


def _equivalent(ctx: ProjectContext, raw: str, role: Optional[str], old_roots: set[str], new_root: Path
                ) -> tuple[Optional[Path], str, list[str]]:
    """(candidato, motivo, ambiguos) para una ruta absoluta. candidato None = no se pudo / ambigua."""
    anchors = ctx.cfg.path_anchors
    where = "en la carpeta elegida" if ctx.cfg.has_workspace else "dentro del proyecto"
    sp = _split_at_anchor(raw, anchors)
    if sp:
        _, anchor, rest = sp
        base = ctx.anchor_dir(anchor)
        cand = base.joinpath(*rest)
        if not cand.exists() and role == "input" and cand.suffix and anchor != ctx.cfg.config_dir:
            hit, many = _unique_by_name(base, cand.name)       # misma carpeta, otro subdirectorio
            if hit is not None:
                return hit, f"único archivo '{cand.name}' dentro de '{anchor}' {where}", []
            if many:
                return None, f"varios archivos '{cand.name}' dentro de '{anchor}'", [str(h) for h in many[:5]]
        return cand, f"misma ubicación relativa bajo '{anchor}' {where}", []

    # sin ancla: ¿cuelga de la raíz antigua del proyecto?
    parts = Path(raw).parts
    for old in sorted(old_roots, key=len, reverse=True):
        old_parts = Path(old).parts
        if len(parts) >= len(old_parts) and [norm_path(x) for x in parts[:len(old_parts)]] == [norm_path(x) for x in old_parts]:
            return new_root.joinpath(*parts[len(old_parts):]), "cuelga de la raíz anterior del proyecto", []

    # sin ancla ni raíz: solo entradas declaradas (se busca un único archivo con ese nombre en las carpetas de trabajo)
    if role == "input" and Path(raw).suffix:
        hits: list[Path] = []
        for a in anchors:
            h, many = _unique_by_name(ctx.anchor_dir(a), Path(raw).name)
            hits += [h] if h is not None else many
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            return hits[0], f"único archivo '{Path(raw).name}' encontrado dentro del proyecto", []
        if len(hits) > 1:
            return None, "varios archivos con ese nombre", [str(h) for h in hits[:5]]
    return None, "sin equivalente en tus carpetas", []


def plan_relocation(ctx: ProjectContext) -> list[PathFix]:
    """Todas las rutas absolutas de todos los conf_*.json que NO están donde corresponde con las carpetas actuales.
    Los elementos con `new` vacío son rutas que no se pueden reubicar (ambiguas o sin equivalente)."""
    anchors = ctx.cfg.path_anchors
    roles = _input_roles(ctx)
    entries: list[tuple[str, str, str]] = []            # (conf, ruta_json, valor)
    for name in conf_names(ctx):
        data, _ = ctx.load_cfg(name)
        if data is None:
            continue
        entries += [(name, jp, v) for jp, v in iter_strings(data) if is_abs_path_str(v)]

    # raíces antiguas = lo que hay ANTES de la ancla en las rutas con ancla (la más frecuente primero)
    freq: Counter[str] = Counter()
    for _, _, v in entries:
        sp = _split_at_anchor(v, anchors)
        if sp and sp[0]:
            freq[str(Path(*sp[0]))] += 1
    old_roots = {r for r, _ in freq.most_common(3)}
    new_root = new_project_root(ctx)

    fixes: dict[tuple[str, str], PathFix] = {}
    seen_cache: dict[str, tuple[Optional[Path], str, list[str]]] = {}
    for name, jp, raw in entries:
        role = roles.get((name, jp))
        ck = f"{role}|{raw}"
        if ck not in seen_cache:
            seen_cache[ck] = _equivalent(ctx, raw, role, old_roots, new_root)
        cand, reason, amb = seen_cache[ck]
        if cand is None:
            if amb or not ctx.inside_root(Path(raw)):
                fixes[(name, jp)] = PathFix(config_file=name, jpath=jp, old=raw, new="", reason=reason,
                                            confidence="media", ambiguous=amb)
            continue
        if norm_path(cand) == norm_path(raw):
            continue
        fixes[(name, jp)] = PathFix(config_file=name, jpath=jp, old=raw, new=str(cand), reason=reason,
                                    confidence="alta" if "misma ubicación" in reason or "raíz" in reason else "media")
    return list(fixes.values())


def relocate_string(ctx: ProjectContext, raw: str, role: str = "input") -> str:
    """La misma regla de reubicación para UN texto (p. ej. una ruta que viene en un CSV de parámetros). Si no se puede, queda igual."""
    if not is_abs_path_str(raw):
        return raw
    cand, _, _ = _equivalent(ctx, raw, role, set(), new_project_root(ctx))
    return str(cand) if cand is not None else raw


def relocate_value(ctx: ProjectContext, value: Any) -> tuple[Any, int]:
    """Reubica todas las rutas absolutas de una estructura (listas/dicts). Devuelve (valor nuevo, cuántas cambiaron)."""
    n = 0

    def walk(v: Any) -> Any:
        nonlocal n
        if isinstance(v, str):
            new = relocate_string(ctx, v)
            if norm_path(new) != norm_path(v) if is_abs_path_str(v) else False:
                n += 1
                return new
            return v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(value), n


def propose_fixes(ctx: ProjectContext, resolved: Iterable[ResolvedPath] = ()) -> list[PathFix]:
    """Compatibilidad con la v2: ya no depende de las rutas del plan, siempre revisa todos los conf."""
    return plan_relocation(ctx)


def summarize(fixes: list[PathFix]) -> dict[str, dict[str, int]]:
    """{conf: {'reubicar': n, 'sin_resolver': m}} para mostrar «qué se va a tocar» sin listar cada ruta."""
    out: dict[str, dict[str, int]] = {}
    for f in fixes:
        d = out.setdefault(f.config_file, {"reubicar": 0, "sin_resolver": 0})
        d["reubicar" if f.new and not f.ambiguous else "sin_resolver"] += 1
    return out


def apply_fixes(ctx: ProjectContext, fixes: list[PathFix], backup_root: Path | None = None) -> list[str]:
    """Aplica las correcciones (solo las no ambiguas). Devuelve las líneas de bitácora.
    Respaldo previo en <Configuraciones>/_respaldo_agente/<fecha>/. Verifica que el JSON resultante sea válido,
    que ya no quede la ruta vieja y que esté la nueva; si algo falla, no modifica nada y lanza PathFixError."""
    todo = [f for f in fixes if f.new and not f.ambiguous]
    if not todo:
        return []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = (backup_root or (ctx.config_dir / "_respaldo_agente")) / stamp
    by_file: dict[str, list[PathFix]] = {}
    for f in todo:
        by_file.setdefault(f.config_file, []).append(f)

    # 1) se prepara todo en memoria: si un conf falla, ninguno se toca
    staged: list[tuple[str, Path, bytes, bytes, list[PathFix]]] = []
    for name, group in by_file.items():
        path = ctx.cfg_path(name)
        original = path.read_bytes()
        has_bom = original.startswith(b"\xef\xbb\xbf")
        new_text = original.decode("utf-8-sig")
        olds: dict[str, str] = {}
        for fx in group:
            if olds.setdefault(fx.old, fx.new) != fx.new:
                raise PathFixError(f"{name}: la misma ruta '{fx.old}' tendría dos destinos distintos; no se modificó nada.")
        for old, new in olds.items():
            for ensure_ascii in (False, True):
                old_tok = json.dumps(old, ensure_ascii=ensure_ascii)
                if old_tok in new_text:
                    new_text = new_text.replace(old_tok, json.dumps(new, ensure_ascii=ensure_ascii))
                    break
            else:
                raise PathFixError(f"{name}: no encontré la cadena exacta de la ruta '{old}' para reemplazarla.")
        try:
            data = json.loads(new_text)
        except ValueError as e:
            raise PathFixError(f"{name}: el JSON resultante sería inválido ({e}); no se modificó nada.") from e
        values = {v for _, v in iter_strings(data)}
        for old, new in olds.items():
            if new not in values or (old != new and old in values):
                raise PathFixError(f"{name}: tras corregir, la ruta '{old}' no quedó como se esperaba; no se modificó nada.")
        payload = new_text.encode("utf-8")
        staged.append((name, path, original, (b"\xef\xbb\xbf" + payload) if has_bom else payload, group))

    # 2) respaldo + escritura
    log: list[str] = []
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name, path, original, payload, group in staged:
        shutil.copy2(path, backup_dir / name)
        path.write_bytes(payload)
        for fx in group:
            log.append(f"{name}: {fx.jpath}\n    - {fx.old}\n    + {fx.new}   ({fx.reason})")
    ctx.invalidate()
    log.append(f"Respaldo de los conf originales en: {backup_dir}")
    return log
