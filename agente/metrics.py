"""Métricas del proceso, en el estilo de los notebooks de la clase: tiempo, tokens, velocidad, memoria y costo comparado.

Todo sale de lo que el propio agente ya guarda en `runs/<id>/state.json` (PlannerMetrics + duración de cada paso).
No hay estimaciones inventadas: si un dato no se midió, se muestra «n/d».
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .models import RunState

# Precio de REFERENCIA de una API comercial pequeña (US$ por 1 millón de tokens), como en el notebook de la clase.
# Solo sirve para comparar órdenes de magnitud: confirma la tabla vigente del proveedor antes de decidir nada.
API_ENTRADA_1M = 0.15
API_SALIDA_1M = 0.60


@dataclass
class RunTotals:
    n_requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    llm_seconds: float = 0.0
    exec_seconds: float = 0.0
    steps_ok: int = 0
    steps_total: int = 0
    cost_usd: float = 0.0               # costo estimado en la nube (precios de referencia de cada modelo)
    cloud_calls: int = 0                # solicitudes e historial del chat que usaron un modelo en la nube

    @property
    def api_cost_usd(self) -> float:
        return api_cost(self.tokens_in, self.tokens_out)


def api_cost(tokens_in: float, tokens_out: float, price_in: float = API_ENTRADA_1M, price_out: float = API_SALIDA_1M) -> float:
    return tokens_in * price_in / 1e6 + tokens_out * price_out / 1e6


def steps_frame(state: RunState) -> pd.DataFrame:
    rows = [{"Paso": s.tool_id, "Estado": s.status, "Segundos": s.duration_s or 0.0} for s in state.plan.steps]
    return pd.DataFrame(rows, columns=["Paso", "Estado", "Segundos"])


def runs_frame(states: list[RunState]) -> pd.DataFrame:
    """Una fila por solicitud (las más antiguas primero)."""
    rows = []
    for st in sorted(states, key=lambda s: s.created_at):
        m = st.metrics
        rows.append({
            "Ejecución": st.run_id, "Solicitud": st.request[:70], "Estado": st.status,
            "Fuente": st.planner_source or "n/d", "Modelo": (m.model if m else "") or "—",
            "Proveedor": (m.provider if m else "") or "—", "Costo (US$)": round(m.cost_usd, 6) if m and m.cost_usd is not None else None,
            "Interpretación (s)": round(m.wall_seconds, 2) if m else None,
            "Tokens entrada": m.tokens_in if m else None, "Tokens salida": m.tokens_out if m else None,
            "Tokens/s": round(m.tokens_per_s, 1) if m and m.tokens_per_s else None,
            "Reintentos": m.retries if m else None,
            "Pasos ok": sum(s.status == "ok" for s in st.plan.steps), "Pasos": len(st.plan.steps),
            "Ejecución (s)": round(sum(s.duration_s or 0 for s in st.plan.steps), 1),
        })
    return pd.DataFrame(rows)


def totals(states: list[RunState]) -> RunTotals:
    t = RunTotals(n_requests=len(states))
    for st in states:
        if st.metrics:
            t.tokens_in += st.metrics.tokens_in
            t.tokens_out += st.metrics.tokens_out
            t.llm_seconds += st.metrics.seconds
            t.cost_usd += st.metrics.cost_usd or 0.0
            t.cloud_calls += int(st.metrics.provider == "huggingface" and st.metrics.calls > 0)
        a = getattr(st, "answer", None)
        if a is not None and a.mode == "slm":                       # las respuestas del chat también cuestan tokens
            t.tokens_in += a.tokens_in
            t.tokens_out += a.tokens_out
            t.llm_seconds += a.seconds
            t.cost_usd += a.cost_usd or 0.0
            t.cloud_calls += int(a.provider == "huggingface")
        t.exec_seconds += sum(s.duration_s or 0 for s in st.plan.steps)
        t.steps_ok += sum(s.status == "ok" for s in st.plan.steps)
        t.steps_total += len(st.plan.steps)
    return t
