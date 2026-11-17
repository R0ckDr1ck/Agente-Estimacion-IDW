# Agente de Estimación IDW · v4

Agente que entiende una solicitud en lenguaje natural, decide qué herramientas del flujo de estimación IDW ejecutar y en qué
orden, **verifica los prerrequisitos antes de correr nada**, te dice qué falta (y te da el archivo para llenarlo), pide
confirmación para lo destructivo, guarda el estado para **reanudar** y al final **te muestra el reporte con los resultados**.
Desde la v3 también es un **chatbot**: responde tus dudas sobre los procesos y sobre qué pasa al cambiar cada parámetro, con un
**manual de parámetros** como fuente.

Todo se maneja desde una **interfaz web local (Streamlit)**. Las herramientas del flujo ya vienen dentro del agente
(`Proyecto_IDW/Flujo_Estimacion`).

## Instalación

```bat
git clone https://github.com/<tu-usuario>/Agente-Estimacion-IDW.git
cd Agente-Estimacion-IDW
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-flujo.txt     :: interfaz + herramientas de estimación (solo la interfaz: requirements.txt)
```

## Inicio rápido

```bat
run_ui.bat                                :: o:  streamlit run streamlit_app.py
```

`run_ui.bat` usa, en este orden: `python_local.txt` (una línea con la ruta a tu `python.exe`; no se sube a Git), `.venv\Scripts\python.exe`,
la variable `AGENTE_PYTHON` o el `python` del PATH.

## ¿Eres usuario nuevo? Crea tus carpetas de entrada y salida

El agente necesita **dos carpetas**: **entrada** (`01_Data`, tus datos) y **salida** (`02_Resultados`, lo que genera). Si empiezas de cero:

**En la interfaz.** Al abrir, el panel *«🆕 ¿Eres usuario nuevo?»* (ya desplegado si no hay carpetas recordadas) te pide **dónde crear el
proyecto** y **un nombre**, y con *Crear mis carpetas* arma esta estructura y **rellena solas las dos rutas** de la pantalla:

```
Mi_Proyecto/
├── 01_Data/            ENTRADA
│   ├── 01_Database/        collares, survey y ensayos (o un CSV de compósitos)
│   ├── 02_Modelo-Bloques/  modelo de bloques existente (puede quedar vacía si el flujo lo crea)
│   ├── 03_Dominio/         sólidos .obj de dominio
│   ├── 04_Parametros_Entrada/  tablas CSV de parámetros
│   ├── 05_Objetivo/        sólido objetivo (recorte / reporte)
│   ├── 06_Topografia/      topografías (opcional)
│   └── 07_Sondajes_Nuevos/ plan de sondajes
├── 02_Resultados/      SALIDA (la llena el flujo)
│   ├── 01_Database/  03_Modelo_Bloques/  04_Reporte/
└── Plantillas_para_llenar/   (opcional) CSV con la cabecera correcta y filas ficticias
```

Cada subcarpeta trae un `LEEME.txt` (qué archivos van, con qué nombre y para qué ruta de trabajo). Puedes **subir tus primeros archivos desde la
misma pantalla** o copiarlos a mano. Las plantillas se dejan **fuera** de `01_Data` a propósito: el agente reconoce los datos por sus cabeceras y una
plantilla con filas de ejemplo se confundiría con datos reales. Nunca se sobrescribe ni se borra nada (si algo ya existe, se deja y se avisa).

**Por consola** (sin abrir la interfaz):

```bat
run_agente.bat init D:\Proyectos --nombre Mi_Proyecto --plantillas --usar
```

`--plantillas` deja los CSV para llenar y `--usar` hace que la interfaz proponga esas carpetas al abrir. Guía completa: [docs/NUEVO_USUARIO.md](docs/NUEVO_USUARIO.md).

Modelos (barra lateral):
* **☁ Nube · Hugging Face (por defecto).** Lista de 5 modelos pequeños para comparar: Gemma 3 4B (el mismo que tenías en Ollama),
  Qwen3-VL 30B-A3B (familia de tu qwen3-vl), Granite 4.2 3B, Llama 3.1 8B y Phi-4 14B. Necesitas una **API key** de Hugging Face
  (token con permiso *Inference Providers*): escríbela en la barra lateral (solo vive en la memoria de la sesión) o defínela en la
  variable de entorno `HF_TOKEN`. Con **Probar modelos** se hace una llamada mínima a cada uno (como en el notebook de la clase);
  **Ver disponibilidad** consulta el catálogo público sin usar tu clave.
