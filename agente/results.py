"""Reporte final: lee lo que dejó el flujo (Excel del reporte de recursos + resumen del plan) y lo deja listo para mostrar.

Es de SOLO LECTURA y no calcula nada nuevo: toma las cifras tal como las escribió `GenerarReporte.py`, para que lo que
se ve en pantalla coincida con el Excel. Cada lector tolera hojas faltantes (el reporte puede ejecutarse sin sólido,
sin comparación o sin plan) y devuelve None en lugar de fallar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pandas as pd

CATEGORIES = ["Medido", "Indicado", "Inferido"]
SHEETS = {"resumen": "Resumen", "potencial": "Resumen_Potencial", "comparacion": "Comparacion_Categ", "ganancia": "Ganancia_Plan"}


@dataclass
class ReportBundle:
    xlsx: Path
    modified: float
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)     # tal cual en el Excel (sin cabecera)
    meta: dict[str, str] = field(default_factory=dict)                 # unidad, fecha, cut-off


def list_reports(folder: Path) -> list[Path]:
    """Excel de reporte de la carpeta, del más reciente al más antiguo."""
    try:
        files = [p for p in Path(folder).glob("*.xlsx") if not p.name.startswith("~$")]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def load_report(xlsx: Path) -> ReportBundle:
    sheets = pd.read_excel(xlsx, sheet_name=None, header=None)
    b = ReportBundle(xlsx=Path(xlsx), modified=Path(xlsx).stat().st_mtime, sheets=sheets)
    first = next(iter(sheets.values()), None)
    if first is not None:
        for v in first.iloc[:6, 0].dropna().astype(str):
            for key, label in (("unidad", "Unidad:"), ("fecha", "Fecha:")):
                if v.startswith(label):
                    b.meta[key] = v.split(":", 1)[1].strip()
        for v in first.iloc[:6].astype(str).to_numpy().ravel():
            if v.startswith("Cut-off"):
                b.meta["cutoff"] = v
                break
    return b


# ------------------------------------------------------------------------------------------------- utilidades de hoja
def _find_row(df: pd.DataFrame, first_cell: str, start: int = 0, col: int = 0) -> Optional[int]:
    for i in range(start, len(df)):
        v = df.iat[i, col]
        if isinstance(v, str) and v.strip() == first_cell:
            return i
    return None


def _block(df: pd.DataFrame, header_row: int, stop_blank: bool = True) -> pd.DataFrame:
    """Tabla que empieza en `header_row` (cabecera) y llega hasta la primera fila vacía."""
    header = [str(h) if pd.notna(h) else "" for h in df.iloc[header_row]]
    rows = []
    for i in range(header_row + 1, len(df)):
        r = df.iloc[i]
        if stop_blank and r.isna().all():
            break
        rows.append(r.tolist())
    out = pd.DataFrame(rows, columns=header)
    return out.loc[:, [c for c in out.columns if c != ""]]


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


# ------------------------------------------------------------------------------------------------- hojas
def resource_table(b: ReportBundle) -> Optional[pd.DataFrame]:
    """Hoja «Resumen» como tabla plana: Dominio · Categoría · Tonelaje · Ley (con el dominio repetido en cada fila)."""
    df = b.sheets.get(SHEETS["resumen"])
    if df is None:
        return None
    h = _find_row(df, "Dominio")
    if h is None:
        return None
    body = df.iloc[h + 2:]                                     # la fila siguiente a la cabecera trae las unidades
    rows, dom = [], None
    for _, r in body.iterrows():
        if r.iloc[:4].isna().all():
            break
        if pd.notna(r.iloc[0]):
            dom = str(r.iloc[0]).replace(".0", "") if not isinstance(r.iloc[0], str) else r.iloc[0]
        if pd.notna(r.iloc[1]):
            rows.append({"Dominio": dom, "Categoría": str(r.iloc[1]), "Tonelaje (t)": _num(r.iloc[2]), "Ley": _num(r.iloc[3])})
    if not rows:
        return None
    metal = str(df.iat[h, 3]) if pd.notna(df.iat[h, 3]) else "Ley"
    out = pd.DataFrame(rows).rename(columns={"Ley": f"Ley {metal}"})
    out.attrs["metal"] = metal
    out.attrs["unit"] = str(df.iat[h + 1, 3]) if pd.notna(df.iat[h + 1, 3]) else ""
    return out


def totals_by_category(table: pd.DataFrame) -> pd.DataFrame:
    """Suma de todos los dominios por categoría, con ley media ponderada por tonelaje."""
    ley = [c for c in table.columns if c.startswith("Ley")][0]
    if (table["Dominio"] == "Total").any():                    # el propio reporte ya trae el total: se usa tal cual
        tot = table[table["Dominio"] == "Total"].drop(columns="Dominio")
        return tot[tot["Categoría"].isin(CATEGORIES + ["Medido & Indicado"])].reset_index(drop=True)
    table = table[table["Dominio"] != "Total"]
    rows = []
    for cat in CATEGORIES + ["Medido & Indicado"]:
        sub = table[table["Categoría"] == cat]
        if sub.empty:
            continue
        t = sub["Tonelaje (t)"].sum()
        g = (sub["Tonelaje (t)"] * sub[ley]).sum() / t if t else 0.0
        rows.append({"Categoría": cat, "Tonelaje (t)": t, ley: g})
    return pd.DataFrame(rows)


def comparison_total(b: ReportBundle) -> Optional[pd.DataFrame]:
    """Bloque «Total» de la hoja Comparacion_Categ: anterior vs actual por categoría."""
    df = b.sheets.get(SHEETS["comparacion"])
    if df is None:
        return None
    h = _find_row(df, "Dominio")
    if h is None:
        return None
    t = _block(df, h)
    t = t.drop(columns=[c for c in t.columns if c == "Dominio"], errors="ignore")
    t = t[t["Categoria"].notna()].rename(columns={"Categoria": "Categoría"})
    for c in t.columns[1:]:
        t[c] = t[c].map(_num)
    return t.reset_index(drop=True)


def gain_tables(b: ReportBundle) -> tuple[dict[str, Any], Optional[pd.DataFrame]]:
    """(datos del plan, tabla de ganancia por categoría) de la hoja Ganancia_Plan."""
    df = b.sheets.get(SHEETS["ganancia"])
    if df is None:
        return {}, None
    plan: dict[str, Any] = {}
    p = _find_row(df, "Plan de sondajes")
    if p is not None:
        for i in range(p + 1, min(p + 8, len(df))):
            k, v = df.iat[i, 0], df.iat[i, 1]
            if pd.isna(k):
                break
            plan[str(k)] = v
    g = _find_row(df, "Categoria")
    table = None
    if g is not None:
        table = _block(df, g)
        for c in table.columns[1:]:
            table[c] = table[c].map(_num)
        table = table.rename(columns={"Categoria": "Categoría"})
    return plan, table


def read_plan_summary(path: Path) -> Optional[dict]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def fmt_t(x: float) -> str:
    """Toneladas con separador de miles y unidad compacta (Mt/kt)."""
    if x != x:
        return "n/d"
    if abs(x) >= 1e6:
        return f"{x / 1e6:,.2f} Mt"
    if abs(x) >= 1e3:
        return f"{x / 1e3:,.1f} kt"
    return f"{x:,.0f} t"
