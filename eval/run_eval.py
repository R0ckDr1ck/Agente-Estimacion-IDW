"""Evaluación del Router/Planner.

    python eval/run_eval.py --mode rules                 # baseline determinista (sin modelo)
    python eval/run_eval.py --mode slm --model gemma3:4b --provider ollama           # SLM local
    python eval/run_eval.py --mode slm --model google/gemma-3-4b-it                  # SLM en Hugging Face (usa HF_TOKEN)
    python eval/run_eval.py --mode both --report eval/resultados.md

Métricas (por modo y por categoría):
  exact        intención + selección (workflow/rango expandidos) + parámetros exactamente como se esperaba
  precision/recall/F1   sobre el CONJUNTO de herramientas implicadas (expandidas), micro-promedio
  clarify_ok   en casos ambiguos/fuera de alcance/adversariales: pidió aclaración y NO eligió nada
  json_ok_1st  % de casos en que el SLM entregó un JSON válido al primer intento
  retries      reintentos medios por caso   |   rules_fallback  % de casos que terminaron en reglas
  halluc       % de casos en que el SLM propuso ids inexistentes en algún intento (alucinación de tools)
  unsafe       % de casos 'clarify' en que se eligió alguna herramienta (el error más grave)
  latency      segundos medios por solicitud
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from agente.catalog import ToolRegistry                 # noqa: E402
from agente.config import AgentConfig                   # noqa: E402
from agente.evaluation import (evaluate_case as _evaluate, load_cases, make_planner, summarize as _summarize)  # noqa: E402


def evaluate_case(planner, case: dict, mode: str = "") -> dict:
    r = _evaluate(planner, case)
    r["pass"] = r["passed"]                       # nombre histórico usado por los informes de este script
    return r


def summarize(rows: list[dict]) -> dict:
    return _summarize(rows)


def fmt(v) -> str:
    return "—" if v is None else (f"{v:.0%}" if isinstance(v, float) and v <= 1.0 else f"{v:.2f}" if isinstance(v, float) else str(v))


def render(mode: str, model: str, summ: dict, rows: list[dict], arbitrated: bool = True) -> str:
    g = summ["global"]
    L = [f"## Modo `{mode}`" + (f" · modelo `{model}`" + ("" if arbitrated else " · SIN arbitraje (crudo)") if mode == "slm" else ""), "",
         "| Métrica | Valor |", "|---|---|"]
    for k in ("n", "exact", "precision", "recall", "f1", "clarify_ok", "unsafe", "json_ok_1st", "retries",
              "rules_fallback", "halluc", "latency"):
        L.append(f"| {k} | {fmt(g[k]) if k != 'latency' else f'{g[k]:.1f} s'} |")
    L += ["", "| Categoría | n | exact | F1 | clarify_ok |", "|---|---|---|---|---|"]
    for c, a in summ["by_category"].items():
        L.append(f"| {c} | {a['n']} | {fmt(a['exact'])} | {fmt(a['f1'])} | {fmt(a['clarify_ok'])} |")
    bad = [r for r in rows if not r["pass"]]
    if bad:
        L += ["", f"**Fallos ({len(bad)})**", ""]
        for r in bad:
            L.append(f"- `{r['id']}` «{r['text']}» → intent={r['intent']} tools={r['tools']} workflow={r['workflow']} "
                     f"params={r['params']} (fuente: {r['source']})")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["rules", "slm", "both"], default="rules")
    ap.add_argument("--cases", type=Path, default=BASE / "eval" / "cases_idw.yaml")
    ap.add_argument("--catalog", type=Path, default=BASE / "catalog" / "tools_idw.yaml")
    ap.add_argument("--model", help="modelo (id de Hugging Face o nombre de Ollama; por defecto el de agent.yaml)")
    ap.add_argument("--provider", choices=["huggingface", "ollama"], help="por defecto el de agent.yaml")
    ap.add_argument("--pause", type=float, default=0.0, help="segundos entre casos (límites de solicitudes de la nube)")
    ap.add_argument("--raw", action="store_true", help="desactiva el arbitraje con reglas (mide el SLM 'crudo')")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--min-exact", type=float, default=0.0, help="código de salida 1 si exact queda por debajo")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    cases = load_cases(args.cases, args.limit)
    reg = ToolRegistry.load([args.catalog])
    modes = ["rules", "slm"] if args.mode == "both" else [args.mode]
    md, worst = [f"# Evaluación del Planner · {len(cases)} casos · {time.strftime('%Y-%m-%d %H:%M')}", ""], 1.0
    for mode in modes:
        cfg = AgentConfig.load(catalogs=[str(args.catalog)])
        planner = make_planner(cfg, reg, "rules" if mode == "rules" else ("raw" if args.raw else "agent"), args.model, args.provider)
        prov = args.provider or cfg.llm.provider
        shown = (args.model or (cfg.llm.cloud.model if prov == "huggingface" else cfg.llm.primary.model))
        if mode == "slm" and planner.primary is None:
            print(f"✘ No hay modelo disponible: {planner.client_error or 'LLM desactivado'}")
            return 2
        rows = []
        for i, c in enumerate(cases):
            if args.pause and i:
                time.sleep(args.pause)
            r = evaluate_case(planner, c, mode)
            rows.append(r)
            print(f"[{mode}] {'OK ' if r['pass'] else 'XX '} {c['id']:5s} {r['latency']:5.1f}s  {c['text'][:60]}", flush=True)
        summ = summarize(rows)
        worst = min(worst, summ["global"]["exact"])
        md.append(render(mode, f"{shown} ({prov})", summ, rows, arbitrated=not args.raw))
        print("\n" + md[-1])
    if args.report:
        args.report.write_text("\n".join(md), encoding="utf-8")
        print(f"Informe escrito en {args.report}")
    return 0 if worst >= args.min_exact else 1


if __name__ == "__main__":
    sys.exit(main())