* **💻 Local · Ollama.** gemma3:4b / qwen3-vl:4b-instruct, sin internet.
* Sin clave o sin red el agente **sigue funcionando con reglas por palabras clave** (y te avisa).

Qué sale de tu equipo con la nube: tu frase, el catálogo de herramientas y las fichas del manual de tu duda. **Nunca tus archivos de
datos ni tus rutas.** La clave nunca se escribe en disco, estado ni trazas.

### Lo que ves al abrir

1. **Carpetas de trabajo.** Te pide la carpeta de **entrada** (tu `01_Data`) y la de **salida** (tu `02_Resultados`).
   Al continuar se reubican **TODAS las rutas de TODOS los `conf_*.json`** (no solo las del flujo actual), con respaldo en
   `Configuraciones/_respaldo_agente/`. Antes te muestra cuántas rutas se tocarán, archivo por archivo. Si cambias de carpetas más
   tarde (otra configuración, otro proyecto), vuelve a reubicarlas todas.
2. **Pestañas**

| Pestaña | Para qué sirve |
|---|---|
| 💬 **Solicitud** | Escribe lo que necesitas: ejecutar pasos, verificar, pedir formatos, preguntar si tus datos alcanzan o hacer una duda. Muestra plan, verificación, faltantes con su solución, *Ejecutar* con barra de avance y **el reporte con los resultados** |
| 🗣 **Consulta** | Chatbot: «¿qué pasa si subo la potencia?», «¿cómo elijo el tope?». Responde con el manual, **cita las fichas usadas** y muestra el valor actual de tu proyecto. Admite preguntas de seguimiento |
| 🗂 **Datos** | Mira **todos** tus datos (collar, survey, assay, compósitos, modelo de bloques, sólidos, plan, base de datos y resultados previos), reconociéndolos por sus cabeceras, y dice qué rutas son viables y qué procesos puedes ejecutar hoy (o tras otros pasos) |
| 📄 **Formatos y plantillas** | «¿Cómo debe ser este archivo?»: columnas, primeras líneas de tu ejemplo real y plantillas descargables (.csv/.json) |
| 🛠 **Parámetros** | Carga las **tablas CSV** (sólidos, topes, pasadas, metales…) al conf y edita valores simples, con respaldo |
| 📘 **Manual** | Manual de parámetros: busca, filtra por herramienta y **descarga el `.md`** |
| 📊 **Reporte final** · ⏱ **Métricas** · 🧪 **Evaluación** | Resultados del reporte; tiempo, tokens y **costo** por modelo; comparación de hasta 5 modelos de la nube (o locales) con casos de prueba |
| 🕘 Historial · 🧰 Herramientas | Ejecuciones reanudables; cada herramienta con resumen, ejemplos y sinónimos |

### Ejemplos de solicitudes
* «¿Es viable estimar con mis datos?» · «¿Qué puedo hacer con lo que tengo?» · «¿Qué me falta para estimar?»
* «¿Qué pasa si subo la potencia?» · «¿Cómo se elige el tope de altas leyes?» · «¿Para qué sirve el radio mayor?»
* «Evalúa el plan de sondajes y genera el reporte» · «Estima, valoriza y categoriza»
* «No sé el formato del archivo de collares» · «Corrige las rutas» · «Lista las herramientas»

## ¿Mis datos alcanzan? (viabilidad con todos los datos)

Antes (v2), «¿puedo estimar?» solo revisaba lo que apuntaba el conf actual: con la importación en «compósitos» ni miraba collar,
survey y assay. Ahora el agente examina **todos** los archivos y evalúa **rutas completas**:

* *Estimar desde compósitos ya codificados* (no necesita collar/survey/assay).
* *Estimar desde sondajes* (collar + survey + assay + sólidos).
* *Evaluar un plan de sondajes nuevos*.

