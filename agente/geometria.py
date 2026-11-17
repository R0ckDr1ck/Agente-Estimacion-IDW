"""Geometría barata para avisar ANTES de ejecutar (v3.4): ¿el modelo de bloques llega hasta el sólido con el que se recorta?

No evalúa «punto en sólido» (eso lo hace la herramienta): solo compara cajas. Si la caja del modelo y la del sólido no se
tocan, ningún centroide puede caer dentro y el paso fallaría seguro; si se tocan solo en parte, se avisa cuánto cubre.

Convención del bearing (la misma de EsquemaDesdeBloques.py): 90 = sin rotación (X local = Este). El eje local u apunta al
azimut `bearing` (sin b, cos b), el eje v es (-cos b, sin b) y w es Z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

Vec = tuple[float, float, float]
_EPS = 1e-6


@dataclass(frozen=True)
class Box:
    lo: Vec
    hi: Vec

    def volume(self) -> float:
        return math.prod(max(h - l, 0.0) for l, h in zip(self.lo, self.hi))


_OBJ_CACHE: dict[tuple[str, int, int], Optional[Box]] = {}


def obj_bbox(path: Path) -> Optional[Box]:
    """Caja (mín/máx de los vértices «v x y z») de un .obj. None si no se puede leer o no tiene vértices. Se guarda en memoria
    por (ruta, tamaño, fecha) para no releer un sólido de millones de vértices en cada verificación."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_size, int(st.st_mtime))
    if key in _OBJ_CACHE:
        return _OBJ_CACHE[key]
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    n = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                if line[:2] != b"v ":
                    continue
                parts = line.split()
                try:
                    xyz = (float(parts[1]), float(parts[2]), float(parts[3]))
                except (IndexError, ValueError):
                    continue
                n += 1
                for i in range(3):
                    if xyz[i] < lo[i]:
                        lo[i] = xyz[i]
                    if xyz[i] > hi[i]:
                        hi[i] = xyz[i]
    except OSError:
        return None
    box = Box(tuple(lo), tuple(hi)) if n else None          # type: ignore[arg-type]
    _OBJ_CACHE[key] = box
    return box


def _num(v: Any) -> Optional[float]:
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _axes(bearing: float) -> tuple[Vec, Vec]:
    s, c = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
    return (s, c, 0.0), (-c, s, 0.0)


@dataclass(frozen=True)
class Grid:
    """Grilla de un modelo nuevo, tal como la define el esquema de conf_crear_mb.json."""
    origin: Vec
    size: Vec
    n: tuple[int, int, int]
    bearing: float

    @property
    def n_blocks(self) -> int:
        return self.n[0] * self.n[1] * self.n[2]

    def box(self) -> Box:
        """Caja envolvente (en ejes del mundo) de la grilla; con rotación es la caja de las 4 esquinas, algo mayor que la grilla."""
        u, v = _axes(self.bearing)
        ex, ey, ez = (self.n[i] * self.size[i] for i in range(3))
        pts = [(self.origin[0] + a * ex * u[0] + b * ey * v[0], self.origin[1] + a * ex * u[1] + b * ey * v[1])
               for a in (0, 1) for b in (0, 1)]
        return Box((min(p[0] for p in pts), min(p[1] for p in pts), self.origin[2]),
                   (max(p[0] for p in pts), max(p[1] for p in pts), self.origin[2] + ez))


def grid_from_schema(esq: Any) -> Optional[Grid]:
    """Grid desde {origen_x/y/z, extension_x/y/z, x_size/y_size/z_size, bearing}; None si falta algo o no es válido."""
    if not isinstance(esq, dict):
        return None
    vals = {k: _num(esq.get(k)) for k in ("origen_x", "origen_y", "origen_z", "extension_x", "extension_y", "extension_z",
                                          "x_size", "y_size", "z_size")}
    if any(v is None for v in vals.values()) or any(vals[k] <= 0 for k in ("x_size", "y_size", "z_size")):   # type: ignore[operator]
        return None
    bearing = _num(esq.get("bearing"))
    n = tuple(max(int(math.ceil(vals[f"extension_{a}"] / vals[f"{a}_size"] - _EPS)), 0) for a in "xyz")   # type: ignore[operator]
    return Grid((vals["origen_x"], vals["origen_y"], vals["origen_z"]),                                      # type: ignore[arg-type]
                (vals["x_size"], vals["y_size"], vals["z_size"]), n,                                       # type: ignore[arg-type]
                90.0 if bearing is None else bearing)


