# Evaluación del Planner · 32 casos · 2026-11-16 12:55

## Modo `slm` · modelo `gemma3:4b (ollama)`

| Métrica | Valor |
|---|---|
| n | 32 |
| exact | 84% |
| precision | 100% |
| recall | 90% |
| f1 | 95% |
| clarify_ok | 80% |
| unsafe | 20% |
| json_ok_1st | 88% |
| retries | 12% |
| rules_fallback | 0% |
| halluc | 12% |
| latency | 9.3 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 2 | 50% | 100% | 50% |
| ambiguous | 1 | 100% | 100% | 100% |
| ask | 5 | 100% | 100% | — |
| info | 3 | 100% | 100% | — |
| multi | 1 | 100% | 100% | — |
| out_of_scope | 2 | 100% | 100% | 100% |
| range | 1 | 100% | 100% | — |
| single | 10 | 60% | 75% | — |
| status | 1 | 100% | 100% | — |
| viability | 4 | 100% | 100% | — |
| workflow | 2 | 100% | 100% | — |

**Fallos (5)**

- `n01` «necesito pasar mis sondajes a la base de datos» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n02` «quiero que cada tramo sepa en qué zona geológica está» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n03` «limita las leyes muy altas dominio por dominio» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n09` «genera la grilla desde los sólidos» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `n31` «envía el reporte por correo a mi jefe» → intent=unknown tools=[] workflow=None params={} (fuente: slm)