Para cada ruta indica ✔/✖, qué falta (con la plantilla para llenarlo) y, si tu configuración apunta a otra ruta, un botón
**«Usar esta ruta en mi configuración»** (cambia solo `origen`, con respaldo; la ruta «evaluar un plan» también lo trae: el origen sale de lo que tus datos permiten, compósitos o sondajes). Si pides ejecutar algo y el conf exige otros archivos que los que hay, la verificación previa lo explica y ofrece el mismo botón («Usar origen «compositos»»). También lista cada paso como *listo*, *posible tras
otros pasos* o *faltan datos*. Se calcula en tu equipo, sin modelo; la base de datos se abre en solo lectura.

## Chatbot y manual de parámetros

`catalog/manual_idw.yaml` documenta cada herramienta y **cada clave de cada `conf_*.json`** (qué significa, qué pasa si se sube o se
baja, recomendación): **270 fichas**. Una prueba exige que ninguna clave de los conf reales quede sin explicar. El chatbot busca las
fichas relevantes y redacta la respuesta **solo con ellas** (si no hay modelo o falla, muestra las fichas tal cual; si el manual no
cubre la duda, lo dice sin consultar al modelo). El manual completo está en [docs/MANUAL_PARAMETROS.md](docs/MANUAL_PARAMETROS.md)
(`run_agente.bat manual` lo regenera).

## Si no sabes cómo llenar un archivo

| Tipo | Ejemplos | Cómo se usa |
|---|---|---|
| **Datos (CSV)** | collares, survey, ensayos, compósitos, modelo de bloques, plan | Descarga la plantilla, llénala y guárdala en la carpeta indicada de tu entrada |
| **Tablas de parámetros (CSV)** | sólidos, topes, variables del modelo, **pasadas IDW**, metales | Descarga la plantilla, llénala y súbela en *Parámetros* → «Aplicar» |
| **Configuración (JSON)** | `conf_comp.json`, `conf_plan.json`… | Plantilla con valores por defecto y bloque `_ayuda`, o la estructura de tu conf actual |

## CLI (opcional)

```bat
run_agente.bat doctor                          :: comprueba venv, raíz, catálogo, API key y modelos
run_agente.bat datos                           :: qué datos hay y qué se puede hacer con ellos
run_agente.bat ask "qué pasa si subo la potencia"
run_agente.bat manual                          :: regenera docs\MANUAL_PARAMETROS.md
run_agente.bat --data-dir D:\datos\01_Data --results-dir D:\res\02_Resultados plan "evalúa el plan de sondajes"
run_agente.bat --provider ollama --model gemma3:4b check
run_agente.bat run --workflow flujo_completo   :: ejecuta (pide confirmar lo destructivo)
```
Opciones globales: `--no-llm`, `--provider huggingface|ollama`, `--model`, `--data-dir`, `--results-dir`, `--root`, `--catalog`, `--quiet`.
Para la nube, define `HF_TOKEN` en el entorno (la CLI nunca recibe la clave como argumento).
Códigos de salida: 0 ok/listo · 1 falló · 2 bloqueado o necesita información · 3 abortado.

## Qué cambió respecto a la v2
* **Reubicación de rutas completa**: todas las rutas de todos los conf siguen a las carpetas elegidas (antes solo las del plan y solo si el archivo no existía, y quedaban «mezcladas»).
* **Viabilidad con todos los datos** (intención `viability`, pestaña *Datos*).
* **Chatbot + manual de parámetros** (intención `ask`, pestañas *Consulta* y *Manual*).
* **Modelos en la nube** (Hugging Face, máx. 5, con API key) además del local; métricas con costo; evaluación comparativa.
* **Catálogo enriquecido**: resumen, sinónimos y 5 ejemplos por herramienta; los ejemplos son una prueba ejecutable.
* Título «Agente de Estimación IDW».

## Seguridad en una línea
El modelo nunca ejecuta nada: solo elige ids del catálogo. El agente ejecuta únicamente `python <script del catálogo> <args del catálogo>`
(sin shell), pide confirmación explícita antes de sobrescribir o modificar datos y solo edita tus `conf_*.json` (rutas, tablas o valores
que tú apruebas) con respaldo previo y verificación posterior. **No pegues tu API key en notebooks ni archivos**: usa `HF_TOKEN` o el campo de la barra lateral.

## Pruebas

