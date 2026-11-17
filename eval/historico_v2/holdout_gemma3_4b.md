# Evaluación del Planner · 29 casos · 2026-11-15 16:39

## Modo `slm` · modelo `gemma3:4b`

| Métrica | Valor |
|---|---|
| n | 29 |
| exact | 90% |
| precision | 100% |
| recall | 96% |
| f1 | 98% |
| clarify_ok | 83% |
| unsafe | 17% |
| json_ok_1st | 97% |
| retries | 7% |
| rules_fallback | 0% |
| halluc | 3% |
| latency | 8.7 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 1 | 100% | 100% | 100% |
| info | 5 | 100% | 100% | — |
| multi | 2 | 100% | 100% | — |
| out_of_scope | 5 | 80% | 100% | 80% |
| param | 1 | 100% | 100% | — |
| range | 2 | 100% | 100% | — |
| single | 9 | 78% | 88% | — |
| status | 2 | 100% | 100% | — |
| workflow | 2 | 100% | 100% | — |

**Fallos (3)**

- `h05` «quiero cortar el modelo con el sólido objetivo» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `h09` «vuelca la base a csv» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `h28` «cambia la densidad a 2.8 en el modelo» → intent=run tools=['valorizar'] workflow=None params={} (fuente: slm)
