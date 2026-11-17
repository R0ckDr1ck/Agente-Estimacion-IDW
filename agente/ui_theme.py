"""Aspecto de la interfaz: mismas tarjetas de KPI y paneles que los notebooks de la clase (azul marino + naranja)."""

from __future__ import annotations

import html
from typing import Iterable

import streamlit as st

NARANJA, AZUL, VERDE, ROJO, AMARILLO = "#ff8a1f", "#3fa9f5", "#3ddc97", "#ff5c5c", "#ffc14d"

CSS = """
<style>
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:6px 0 12px 0}
.kpi{flex:1;min-width:170px;background:#0d1f3a;border:1px solid #1f3b63;border-top:3px solid #ff8a1f;border-radius:10px;padding:10px 14px}
.kpi .r{font-size:11px;color:#9fb3cc;text-transform:uppercase;letter-spacing:.05em}
.kpi .v{font-size:24px;font-weight:700;color:#fff;margin:2px 0;overflow-wrap:anywhere}
.kpi .v.mono{font-family:ui-monospace,Consolas,monospace;font-size:18px;color:#3ddc97}
.kpi .s{font-size:12px;color:#ff8a1f}
.kpi.ok{border-top-color:#3ddc97}.kpi.bad{border-top-color:#ff5c5c}.kpi.warn{border-top-color:#ffc14d}.kpi.info{border-top-color:#3fa9f5}
.kpi.ok .s{color:#3ddc97}.kpi.bad .s{color:#ff5c5c}.kpi.warn .s{color:#ffc14d}.kpi.info .s{color:#3fa9f5}
.pnl{background:linear-gradient(135deg,#0a1628 0%,#0f2240 100%);border:1px solid #1f3b63;border-radius:14px;padding:14px 20px;margin:8px 0}
.pnl h3{margin:0 0 4px 0;font-size:19px;color:#fff;font-weight:700}.pnl h3 b{color:#ff8a1f}
.pnl .sub{color:#9fb3cc;font-size:13px}
.quote{background:#12284a;border-left:4px solid #ff8a1f;border-radius:8px;padding:10px 14px;margin:6px 0;font-size:15px;line-height:1.5;color:#e6edf7}
.quote .r{font-size:11px;color:#ff8a1f;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:2px}
.nota{font-size:12px;color:#9fb3cc;margin-top:6px;border-left:3px solid #ff8a1f;padding-left:9px}
.badge{display:inline-block;white-space:nowrap;padding:3px 12px;border-radius:999px;font-weight:800;font-size:12px;letter-spacing:.04em}
.badge.ok{background:#3ddc97;color:#002614}.badge.bad{background:#ff5c5c;color:#1a0000}
.badge.warn{background:#ffc14d;color:#2a1a00}.badge.info{background:#3fa9f5;color:#001a2e}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def kpis(items: Iterable[tuple], tone: str = "") -> None:
    """Fila de tarjetas. Cada ítem: (rótulo, valor, subtítulo[, tono][, mono])."""
    cards = []
    for it in items:
        label, value, sub = it[0], it[1], it[2] if len(it) > 2 else ""
        t = it[3] if len(it) > 3 and it[3] else tone
        mono = " mono" if len(it) > 4 and it[4] else ""
        cards.append(f"<div class='kpi {t}'><div class='r'>{esc(label)}</div><div class='v{mono}'>{esc(value)}</div>"
                     f"<div class='s'>{esc(sub)}</div></div>")
    st.markdown("<div class='kpis'>" + "".join(cards) + "</div>", unsafe_allow_html=True)


def quote(label: str, text: str) -> None:
    st.markdown(f"<div class='quote'><span class='r'>{esc(label)}</span>{esc(text)}</div>", unsafe_allow_html=True)


def panel(title_html: str, sub: str = "") -> None:
    st.markdown(f"<div class='pnl'><h3>{title_html}</h3>" + (f"<div class='sub'>{esc(sub)}</div>" if sub else "") + "</div>",
                unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f"<div class='nota'>{esc(text)}</div>", unsafe_allow_html=True)


def badge(text: str, tone: str) -> str:
    return f"<span class='badge {tone}'>{esc(text)}</span>"


STATUS_TONE = {"completed": "ok", "ready": "warn", "blocked": "bad", "failed": "bad", "aborted": "bad",
               "running": "info", "info": "info", "needs_input": "warn"}
STATUS_LABEL = {"completed": "COMPLETADO", "ready": "LISTO · SIMULACIÓN", "blocked": "BLOQUEADO", "failed": "FALLÓ",
                "aborted": "ABORTADO", "running": "EJECUTANDO", "info": "INFORMATIVO", "needs_input": "FALTA INFORMACIÓN"}


def fmt_s(x: float | None) -> str:
    if x is None:
        return "n/d"
    if x >= 120:
        return f"{x / 60:.1f} min"
    return f"{x:.1f} s"


def fmt_int(x: float | int) -> str:
    return f"{x:,.0f}".replace(",", ".")