```bat
pip install -r requirements-dev.txt
python -m pytest -q
python eval\run_eval.py --mode slm --model google/gemma-3-4b-it --report eval\resultados.md
python eval\run_eval.py --mode slm --provider ollama --model gemma3:4b --cases eval\cases_holdout_v3.yaml
```
Las pruebas no salen a internet (usan transportes simulados) y trabajan sobre copias: verifican que **no tocan** tus `conf_*.json` ni
las carpetas recordadas. Con las carpetas `Prueba_Agente_v1/v2/v3` (si existen) se comprueba la viabilidad en tres escenarios reales.

Documentación: [Arquitectura](docs/ARQUITECTURA.md) · [Manual de parámetros](docs/MANUAL_PARAMETROS.md) · [Recomendaciones](docs/RECOMENDACIONES.md) · [Evaluación](docs/EVALUACION.md)

## Alertas antes de ejecutar (coherencia)

Además de los archivos y las rutas, la verificación previa revisa dos cosas que antes solo fallaban a mitad del flujo:

- **Columnas entre pasos.** Cada paso declara en el catálogo (`columns`) qué columnas del modelo de bloques nombra su conf (ley del metal,
  dominio, categoría anterior, variables de las expresiones de NSR/equivalente…) y cuáles agrega. El agente sigue el modelo paso a paso
  (lee la cabecera del CSV si ya existe, o simula lo que dejará el paso anterior) y avisa, p. ej., «valorizar usa `au_idw` pero la
  estimación solo produce `cu_idw`». Es una **alerta** (`COLUMN_MISSING`): puedes ejecutar igual, marcando que la entiendes.
- **Tablas CSV sin cargar.** Si un CSV de `04_Parametros_Entrada` (metales de valorización, variables del modelo, metales del reporte…)
  difiere del conf, lo avisa con el detalle de lo que cambia y un botón «Cargar … al conf» (con respaldo).

## Si la nube no conecta

En la barra lateral, «Diagnosticar mi clave» pregunta a Hugging Face qué tipo de token es y si tiene el permiso **«Make calls to
Inference Providers»**; «Probar modelos» muestra el error completo de cada modelo. Cuando el modelo falla, la cabecera de cada solicitud
dice por qué se usaron las reglas por palabras clave.

## Flujos «desde sondajes»

- **`estimar_desde_sondajes_recortado`** («realiza la estimación desde sondajes de un modelo recortado»): importar (collar + survey + assay) →
  codificar → acotar → compositar → crear modelo de bloques → recortarlo → estimar IDW → valorizar (opcional) → categorizar → reporte.
  **No** incluye el plan de sondajes.
- **`estimar_desde_sondajes`**: lo mismo, sin el recorte.
- Cada flujo declara en el catálogo los valores de conf que necesita (`conf`: `origen = sondajes`, `crear_modelo_bloques = true`,
  `recortar activo`); si no coinciden, la verificación lo marca como error con un botón «Ajustar … para este flujo» (con respaldo), en vez de
  omitir pasos en silencio.
- Si el reporte pide `Plan_Resumen.json` (`ganancia.activo`) y el flujo no incluye el plan, ofrece desactivar esa hoja.
- **Modelos encadenados**: si el modelo que lee un paso no es el que dejó el paso anterior (p. ej. `recortar` lee otro `mb_smeb.csv` que el
  creado por `modelo`, o `categorizar` espera el valorizado cuando se omitió `valorizar`), lo avisa y ofrece apuntar el conf al correcto.
- En la lexicografía del catálogo, `~` entre palabras clave admite hasta 4 palabras intermedias (`estim* ~ sondaje* ~ recortad*`).


## Valores por defecto de un modelo nuevo

Cuando el modelo de bloques lo crea el propio flujo y no trae columnas que tus conf esperan del modelo anterior, en vez de una alerta por
columna aparece **un solo aviso** con los valores por defecto que se usarían, y un botón «Aplicar valores por defecto del modelo nuevo»
(cambia solo los conf, con respaldo; nada se aplica solo). Se declaran en el catálogo (`fallback` de cada lectura de columna):

| Falta | Valor por defecto |
|---|---|
| `lva_b`, `lva_p`, `lva_d` (bearing/plunge/dip de las pasadas) | ángulo 0 |
| columna de densidad | densidad fija del conf (2.65), `asignar_densidad = true` |
| categoría anterior del piso | se desactiva el piso (`piso.activo = false`) |
| `topof` (variable de minado) | se agrega al modelo nuevo con valor 100 en todos los bloques |
| categoría anterior de la comparación | se desactiva la comparación (`comparacion.activo = false`) |

