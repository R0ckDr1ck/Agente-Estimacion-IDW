# Recomendaciones de uso: SLM vs. LLM, contexto y alucinaciones

## 1. Qué hace el modelo y qué no (v3: SLM en la nube o local)

El agente está diseñado para que el 95 % del trabajo no necesite razonamiento del modelo; el resto lo cubren las reglas del catálogo.

| Tarea | Quién la hace | Por qué |
|---|---|---|
| Entender la frase y elegir herramientas/workflow/rango/intención | **SLM** (nube o local) | Clasificación con salida cerrada (enum de ids); un modelo de 3 a 14 B alcanza si el catálogo es corto y hay few-shot |
| Corregir al modelo donde falla (intención, workflow, anclaje léxico) | **Código** (`Planner._arbitrate`) | Determinista y medible; bajó las acciones inseguras de 43 % a ~0 % |
| Ordenar pasos, inferir prerrequisitos, verificar archivos, armar comandos | **Código** | Cero tolerancia a error |
| **¿Mis datos alcanzan? ¿Qué puedo ejecutar?** | **Código** (`datos.py`) | Se calcula mirando los archivos reales; un modelo inventaría columnas |
| **Responder dudas de parámetros** | Código recupera fichas del manual · **modelo las redacta** | El modelo no «sabe» tu flujo: solo explica lo que dice el manual y lo cita |
| Explicar el resultado y mostrar el reporte | Código (plantilla) | Un informe no debe poder inventar hechos |

### Nube (Hugging Face) o local (Ollama)

| | Nube · Hugging Face | Local · Ollama |
|---|---|---|
| Modelos | 5 en `config/agent.yaml` (Gemma 3 4B, Qwen3-VL 30B-A3B, Granite 4.2 3B, Llama 3.1 8B, Phi-4 14B); cambian según lo que sirvan los proveedores: usa **Probar modelos** | gemma3:4b, qwen3-vl:4b-instruct |
| Latencia | ~1–3 s por llamada (red); sin carga inicial | ~7 s con GPU compartida (1.ª llamada ~1 min) |
| Costo | Centavos: ~2 300 tokens de entrada por solicitud ≈ US$ 0,0001 con los precios de referencia | US$ 0 |
| Privacidad | Salen la frase, el catálogo y las fichas del manual; nunca datos ni rutas | No sale nada |
| Disponibilidad | Necesita internet, API key y puede haber límites (429) | Funciona offline |
| Si falla | Reglas por palabras clave (se avisa) | Reglas por palabras clave |

Cuándo elegir cada una: **nube** para comparar muchos modelos sin instalar nada y para el chatbot (la redacción mejora con modelos
algo mayores); **local** cuando los datos o las frases sean sensibles o no haya red. **No uses la variante «thinking» de Qwen**
(`qwen3-vl:4b`, o modelos de razonamiento en la nube): consumen el límite de tokens pensando y devuelven vacío; el cliente lo detecta.

**API key.** Crea un token en huggingface.co/settings/tokens (permiso *Inference Providers*), úsalo por `HF_TOKEN` o en el campo de
la barra lateral. Nunca lo pegues en un notebook, un `.yaml` o un chat: si se filtra, revócalo y crea otro.

**Costos y límites.** Los precios del YAML son de referencia (catálogo público del router, nov. 2026) y varían por proveedor; el
plan gratuito tiene crédito mensual limitado. La pestaña *Evaluación* tiene «Pausa entre casos» para respetar los límites.

### ¿Calibrar o hacer fine-tuning con un manual de Proyecto_IDW?

Un manual **en prosa no es el material adecuado** para entrenar al Router: este solo convierte una frase en un id de herramienta; no
necesita entender geoestadística. Lo que sí funciona, de menor a mayor esfuerzo (lo ya hecho en la v3 va marcado):

1. ✔ **Enriquecer el catálogo**: resumen, sinónimos y ejemplos por herramienta (los ejemplos son una prueba ejecutable).
2. ✔ **Usar el manual como fuente de consulta** (chatbot con recuperación): ahí está el valor de un manual.
3. **Elegir los ejemplos del prompt según la frase** (few-shot dinámico con los casos de `eval/`): mejora paráfrasis sin entrenar.
4. **Calibrar el umbral de confianza** con los resultados de la pestaña *Evaluación* (el campo `confidence` del modelo no es fiable tal cual).
5. **LoRA** con 500–1 000 pares *frase → JSON* generados del catálogo, con paráfrasis, casos fuera de alcance y ambiguos. Ventaja: el
   prompt bajaría de ~2 200 a ~200 tokens. Riesgos: hay que escribir un conjunto reservado **nuevo y a mano** (el actual ya se conoce),
   entrenar en GPU de 8 GB es posible con QLoRA pero engorroso en Windows (Colab/WSL), y el arbitraje debe mantenerse.

## 2. Límites de contexto

