# Evaluación del Planner · 32 casos · 2026-11-16 13:07

## Modo `slm` · modelo `qwen3-vl:4b-instruct (ollama)`

| Métrica | Valor |
|---|---|
| n | 32 |
| exact | 84% |
| precision | 78% |
| recall | 90% |
| f1 | 84% |
| clarify_ok | 100% |
| unsafe | 0% |
| json_ok_1st | 100% |
| retries | 0% |
| rules_fallback | 0% |
| halluc | 0% |
| latency | 7.7 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 2 | 100% | 100% | 100% |
| ambiguous | 1 | 100% | 100% | 100% |
| ask | 5 | 100% | 100% | — |
| info | 3 | 100% | 100% | — |
| multi | 1 | 100% | 100% | — |
| out_of_scope | 2 | 100% | 100% | 100% |
| range | 1 | 100% | 100% | — |
| single | 10 | 50% | 44% | — |
| status | 1 | 100% | 100% | — |
| viability | 4 | 100% | 100% | — |
| workflow | 2 | 100% | 100% | — |

**Fallos (5)**

- `n01` «necesito pasar mis sondajes a la base de datos» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n02` «quiero que cada tramo sepa en qué zona geológica está» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n03` «limita las leyes muy altas dominio por dominio» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n06` «dame el excel final con los recursos» → intent=run tools=[] workflow=flujo_completo params={} (fuente: slm)
- `n09` «genera la grilla desde los sólidos» → intent=run tools=[] workflow=None params={} (fuente: slm)
