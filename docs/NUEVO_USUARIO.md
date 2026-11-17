# Guía para usuarios nuevos: carpetas de entrada y de salida

El agente trabaja con **dos carpetas** que tú eliges al abrir la interfaz:

| Carpeta | Nombre habitual | Qué contiene |
|---|---|---|
| **Entrada** | `01_Data` | Tus datos: sondajes, modelo de bloques, sólidos `.obj`, tablas de parámetros |
| **Salida** | `02_Resultados` | Lo que genera el flujo: base de datos, modelos estimados, reporte |

Deben ser **carpetas distintas** y la de salida debe ser escribible. Los nombres `01_Data` y `02_Resultados` son los que usan los
`conf_*.json` como «ancla» para reubicar sus rutas, así que conviene respetarlos (dentro de la carpeta de tu proyecto).

## Opción A · Desde la interfaz (recomendada)

1. Abre la interfaz: `run_ui.bat` (o `streamlit run streamlit_app.py`).
2. En la pantalla inicial despliega **«🆕 ¿Eres usuario nuevo? Crea tus carpetas de entrada y de salida»** (ya viene desplegado si no hay carpetas recordadas).
3. Escribe **dónde crear el proyecto** (o pulsa *Examinar*) y el **nombre del proyecto**. Deja marcado *Incluir plantillas CSV* si quieres los
   archivos de ejemplo con la cabecera correcta.
4. Pulsa **Crear mis carpetas**. Aparece el árbol creado y las rutas de *Carpeta de ENTRADA* y *Carpeta de SALIDA* **se llenan solas**.
5. (Opcional) **Sube tus primeros archivos**: elige la subcarpeta de destino y arrastra tus `.csv`, `.obj`, `.json`, `.xlsx` o `.db`.
   Un archivo que ya existe nunca se sobrescribe.
6. Pulsa **Continuar →**. El agente te muestra cuántas rutas de los `conf_*.json` va a reubicar (con respaldo) y abre el espacio de trabajo.

## Opción B · Por consola

```bat
run_agente.bat init D:\Proyectos --nombre Mi_Proyecto --plantillas --usar
```

| Opción | Efecto |
|---|---|
| `destino` | Ruta completa de la carpeta donde se crea el proyecto (se crea si no existe) |
| `--nombre` | Nombre de la carpeta del proyecto (por defecto `Mi_Proyecto`) |
| `--plantillas` | Crea `Plantillas_para_llenar/` con un CSV por tipo de archivo y por tabla de parámetros |
| `--usar` | Recuerda las carpetas: la interfaz las propone al abrir |

Código de salida 0 si todo salió bien, 1 si el nombre no es válido o no se pudo crear.

## Opción C · A mano

Crea la estructura de abajo con el Explorador de archivos. Funciona igual: el agente solo exige que existan la carpeta de entrada y que la de salida
se pueda crear.

## Qué se crea

```
Mi_Proyecto/
├── 01_Data/                       ENTRADA
│   ├── 01_Database/                 ddh_collar.csv · ddh_survey.csv · ddh_assay.csv   (o assay_comp.csv)
│   ├── 02_Modelo-Bloques/           mb_<nombre>.csv   (puede quedar vacía si el flujo crea el modelo)
│   ├── 03_Dominio/                  un sólido .obj por dominio
│   ├── 04_Parametros_Entrada/       tablas CSV de parámetros (sólidos, topes, pasadas IDW, metales…)
│   ├── 05_Objetivo/                 sólido objetivo (recorte del modelo / reporte)
│   ├── 06_Topografia/               topografías (opcional; el flujo actual no las lee)
│   └── 07_Sondajes_Nuevos/          Plan-DDH_collar.csv · Plan-DDH_survey.csv
├── 02_Resultados/                 SALIDA
│   ├── 01_Database/                 Database.db, Importacion_Log.txt, CSV exportados
│   ├── 03_Modelo_Bloques/           modelo creado / recortado / estimado / valorizado / categorizado
│   └── 04_Reporte/                  reporte de recursos (Excel) y CSV de apoyo
└── Plantillas_para_llenar/        (solo con plantillas) fuera de 01_Data a propósito
```

* Cada subcarpeta lleva un **`LEEME.txt`** con qué va ahí, nombres típicos y para qué ruta de trabajo se usa. Puedes borrarlo.
* Las **plantillas** traen la cabecera correcta y 1-3 filas **ficticias**. Reemplaza las filas por tus datos y copia el archivo a la subcarpeta indicada
  en `Plantillas_para_llenar/LEEME.txt`. No se dejan dentro de `01_Data` porque el agente reconoce los datos por sus cabeceras y una plantilla se
  confundiría con datos reales.
* **No se sobrescribe ni se borra nada.** Si vuelves a crear un proyecto con el mismo nombre, lo que ya existe se deja tal cual y se informa.
* El nombre del proyecto no puede llevar `\ / : * ? " < > |`, ni terminar en punto o espacio, ni ser un nombre reservado de Windows (`CON`, `NUL`…).

## ¿Qué datos necesito para empezar?

Depende del trabajo que quieras hacer (el agente lo evalúa en la pestaña **🗂 Datos**, «¿Mis datos alcanzan?»):

| Quiero… | Necesito como mínimo |
|---|---|
| Estimar desde **sondajes** | `01_Database`: collar + survey + ensayos · `03_Dominio` (sólidos) · tablas en `04_Parametros_Entrada` |
| Estimar desde **compósitos** ya codificados | `01_Database/assay_comp.csv` · un modelo de bloques (existente o por crear) · tablas |
| **Recortar** el modelo | lo anterior + un sólido en `05_Objetivo` que **cruce** el modelo (el agente avisa antes de ejecutar si no se cruzan) |
| **Evaluar un plan** de sondajes | compósitos + `07_Sondajes_Nuevos` (collar y survey del plan) |

Si no sabes cómo debe ser un archivo, abre la pestaña **📄 Formatos y plantillas**: muestra las columnas, las primeras líneas de un ejemplo y deja
descargar la plantilla. Las tablas de parámetros se llenan y se cargan desde la pestaña **🛠 Parámetros**.

## Si algo falla

| Mensaje | Qué hacer |
|---|---|
| «Escribe rutas completas» | Usa una ruta absoluta (`D:\Proyectos\...`), no relativa |
| «No pude crear las carpetas» | Revisa permisos de esa ubicación o elige otra (p. ej. dentro de Documentos) |
| «La carpeta de entrada y la de salida deben ser distintas» | Elige dos carpetas diferentes |
| «La carpeta de entrada está vacía» | Normal en un proyecto nuevo: copia tus archivos o súbelos desde la pantalla |
| Sigues viendo rutas de otra persona en los conf | Pulsa **Continuar →**: se reubican *todas* las rutas de *todos* los conf (con respaldo en `Configuraciones/_respaldo_agente/`) |