def csv_centroid_box(path: Path, cols: tuple[str, str, str]) -> Optional[Box]:
    """Caja de los centroides de un modelo en disco (lee solo las 3 columnas, por tandas)."""
    try:
        import pandas as pd
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for chunk in pd.read_csv(path, usecols=list(cols), chunksize=500_000, encoding="utf-8-sig"):
            for i, c in enumerate(cols):
                col = pd.to_numeric(chunk[c], errors="coerce").dropna()
                if len(col):
                    lo[i], hi[i] = min(lo[i], float(col.min())), max(hi[i], float(col.max()))
    except Exception:                                                    # noqa: BLE001  (archivo ilegible o sin esas columnas)
        return None
    return None if math.inf in lo else Box(tuple(lo), tuple(hi))      # type: ignore[arg-type]


def overlap(model: Box, solid: Box) -> tuple[list[bool], float]:
    """(¿se tocan en cada eje X, Y, Z?, fracción del volumen del sólido que queda dentro de la caja del modelo)."""
    touch = [model.lo[i] <= solid.hi[i] and solid.lo[i] <= model.hi[i] for i in range(3)]
    if not all(touch):
        return touch, 0.0
    inter = Box(tuple(max(model.lo[i], solid.lo[i]) for i in range(3)),
                tuple(min(model.hi[i], solid.hi[i]) for i in range(3)))              # type: ignore[arg-type]
    vol = solid.volume()
    return touch, (inter.volume() / vol if vol > 0 else 1.0)


def fit_schema(grid: Grid, solid: Box, clip: bool) -> dict[str, float]:
    """Nuevo origen y extensión (alineados a los bloques) para que la grilla cubra el sólido.

    clip = True: la grilla queda justo sobre el sólido (el modelo se recortará a él, no hace falta más).
    clip = False: se conserva lo que ya cubría y solo se agranda."""
    u, v = _axes(grid.bearing)
    axes = (u, v, (0.0, 0.0, 1.0))
    corners = [(x, y, z) for x in (solid.lo[0], solid.hi[0]) for y in (solid.lo[1], solid.hi[1]) for z in (solid.lo[2], solid.hi[2])]
    lo_n, hi_n = [], []
    for i in range(3):
        d = [sum((c[k] - grid.origin[k]) * axes[i][k] for k in range(3)) for c in corners]
        size = grid.size[i]
        a = math.floor(min(d) / size + _EPS)
        b = math.ceil(max(d) / size - _EPS)
        if not clip:
            a, b = min(a, 0), max(b, grid.n[i])
        lo_n.append(a)
        hi_n.append(max(b, a + 1))
    ox = grid.origin[0] + lo_n[0] * grid.size[0] * u[0] + lo_n[1] * grid.size[1] * v[0]
    oy = grid.origin[1] + lo_n[0] * grid.size[0] * u[1] + lo_n[1] * grid.size[1] * v[1]
    oz = grid.origin[2] + lo_n[2] * grid.size[2]
    r = lambda x: round(x, 4) + 0.0                                                                   # noqa: E731
    return {"origen_x": r(ox), "origen_y": r(oy), "origen_z": r(oz),
            "extension_x": r((hi_n[0] - lo_n[0]) * grid.size[0]), "extension_y": r((hi_n[1] - lo_n[1]) * grid.size[1]),
            "extension_z": r((hi_n[2] - lo_n[2]) * grid.size[2])}
