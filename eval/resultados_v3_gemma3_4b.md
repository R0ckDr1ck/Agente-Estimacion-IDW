# Evaluación del Planner · 54 casos · 2026-11-16 12:47

## Modo `slm` · modelo `gemma3:4b (ollama)`

| Métrica | Valor |
|---|---|
| n | 54 |
| exact | 96% |
| precision | 100% |
| recall | 99% |
| f1 | 99% |
| clarify_ok | 100% |
| unsafe | 0% |
| json_ok_1st | 93% |
| retries | 7% |
| rules_fallback | 0% |
| halluc | 7% |
| latency | 8.3 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 2 | 100% | 100% | 100% |
| ambiguous | 2 | 100% | 100% | 100% |
| ask | 6 | 83% | 100% | — |
| info | 8 | 100% | 100% | — |
| multi | 3 | 100% | 100% | — |
| out_of_scope | 3 | 100% | 100% | 100% |
| param | 2 | 100% | 100% | — |
| range | 3 | 100% | 100% | — |
| single | 14 | 100% | 100% | — |
| status | 3 | 100% | 100% | — |
| viability | 5 | 80% | 80% | — |
| workflow | 3 | 100% | 100% | — |

**Fallos (2)**

- `v07` «qué me falta para poder estimar» → intent=viability tools=[] workflow=None params={} (fuente: slm)
- `q03` «para qué sirve el radio mayor» → intent=explain tools=[] workflow=None params={} (fuente: slm)