Los CSV de `04_Parametros_Entrada` no se modifican; si difieren por esto, el aviso lo dice como «esperado».

## El modelo debe llegar hasta el sólido (recorte y reporte)

Antes de correr, si un paso elige bloques con un sólido (`recortar`, o el sólido del `reporte`), el agente compara la **caja** del modelo
con la del sólido (lee solo los vértices del `.obj`; no evalúa punto en sólido). El modelo puede ser el que crea el propio flujo (se toma
el `esquema` de `conf_crear_mb.json`, con su bearing) o el CSV que ya existe (se leen sus centroides). Si no se cruzan, es un **error**
(`MODEL_SOLID_NO_OVERLAP`: ningún bloque caería dentro y el paso fallaría después de haber creado el modelo, que puede tardar mucho); si solo
cubre una parte, una **alerta** (`MODEL_SOLID_PARTIAL`, con el porcentaje del volumen del sólido que cubre).

Cuando el modelo lo crea el flujo hay un botón «Ajustar el esquema del modelo al sólido de recorte» que cambia `origen_*` y `extension_*`
de `conf_crear_mb.json` (con respaldo; la malla conserva su fase, los tamaños de bloque y el bearing). El aviso dice cuántos bloques tendrá el modelo
(un esquema pequeño que pasa a cubrir un sólido grande puede multiplicarlos por decenas: crearlo tardará más). Con un modelo ya existente no se
ofrece botón: habría que usar otro modelo u otro sólido. Se declara en el catálogo (`columns.grid` y `columns.solid`).

## Modelos: una sola lista (nube y descargados)

La barra lateral muestra **una lista** con los 5 modelos de Hugging Face (☁) y los modelos locales (💻): los del conf (`llm.models`) más
**todos los que tengas descargados en Ollama** (se consultan en vivo; los de embeddings se omiten y «llama3» = «llama3:latest»).
Elegir uno cambia el proveedor: con uno de la nube aparece el campo de la API key; con uno local se indica si está descargado.

## Informe y presentación del proyecto

En `docs/`: `Informe_Agente_Estimacion_IDW.docx` (informe técnico: qué hace cada herramienta de `Proyecto_IDW`, cómo funciona el agente,
evaluación, casos reales, limitaciones) y `Presentacion_Agente_Estimacion_IDW.pptx` (35 láminas con notas del orador). Antes de presentar,
completa el campo «[nombre del estudiante]» de la portada de ambos.


## Novedades de la v4

* **Usuario nuevo**: panel *«¿Eres usuario nuevo?»* y comando `init` que crean las carpetas de entrada y salida (ver arriba), con `LEEME.txt` por subcarpeta,
  plantillas opcionales y subida de archivos. Código en `agente/nuevo_usuario.py`.
* **Repositorio portable**: sin rutas de un equipo concreto (los conf traen una raíz neutra que la interfaz reubica al continuar; el intérprete de Python se
  detecta solo), `requirements*.txt` separados y `streamlit_app.py` como punto de entrada para Streamlit.
* Los datos y resultados de ejemplo (`Proyecto_IDW/01_Data`, `02_Resultados`) **no** se suben al repositorio.

## Publicar la interfaz en Streamlit Community Cloud

1. Sube el repositorio a GitHub (privado o público).
2. En <https://share.streamlit.io> → *Create app* → elige el repositorio, la rama `main` y el archivo principal `streamlit_app.py`.
3. (Opcional) en *Advanced settings → Secrets* agrega `HF_TOKEN = "hf_..."` para que los modelos de la nube funcionen sin escribir la clave en la barra
   lateral. **Nunca** la guardes en un archivo del repositorio.

Qué esperar de la versión en la nube: el servidor **no ve tus archivos locales**, así que ahí el flujo es: crear las carpetas en el servidor →
subir tus archivos desde la pantalla inicial → consultar el manual, verificar, evaluar viabilidad y ver plantillas. Los modelos locales (Ollama) no están
disponibles y el servidor gratuito es pequeño: las estimaciones grandes (modelos de millones de bloques) conviene correrlas **en tu equipo** con `run_ui.bat`.
Además `requirements.txt` solo instala la interfaz; para ejecutar herramientas hay que usar `requirements-flujo.txt` (en la nube no se instala por defecto).
