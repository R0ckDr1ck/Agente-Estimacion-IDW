# Evaluación del Planner · 29 casos · 2026-11-15 16:49

## Modo `slm` · modelo `qwen3-vl:4b-instruct`

| Métrica | Valor |
|---|---|
| n | 29 |
| exact | 83% |
| precision | 82% |
| recall | 96% |
| f1 | 88% |
| clarify_ok | 83% |
| unsafe | 17% |
| json_ok_1st | 100% |
| retries | 0% |
| rules_fallback | 0% |
| halluc | 0% |
| latency | 7.9 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 1 | 100% | 100% | 100% |
| info | 5 | 100% | 100% | — |
| multi | 2 | 100% | 100% | — |
| out_of_scope | 5 | 80% | 100% | 80% |
| param | 1 | 0% | 100% | — |
| range | 2 | 100% | 100% | — |
| single | 9 | 67% | 52% | — |
| status | 2 | 100% | 100% | — |
| workflow | 2 | 100% | 100% | — |

**Fallos (5)**

- `h01` «necesito el informe en excel de los recursos» → intent=run tools=[] workflow=flujo_completo params={} (fuente: slm)
- `h05` «quiero cortar el modelo con el sólido objetivo» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `h09` «vuelca la base a csv» → intent=run tools=[] workflow=None params={} (fuente: slm)
- `h22` «calcula el esquema de bloques de "D:/datos/modelo_x.csv"» → intent=run tools=['esquema_bloques'] workflow=None params={} (fuente: slm)
- `h28` «cambia la densidad a 2.8 en el modelo» → intent=run tools=['valorizar'] workflow=None params={} (fuente: slm)
