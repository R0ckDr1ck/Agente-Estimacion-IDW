"""Evaluación del Router/Planner (la misma lógica que usa `eval/run_eval.py`, ahora reutilizable desde la interfaz).

Mide, sobre casos escritos a mano (`eval/cases_*.yaml`), si el agente entiende bien la solicitud:
  exact        intención + selección (workflow/rango expandidos) + parámetros exactamente como se esperaba
  precision/recall/F1   sobre el CONJUNTO de herramientas implicadas
  unsafe       % de casos ambiguos/fuera de alcance/adversariales en que se eligió alguna herramienta (el error más grave)
  clarify_ok   en esos mismos casos, pidió aclaración
  json_ok_1st  % de casos en que el SLM entregó un JSON válido al primer intento
  retries      reintentos medios por caso
  halluc       % de casos en que el SLM propuso ids inexistentes en algún intento
  tokens/latencia/costo  lo que cuesta cada solicitud (medido por el proveedor: Hugging Face u Ollama)

Tres configuraciones, como la «escalera de intervención» de la clase (de menos a más ayuda al modelo):
  rules   solo palabras clave (sin modelo)                  → piso determinista
  raw     SLM tal cual (sin arbitraje)                      → lo que el modelo hace solo
  agent   SLM + arbitraje con el léxico del catálogo        → el agente completo
"""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

import yaml

from .catalog import ToolRegistry
from .config import AgentConfig
from .models import PlannerOutput
from .planner import Planner

MODES = {
    "rules": "Solo reglas (sin modelo)",
    "raw": "SLM crudo (sin arbitraje)",
    "agent": "SLM + arbitraje (agente)",
}
CLARIFY_CATEGORIES = ("ambiguous", "out_of_scope", "adversarial")


def load_cases(path: Path, limit: Optional[int] = None) -> list[dict]:
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8"))["cases"])[:limit]


def make_planner(cfg: AgentConfig, reg: ToolRegistry, mode: str, model: Optional[str] = None, provider: Optional[str] = None) -> Planner:
    """Planner configurado para una de las tres configuraciones (copia la config: no toca la del agente).
    `provider`: huggingface (nube; `model` es un id de Hugging Face) u ollama (local); por defecto el configurado."""
    c = cfg.model_copy(deep=True)
    c.llm.enabled = mode != "rules"
    c.llm.arbitrate_with_rules = mode == "agent"
    if provider:
        c.llm.provider = provider                        # type: ignore[assignment]
    if model:
        if c.llm.provider == "huggingface":
            c.llm.cloud.model = model
        else:
            c.llm.primary.model = model
    return Planner(c, reg)


def evaluate_case(planner: Planner, case: dict) -> dict:
    exp = case["expect"]
    t0 = time.perf_counter()
    res = planner.plan(case["text"])
    dt = time.perf_counter() - t0
    out, m = res.output, res.metrics
    expand = planner._expand
    row = {"id": case["id"], "category": case["category"], "text": case["text"], "source": res.source, "latency": dt,
           "tools": out.tools, "intent": out.intent, "workflow": out.workflow, "params": out.params,
           "clarify": bool(out.needs_clarification) or out.intent == "unknown",
           "attempts": len(res.attempts), "json_first": bool(res.attempts and res.attempts[0].get("ok")),
           "retries": m.retries if res.source != "rules" else 0,
           "halluc": any("NO existe" in str(a.get("error", "")) for a in res.attempts),
           "tokens_in": m.tokens_in, "tokens_out": m.tokens_out, "cost_usd": m.cost_usd or 0.0, "warnings": res.warnings}

    if exp.get("clarify"):
        picked = bool(expand(out))
        row.update(passed=row["clarify"] and not picked, unsafe=picked, tp=0, fp=0, fn=0)
        return row

    want = PlannerOutput(intent=exp.get("intent", "run"), tools=exp.get("tools", []), workflow=exp.get("workflow"),
                         from_tool=exp.get("from_tool"), to_tool=exp.get("to_tool"))
    ok_intent = out.intent == want.intent
    if exp.get("check_selection", True):
        a, b = expand(out), expand(want)
        ok_sel = a == b
        row.update(tp=len(a & b), fp=len(a - b), fn=len(b - a))
    else:
        ok_sel = True
        row.update(tp=0, fp=0, fn=0)
    ok_params = True
    if "params" in exp:
        got = {k: str(v).replace("\\", "/") for k, v in out.params.items()}
        ok_params = got == {k: str(v).replace("\\", "/") for k, v in exp["params"].items()}
    row.update(passed=ok_intent and ok_sel and ok_params, unsafe=False)
    return row


def summarize(rows: list[dict]) -> dict:
    def agg(rs: list[dict]) -> dict:
        n = len(rs) or 1
        tp, fp, fn = (sum(r[k] for r in rs) for k in ("tp", "fp", "fn"))
        p = tp / (tp + fp) if tp + fp else 1.0
        r_ = tp / (tp + fn) if tp + fn else 1.0
        clar = [r for r in rs if r["category"] in CLARIFY_CATEGORIES]
        slm = [r for r in rs if r["attempts"]]
        return {
            "n": len(rs), "exact": sum(r["passed"] for r in rs) / n,
            "precision": p, "recall": r_, "f1": 2 * p * r_ / (p + r_) if p + r_ else 0.0,
            "clarify_ok": (sum(r["passed"] for r in clar) / len(clar)) if clar else None,
            "unsafe": (sum(r["unsafe"] for r in clar) / len(clar)) if clar else None,
            "json_ok_1st": (sum(r["json_first"] for r in slm) / len(slm)) if slm else None,
            "retries": (sum(r["retries"] for r in slm) / len(slm)) if slm else None,
            "rules_fallback": (sum(r["source"] == "rules" for r in slm) / len(slm)) if slm else None,
            "halluc": (sum(r["halluc"] for r in slm) / len(slm)) if slm else None,
            "latency": sum(r["latency"] for r in rs) / n,
            "tokens_in": sum(r["tokens_in"] for r in rs) / n, "tokens_out": sum(r["tokens_out"] for r in rs) / n,
            "cost_usd": sum(r.get("cost_usd", 0.0) for r in rs),
        }
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    return {"global": agg(rows), "by_category": {k: agg(v) for k, v in sorted(by_cat.items())}}


def run_cases(planner: Planner, cases: list[dict], on_progress: Optional[Callable[[int, int, dict], None]] = None,
              pause_s: float = 0.0) -> list[dict]:
    """Evalúa los casos uno por uno (secuencial: así la latencia de cada llamada es la real). `pause_s` espacia las llamadas
    para respetar los límites de solicitudes de un proveedor en la nube."""
    rows = []
    for i, c in enumerate(cases, 1):
        if pause_s and i > 1:
            time.sleep(pause_s)
        r = evaluate_case(planner, c)
        rows.append(r)
        if on_progress:
            on_progress(i, len(cases), r)
    return rows
