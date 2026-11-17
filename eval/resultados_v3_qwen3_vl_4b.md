# Evaluación del Planner · 54 casos · 2026-11-16 13:00

## Modo `slm` · modelo `qwen3-vl:4b-instruct (ollama)`

| Métrica | Valor |
|---|---|
| n | 54 |
| exact | 93% |
| precision | 87% |
| recall | 100% |
| f1 | 93% |
| clarify_ok | 100% |
| unsafe | 0% |
| json_ok_1st | 100% |
| retries | 0% |
| rules_fallback | 0% |
| halluc | 0% |
| latency | 7.8 s |

| Categoría | n | exact | F1 | clarify_ok |
|---|---|---|---|---|
| adversarial | 2 | 100% | 100% | 100% |
| ambiguous | 2 | 100% | 100% | 100% |
| ask | 6 | 83% | 100% | — |
| info | 8 | 100% | 100% | — |
| multi | 3 | 100% | 100% | — |
| out_of_scope | 3 | 100% | 100% | 100% |
| param | 2 | 0% | 100% | — |
| range | 3 | 100% | 100% | — |
| single | 14 | 93% | 72% | — |
| status | 3 | 100% | 100% | — |
| viability | 5 | 100% | 100% | — |
| workflow | 3 | 100% | 100% | — |

**Fallos (4)**

- `s13` «dame el excel con el tonelaje y la ley» → intent=run tools=[] workflow=flujo_completo params={} (fuente: slm)
- `p01` «identifica el esquema de la grilla de "D:/Proyecto_SLM/Proyecto_IDW/01_Data/02_Modelo-Bloques/mb_smeb.csv"» → intent=run tools=['esquema_bloques'] workflow=None params={} (fuente: slm)
- `p02` «identifica el esquema del modelo de bloques» → intent=explain tools=['esquema_bloques'] workflow=None params={} (fuente: slm)
- `q03` «para qué sirve el radio mayor» → intent=explain tools=[] workflow=None params={} (fuente: slm)
