# Evaluación del Planner · 43 casos · 2026-11-15 16:33

## Modo `slm` · modelo `gemma3:4b`

| Métrica | Valor |
|---|---|
| n | 43 |
| exact | 98% |
| precision | 86% |
| recall | 100% |
| f1 | 93% |
| clarify_ok | 100% |
| unsafe | 0% |
| json_ok_1st | 93% |
| retries | 7% |
| rules_fallback | 0% |
| halluc | 7% |
| latency | 8.9 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 2 | 100% | 100% | 100% |
| ambiguous | 2 | 100% | 100% | 100% |
| info | 8 | 100% | 100% | — |
| multi | 3 | 100% | 100% | — |
| out_of_scope | 3 | 100% | 100% | 100% |
| param | 2 | 100% | 100% | — |
| range | 3 | 100% | 100% | — |
| single | 14 | 100% | 100% | — |
| status | 3 | 67% | 27% | — |
| workflow | 3 | 100% | 100% | — |

**Fallos (1)**

- `v03` «revisa los prerrequisitos del reporte» → intent=status tools=[] workflow=evaluar_plan_sondajes params={} (fuente: slm)