| Concepto | Valor actual | Qué pasa al crecer |
|---|---|---|
| `num_ctx` | 4096 tokens | Suficiente: el prompt del planner mide ~5,9 mil caracteres ≈ 2 k tokens (estimado) con 14 herramientas |
| Catálogo en el prompt | 1 línea por herramienta (~35 tokens) | Con ≤ 40 herramientas cabe sin problema |
| Few-shot | 6 ejemplos autogenerados desde `examples:` | Se reparten a lo largo del flujo; cada ejemplo ~90 tokens |
| Salida | `num_predict: 400` | El JSON mide ~120 tokens |

Si el catálogo supera ~40 herramientas:
1. **Enrutamiento en dos etapas**: primero categoría (flujo / modelo de bloques / reportes…), luego herramienta dentro de la categoría.
2. **Recuperación**: pre-seleccionar las ~8 herramientas más parecidas (BM25/embeddings sobre `description + keywords + examples`) y poner solo esas en el prompt. El campo `category` y `keywords` ya existen para esto.
3. Nunca pegar `conf_*.json` completos en el prompt (`conf_estimacion.json` pesa 112 KB ≈ 30 k tokens): el modelo no los necesita, los lee el código.

## 3. Manejo de alucinaciones en la selección de herramientas

Defensas implementadas (en orden de ejecución):

1. **Espacio de salida cerrado**: el prompt lista los ids; `PlannerOutput` + `validate_against_catalog` rechazan cualquier id, workflow o rango inexistente.
2. **Reintento con el error a la vista** (hasta 2): se le devuelve al modelo *por qué* falló y los ids más parecidos («`estimacion_idw` NO existe, ¿quisiste decir `estimar`?»).
3. **Parámetros solo declarados**: los que el modelo invente se descartan y se avisa; los valores se validan por tipo (`path` debe quedar dentro del proyecto).
4. **El modelo no decide lo peligroso**: no elige orden, rutas, comandos ni confirma nada. Con 100 % de alucinación, lo peor que puede pasar es *proponer* el plan equivocado, que se muestra completo antes de ejecutar (dry-run por defecto) y cuyos pasos destructivos requieren confirmación.
5. **Arbitraje con el léxico del catálogo** (`llm.arbitrate_with_rules`): las reglas por palabras clave corren siempre y (a) fijan la intención explícita, el workflow y el rango; (b) **exigen respaldo léxico** para cada herramienta que el modelo elige (si no lo hay, se descarta); (c) rescatan la selección cuando el modelo no eligió nada utilizable. Con gemma3:4b esto llevó `unsafe` de 43 % a 0 % (ver `docs/EVALUACION.md`). Si el modelo y las reglas aún implican conjuntos incompatibles, se avisa en el informe.
6. **Abstención**: «hazlo», fuera de alcance o adversarial → `needs_clarification`, nunca una herramienta «por defecto». Se mide con la métrica `unsafe` (debe ser 0 %).
7. **Fallback explícito, nunca silencioso**: si se cae a reglas, el informe lo dice en la cabecera.
8. **Reporter sin libertad**: el informe es una plantilla; el resumen opcional del SLM solo puede recomendar acciones que existan en `acciones_permitidas`.
9. **Temperatura 0** y salida acotada (`num_predict`).

Qué vigilar en producción:
- `trace.jsonl` guarda, por solicitud, la fuente (`slm | rules`) y las métricas (tokens, tiempos), los intentos y los errores de validación: una subida de `rules` o de reintentos indica que cambió el catálogo o el modelo.
- **Mantén vivo el léxico**: como cada herramienta elegida necesita respaldo léxico, una paráfrasis sin ninguna palabra clave del catálogo se rechaza con una pregunta (falla segura, no ejecuta nada). Si en `trace.jsonl` ves «se descartó lo que la frase no respalda» con una petición legítima, añade esa palabra a `keywords` de la herramienta.
- Cada vez que añadas una herramienta con un nombre parecido a otra (`estimar` / `estimar_d3h`), añade 2-3 casos a `eval/cases_idw.yaml` y reevalúa: las confusiones aparecen primero entre nombres cercanos.
- Evita ids que sean palabras comunes del dominio (`modelo`, `plan`): el SLM y las reglas los activan por accidente. Ya existe `modelo`; está cubierto con palabras clave específicas («crear modelo de bloques»), pero conviene un id más largo si se reestructura el catálogo.

## 4. Operación

- **Siempre** revisa el plan en modo simulación antes de ejecutar flujos largos (`estimar` puede tardar horas; `runner.timeout_s` = 6 h).
- `--yes` concede *todas* las confirmaciones (incluida la sobrescritura de `Database.db`). Úsalo solo en ejecuciones desatendidas que ya revisaste.
- Tras actualizar el catálogo o el modelo, corre `pytest` y `eval/run_eval.py --mode both`.
- El log crudo de cada paso queda en `runs/<id>/NNN_<herramienta>.log`; el de `EjecutarFlujo.py` clásico (`02_Resultados/Flujo_Log_*.txt`) **no** se genera cuando el agente corre los pasos por separado: la traza del agente lo reemplaza.
