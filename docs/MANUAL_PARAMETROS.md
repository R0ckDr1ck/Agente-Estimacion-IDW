# Manual de parámetros · flujo de estimación IDW

Este manual explica, herramienta por herramienta, qué hace cada paso del flujo de estimación y qué significa cada parámetro
de su archivo de configuración (conf_*.json): qué valor admite, qué pasa si lo subes o lo bajas y qué conviene recomendar.
Los consejos son orientación general de estimación de recursos; la decisión final depende de tu yacimiento y de tu estándar
de reporte. Puedes consultarlo conversando con el agente («¿qué pasa si subo la potencia?»).

## Índice

- [Conceptos generales](#conceptos-generales)
- [Rutas del flujo (conf_proyecto.json)](#rutas-del-flujo-conf-proyecto-json) — `conf_proyecto.json`
- [Importar base de datos](#importar-base-de-datos) — `conf_importar_bd.json`
- [Exportar base a CSV](#exportar-base-a-csv) — `conf_importar_bd.json`
- [Codificar sondajes](#codificar-sondajes) — `conf_codificar.json`
- [Acotar altas leyes](#acotar-altas-leyes) — `conf_acotamiento.json`
- [Compositar](#compositar) — `conf_comp.json`
- [Plan de sondajes](#plan-de-sondajes) — `conf_plan.json`
- [Crear modelo de bloques](#crear-modelo-de-bloques) — `conf_crear_mb.json`
- [Recortar modelo al sólido](#recortar-modelo-al-solido) — `conf_recorte.json`
- [Estimar (IDW)](#estimar-idw) — `conf_estimacion.json`
- [Valorizar modelo](#valorizar-modelo) — `conf_valorizacion.json`
- [Categorizar](#categorizar) — `conf_categ.json`
- [Generar reporte](#generar-reporte) — `conf_reporte.json`
- [Identificar esquema de un modelo de bloques](#identificar-esquema-de-un-modelo-de-bloques) — `sin configuración`

## Conceptos generales

### Inverso de la distancia (IDW)

El IDW estima la ley de un bloque como un promedio de las muestras cercanas donde cada muestra pesa 1 / distancia^potencia: cuanto más cerca, más pesa. Es un método simple y rápido, sin modelo de variograma. Los parámetros que más lo condicionan son la potencia, el elipsoide de búsqueda (radios y orientación) y el número mínimo y máximo de muestras.

### Estimación por pasadas

La estimación se hace en varias pasadas, de la más estricta (radios chicos, más muestras mínimas) a la más amplia. Cada pasada solo estima los bloques que cumplan su Condición (normalmente los que aún valen -99). Así los bloques cercanos a sondajes se estiman con criterios exigentes y los lejanos reciben una estimación más holgada en una pasada posterior. La pasada con que se estimó un bloque se guarda y luego decide la categoría.

### Elipsoide de búsqueda y anisotropía

Las muestras que puede usar un bloque están dentro de un elipsoide de tres radios (mayor, semi y menor) orientado con bearing, plunge y dip. Alinearlo con la continuidad geológica (p. ej. el rumbo y el manteo de una veta) evita mezclar muestras de direcciones sin relación. Con anisotropía variable, bearing, plunge y dip se leen de columnas del modelo de bloques.

### Dominios

Un dominio es una zona geológica con comportamiento de ley similar, modelada como un sólido (.obj). Los sondajes y los bloques reciben el código del sólido que los contiene. Estimar cada dominio solo con muestras de su propio dominio (contacto duro) evita mezclar poblaciones distintas.

### Compósitos

Los compósitos regularizan las muestras a una longitud de soporte común (por ejemplo 2 m) con un promedio ponderado por longitud. Se estima con compósitos, no con las muestras crudas, para que todas pesen según un soporte comparable.

### Acotamiento de altas leyes (top cut)

Unos pocos valores extremos pueden dominar un promedio. El acotamiento reemplaza las leyes mayores a un tope por el propio tope, por dominio. Un tope muy bajo recorta metal real; uno muy alto no controla el efecto de las altas leyes.

### Categorización de recursos

Clasifica cada bloque en Medido, Indicado, Inferido o Potencial según la distancia promedio a 3 sondajes y el número de sondajes que lo estimaron (y la pasada). Cuanto más cerca y más sondajes, mayor categoría. Las distancias de corte son una decisión de la persona competente, no un valor universal.

### Ley de corte y reporte

El reporte suma el tonelaje y promedia las leyes de los bloques que cumplen el corte (cutoff) sobre una variable (NSR, equivalente o la ley del primer metal), agrupados por dominio y categoría. Subir el corte baja el tonelaje y sube la ley media.


## Rutas del flujo (conf_proyecto.json)

`proyecto` · configuración: `conf_proyecto.json`

Archivo propio del proyecto con las carpetas del flujo y dos interruptores que cambian qué pasos se ejecutan. Al elegir las carpetas de entrada y salida el agente reubica estas rutas solo.

### Parámetros

#### Carpeta raíz del proyecto

`conf_proyecto.json` → `raiz`

Carpeta base del proyecto. Es informativa (la usa el flujo para su registro).

- **Si lo cambias:** Cambiarla no mueve archivos; solo cambia lo que el flujo anota.
- **Recomendación:** Déjala que el agente la actualice al elegir tus carpetas.

#### Base de datos del flujo

`conf_proyecto.json` → `base_datos`

Ruta de Database.db (SQLite) donde viven los sondajes, ensayos y compósitos.

- **Si lo cambias:** Los pasos importar, codificar, acotar, compositar, plan y estimar leen o escriben esta base; si cambia de lugar hay que importarla de nuevo.
- **Recomendación:** Mantenla dentro de tu carpeta de salida.

#### Carpeta de la base y de los CSV exportados

`conf_proyecto.json` → `carpeta_database`

Carpeta donde se guarda la base y donde el paso exportar deja un CSV por tabla.


#### Carpeta de los modelos de bloques

`conf_proyecto.json` → `carpeta_modelo_bloques`

Carpeta de salida de los modelos creados, estimados, valorizados y categorizados.


#### Carpeta del reporte

`conf_proyecto.json` → `carpeta_reporte`

Carpeta donde se guarda el Excel del reporte de recursos y un CSV por hoja.


#### Nombre base del modelo

`conf_proyecto.json` → `nombre_modelo`

Nombre base de los archivos de modelo (p. ej. mb_smeb genera mb_smeb_estimado.csv, mb_smeb_valorizado.csv).

- **Si lo cambias:** Cambiarlo cambia los nombres de los archivos de salida; los conf de cada paso deben apuntar a los mismos nombres.

#### Crear el modelo de bloques en el flujo

`conf_proyecto.json` → `crear_modelo_bloques` · valores: true o false

true = el flujo completo ejecuta el paso modelo y crea el modelo desde los sólidos; false = usa un modelo existente y omite ese paso.

- **Si lo cambias:** Con false el modelo debe existir en modelo_bloques_existente. Con true se crea de nuevo y se sobrescribe la salida.

#### Modelo de bloques existente

`conf_proyecto.json` → `modelo_bloques_existente`

CSV del modelo que se usa cuando crear_modelo_bloques es false.

- **Recomendación:** Debe traer las columnas de grilla XC, YC, ZC, DX, DY, DZ y las variables que usarán las pasadas (dominio, anisotropía).

#### Valorizar el modelo

`conf_proyecto.json` → `valorizar_modelo` · valores: true o false

true = el flujo ejecuta el paso valorizar; false = lo omite y categorizar toma el modelo estimado.

- **Si lo cambias:** Con false el reporte no tiene NSR ni equivalente (el corte se aplica a la ley del primer metal) y conf_reporte debe tener valorizado = false.


## Importar base de datos

`importar` · configuración: `conf_importar_bd.json`

Crea la base de datos del flujo (Database.db). Puede armarla con los datos crudos de sondajes (collar, survey y assay) o, si ya tienes compósitos codificados, solo con ese CSV.

**Cuándo usarlo.** Es el primer paso de cualquier flujo. Repítelo si cambian los CSV de sondajes o de compósitos.

**Qué hace, paso a paso**

1. Lee los CSV indicados
2. Valida los sondajes (los que tengan observaciones se excluyen o detienen el paso)
3. Escribe las tablas en Database.db

**Lee:** CSV de collar, survey y assay (origen sondajes) o el CSV de compósitos (origen compositos)  
**Genera:** Database.db

> ⚠ Recrea Database.db; reemplaza lo que hubiera (confirmación obligatoria).
> ⚠ Con origen compositos los pasos codificar, acotar y compositar se omiten solos.

### Parámetros

#### Base de datos de salida

`conf_importar_bd.json` → `proyecto`

Ruta de Database.db que se crea.

- **Si lo cambias:** Si cambia, los demás pasos deben apuntar a la misma base.

#### Carpeta de los CSV exportados

`conf_importar_bd.json` → `carpeta_csv_salida`

Carpeta donde el paso exportar escribe un CSV por tabla de la base.


#### Decimales

`conf_importar_bd.json` → `decimales` · valores: entero >= 0

Número de decimales al redondear las columnas numéricas al importar sondajes y al exportar CSV (los compósitos importados no se redondean).

- **Si lo cambias:** Menos decimales = archivos más livianos pero se pierde precisión en coordenadas y leyes (con 3 decimales hay diferencias de hasta 0.0005).
- **Recomendación:** 3 o 4 decimales suelen bastar; usa más si trabajas con coordenadas UTM muy precisas.

#### Excluir sondajes con observaciones

`conf_importar_bd.json` → `excluir_sondajes_con_observaciones` · valores: true o false

Qué hacer si la validación encuentra problemas en un sondaje (por ejemplo intervalos solapados o sin collar).

- **Si lo cambias:** true = ese sondaje se excluye y se avisa; false = el paso se detiene con error para que lo corrijas.
- **Recomendación:** Usa false cuando quieras garantizar que ningún sondaje se pierda en silencio.

#### Origen de los datos

`conf_importar_bd.json` → `origen` · valores: sondajes o compositos (cualquier otro valor detiene el paso)

Define de dónde sale la base.

- **Si lo cambias:** sondajes = collar + survey + assay (luego hay que codificar, acotar y compositar); compositos = solo el CSV de compósitos ya codificado (esos tres pasos se omiten).

#### CSV de compósitos

`conf_importar_bd.json` → `compositos.archivo`

Archivo de compósitos ya codificados y compositados (origen compositos).

- **Recomendación:** Debe traer Sondaje, Desde, Hasta, las coordenadas medias MIDX/MIDY/MIDZ, la ley y el dominio.

#### Nombre de la tabla de compósitos

`conf_importar_bd.json` → `compositos.nombre_tabla`

Nombre con que se guarda la tabla de compósitos en la base (assay_comp).

- **Si lo cambias:** Debe coincidir con la columna Compósito de las pasadas y con tabla_composito de conf_plan.json y conf_comp.json.

#### Rol de cada columna de compósitos

`conf_importar_bd.json` → `compositos.roles.*` · valores: Sondaje, Desde, Hasta, Incluir, Omitir

Para cada columna del CSV indica su rol. Sondaje, Desde y Hasta son obligatorios; Incluir la deja en la tabla; Omitir la descarta.


#### Tipo de dato de cada columna

`conf_importar_bd.json` → `compositos.tipos.*` · valores: Numérico o Texto

Tipo con que se guardan las columnas incluidas.


#### Tipo de tabla

`conf_importar_bd.json` → `compositos.tipo` · valores: Compósito

Tipo de la tabla (Compósito). Lo fija la herramienta; no suele cambiarse.


#### Columnas enteras

`conf_importar_bd.json` → `compositos.enteros[*]`

Columnas numéricas que se guardan como enteros (por ejemplo el dominio DOMCU).

- **Si lo cambias:** Sirve para que los códigos de dominio no queden como 21.0 y casen bien con las condiciones de las pasadas.

#### Tipo de tabla (Collar, Survey, Assay)

`conf_importar_bd.json` → `tablas[*].tipo` · valores: Collar, Survey o Assay

Cada elemento de tablas describe un CSV de sondajes y su tipo.


#### Archivo CSV de la tabla

`conf_importar_bd.json` → `tablas[*].archivo`

CSV de collar, survey o assay.


#### Nombre de la tabla en la base

`conf_importar_bd.json` → `tablas[*].nombre_tabla`

Nombre con que se guarda en la base (collar, survey, assay).

- **Si lo cambias:** assay debe coincidir con tabla_assay de conf_comp.json y con tabla de conf_codificar.json y conf_acotamiento.json.

#### Rol de cada columna del CSV

`conf_importar_bd.json` → `tablas[*].roles.*` · valores: Sondaje, Este, Norte, Cota, Profundidad, Azimut, Dip, Desde, Hasta, Incluir, Omitir

Cada columna (en MAYÚSCULAS) toma un rol de la base. Collar: Sondaje, Este, Norte, Cota, Profundidad. Survey: Sondaje, Profundidad, Azimut, Dip. Assay: Sondaje, Desde, Hasta.

- **Si lo cambias:** Las columnas Incluir viajan a la base con su nombre original (leyes, dominio antiguo, etc.); las Omitir se descartan.

#### Tipo de las columnas incluidas

`conf_importar_bd.json` → `tablas[*].tipos.*` · valores: Numérico o Texto

Tipo de dato de las columnas con rol Incluir.


#### Columnas enteras de la tabla

`conf_importar_bd.json` → `tablas[*].enteros[*]`

Columnas numéricas que se guardan como enteros (p. ej. un dominio antiguo).



## Exportar base a CSV

`exportar` · configuración: `conf_importar_bd.json`

Exporta a CSV todas las tablas de Database.db. Sirve para revisar en Excel u otro software cómo quedó la base tras codificar, acotar y compositar. No cambia la base.

**Cuándo usarlo.** Después de compositar (o de importar compósitos) si quieres revisar los datos fuera del flujo.

**Lee:** Database.db  
**Genera:** Un CSV por tabla en carpeta_csv_salida

> ⚠ Si el CSV de entrada está en la misma carpeta, el paso no lo sobrescribe cuando el origen es compositos.


## Codificar sondajes

`codificar` · configuración: `conf_codificar.json`

Asigna a cada tramo de sondaje el código del dominio (sólido .obj) en que cae su punto medio. Los puntos medios salen del desurvey de los sondajes (trayectoria con collar y survey).

**Cuándo usarlo.** Con origen sondajes, después de importar y antes de acotar y compositar.

**Qué hace, paso a paso**

1. Calcula la trayectoria 3D de cada sondaje (desurvey tangente)
2. Toma el punto medio de cada tramo
3. Prueba en qué sólido cae (con prioridad si hay solapes)
4. Escribe el código en la columna campo

**Lee:** Database.db (tabla de ensayos) y los sólidos .obj  
**Genera:** Modifica la tabla de ensayos en Database.db (agrega o reescribe la columna del dominio)

> ⚠ Modifica la base en el sitio (confirmación obligatoria).
> ⚠ Un sólido mal cerrado puede dar códigos erróneos en el borde.

### Parámetros

#### Base de datos

`conf_codificar.json` → `proyecto`

Database.db cuya tabla de ensayos se codifica.


#### Tabla de ensayos

`conf_codificar.json` → `tabla`

Tabla de la base que se codifica (assay).

- **Si lo cambias:** Debe ser la tabla que importaste.

#### Campo del dominio

`conf_codificar.json` → `campo`

Columna donde se escribe el código del dominio (por ejemplo DOMCU).

- **Si lo cambias:** Debe coincidir con campo_dominio de conf_acotamiento.json y con codigo_roca de conf_comp.json.

#### Tramos fuera de todo sólido

`conf_codificar.json` → `no_codificados` · valores: defecto o mantener

Qué hacer con los tramos que no caen en ningún sólido.

- **Si lo cambias:** defecto = reciben el valor_defecto; mantener = conservan el valor que ya tenía el campo.

#### Valor por defecto

`conf_codificar.json` → `valor_defecto`

Código que reciben los tramos no codificados cuando no_codificados es defecto (normalmente -99).

- **Si lo cambias:** Los tramos con -99 suelen descartarse al estimar porque ningún dominio los reclama.

#### Tolerancia de pared

`conf_codificar.json` → `tolerancia_pared` · valores: metros >= 0

Distancia en metros para rescatar puntos que caen justo fuera de un sólido pero a menos de esa distancia de su pared.

- **Si lo cambias:** Subirla asigna dominio a más tramos de borde (menos -99) pero puede meter muestras que no pertenecen al dominio; 0 es estricto.
- **Recomendación:** Usa 0 si tus sólidos son confiables; valores pequeños (decímetros) solo para absorber el error de modelado del contacto.

#### Método de desurvey

`conf_codificar.json` → `metodo_desurvey` · valores: Tangente Vulcan

Método con que se calcula la trayectoria del sondaje. Hoy es informativo: el codificador usa Tangente Vulcan.


#### Ruta del sólido

`conf_codificar.json` → `solidos[*].ruta`

Archivo .obj del dominio.


#### Código del dominio

`conf_codificar.json` → `solidos[*].valor`

Valor que se asigna a los tramos dentro de ese sólido (21, 31, 101...).

- **Si lo cambias:** Es el código que usarán las pasadas en Valor dominio y los topes de acotamiento.

#### Prioridad

`conf_codificar.json` → `solidos[*].prioridad` · valores: entero

Número entero que decide qué sólido gana cuando dos se solapan: prevalece la mayor prioridad.

- **Si lo cambias:** Subirla en un sólido lo hace ganar en las zonas de solape.


## Acotar altas leyes

`acotar` · configuración: `conf_acotamiento.json`

Aplica el tope de altas leyes (top cut) por dominio y crea la columna <variable>_ACOT con la ley acotada. Los valores sin dato y los bajo límite de detección se tratan aparte. Guarda los topes en la tabla _acotamiento de la base.

**Cuándo usarlo.** Después de codificar y antes de compositar, para controlar el efecto de las altas leyes.

**Qué hace, paso a paso**

1. Limpia la variable (marca -99 como sin dato y trata los valores bajo límite)
2. Aplica para cada dominio min(ley, tope)
3. Escribe la columna <variable>_ACOT

**Lee:** Database.db (tabla de ensayos con dominio)  
**Genera:** Modifica la tabla de ensayos (agrega <variable>_ACOT) y crea la tabla _acotamiento

> ⚠ Modifica la base en el sitio (confirmación obligatoria).
> ⚠ El motor aplica el valor de tope; el método y su parámetro quedan como registro de cómo se eligió.

### Parámetros

#### Base de datos

`conf_acotamiento.json` → `proyecto`

Database.db con los ensayos.


#### Tabla de ensayos

`conf_acotamiento.json` → `tabla`

Tabla donde se aplica el tope (assay).


#### Campo de dominio

`conf_acotamiento.json` → `campo_dominio`

Columna con el código de dominio (la que escribió codificar, p. ej. DOMCU).


#### Variable a acotar

`conf_acotamiento.json` → `variable`

Columna de ley sobre la que se aplican los topes (por ejemplo CUPCT).

- **Si lo cambias:** Genera la columna <variable>_ACOT; la compositación y la estimación deben usar esa columna acotada (por ejemplo CUPCT_ACOT) si quieres que el tope tenga efecto.

#### Valores sin dato

`conf_acotamiento.json` → `nulos`

Valor (o valores) que marcan «sin dato» y se excluyen de la estadística y del tope (normalmente -99).

- **Si lo cambias:** Si tus datos usan otro marcador, hay que indicarlo o se tratará como ley real.

#### Tratamiento bajo límite de detección

`conf_acotamiento.json` → `bdl` · valores: mitad, excluir, cero o tal_cual

Cómo tratar las leyes negativas que representan valores bajo el límite de detección.

- **Si lo cambias:** mitad = sustituye por la mitad del valor absoluto; excluir = como sin dato; cero = 0; tal_cual = no modifica.
- **Recomendación:** mitad es la convención habitual en geoquímica.

#### Valor del tope

`conf_acotamiento.json` → `topes.*.*.tope` · valores: número en unidades de la ley

Ley máxima permitida para esa variable y ese dominio; las leyes mayores se reemplazan por este valor. 99999 equivale a sin tope.

- **Si lo cambias:** Bajarlo reduce el efecto de las altas leyes y el metal estimado; subirlo (o 99999) deja pasar más altas leyes. Un tope se justifica con curvas de media y CV frente al tope, deciles o gráfico de probabilidad.
- **Recomendación:** Fíjalo por dominio con alguno de los métodos de abajo y revisa cuánto metal remueve (valores usuales de 1 a 5 % del metal).

#### Método con que se eligió el tope

`conf_acotamiento.json` → `topes.*.*.metodo` · valores: manual, percentil, media_sigma, cv_objetivo o metal_riesgo

Criterio usado para sugerir el tope de ese dominio. Queda registrado en la base.

- **Si lo cambias:** percentil = percentil ponderado de la ley; media_sigma = media + k·desviación; cv_objetivo = el tope más alto que deja el CV bajo un objetivo; metal_riesgo = percentil 98 o 99 según el metal en el decil superior; manual = valor puesto a mano.

#### Parámetro del método

`conf_acotamiento.json` → `topes.*.*.parametro` · valores: número

Número que alimenta el método. Con manual es el propio tope; con percentil, el percentil (p. ej. 98); con media_sigma, k (2.5 por defecto); con cv_objetivo, el CV buscado (1.5 por defecto).



## Compositar

`compositar` · configuración: `conf_comp.json`

Convierte los ensayos en compósitos de longitud fija (Run Length) y los georreferencia con el desurvey. Cada compósito es el promedio ponderado por longitud de los tramos que cubre, y puede cortarse al cambiar de dominio.

**Cuándo usarlo.** Después de codificar (y acotar), antes de estimar, cuando partes de sondajes crudos.

**Qué hace, paso a paso**

1. Prepara los ensayos y trata los faltantes
2. Corta cada sondaje en tramos de la longitud elegida (sin cruzar dominios si se pide)
3. Promedia las leyes ponderando por longitud
4. Calcula las coordenadas del punto medio (MID)
5. del techo (TOP) y de la base (BOT)
6. Guarda la tabla de compósitos

**Lee:** Database.db (tabla de ensayos)  
**Genera:** Crea o reemplaza la tabla de compósitos (assay_comp) en Database.db

> ⚠ Reemplaza la tabla de compósitos (confirmación obligatoria).

### Parámetros

#### Base de datos

`conf_comp.json` → `proyecto`

Database.db con los ensayos.


#### Tabla de ensayos de entrada

`conf_comp.json` → `tabla_assay`

Tabla con los ensayos codificados (assay).


#### Tabla de compósitos de salida

`conf_comp.json` → `tabla_composito`

Nombre de la tabla que se crea (assay_comp).

- **Si lo cambias:** Debe coincidir con la columna Compósito de las pasadas de estimación.

#### Método de compositación

`conf_comp.json` → `metodo` · valores: Run Length o Straight

Run Length corta cada sondaje en tramos de longitud fija; Straight conserva los intervalos originales.


#### Longitud del compósito

`conf_comp.json` → `longitud` · valores: metros > 0

Longitud fija de los compósitos en metros (solo Run Length).

- **Si lo cambias:** Más largo = más suavizado y menos compósitos (menos variabilidad); más corto = más detalle y más compósitos pero con más ruido.
- **Recomendación:** Suele elegirse la longitud de muestreo más frecuente o un múltiplo cercano a la altura de banco.

#### Tolerancia del último tramo

`conf_comp.json` → `tolerancia` · valores: metros >= 0

Si el residuo al final de un tramo es estrictamente menor que esta longitud, se fusiona con el compósito anterior en vez de quedar un compósito corto.

- **Si lo cambias:** Subirla evita compósitos cortos pero permite compósitos algo más largos que la longitud nominal.

#### Marcador de ley faltante

`conf_comp.json` → `faltantes_flag`

Valor que marca una ley faltante en los ensayos (normalmente -99).


#### Acción ante ley faltante

`conf_comp.json` → `faltantes_accion` · valores: Ignorar Tramo o Asignar valor fijo

Qué hacer con los tramos de ley faltante.

- **Si lo cambias:** Ignorar Tramo = no entran al promedio ni al denominador (el compósito se calcula con lo que sí tiene ley); Asignar valor fijo = se reemplazan por faltantes_valor.

#### Valor para ley faltante

`conf_comp.json` → `faltantes_valor` · valores: número

Valor que se usa cuando la acción es Asignar valor fijo.

- **Si lo cambias:** Con 0 los faltantes bajan la ley del compósito; úsalo solo si «sin dato» realmente significa estéril.

#### Acción ante huecos no registrados

`conf_comp.json` → `noregistrado_accion` · valores: Ignorar Tramo o Asignar valor fijo

Qué hacer con los tramos del sondaje que no tienen intervalo de ensayo (huecos).

- **Si lo cambias:** Ignorar Tramo = no se generan compósitos sobre el hueco; Asignar valor fijo = el hueco se rellena con noregistrado_valor.

#### Valor para huecos no registrados

`conf_comp.json` → `noregistrado_valor` · valores: número

Valor con que se rellenan los huecos cuando la acción es Asignar valor fijo.


#### Cortar por geología

`conf_comp.json` → `cortar_geologia` · valores: true o false

Si es true, ningún compósito cruza un cambio de dominio.

- **Si lo cambias:** true evita mezclar poblaciones distintas en un compósito (recomendado con contacto duro); false permite compósitos que cruzan el contacto.

#### Campo de dominio para cortar

`conf_comp.json` → `codigo_roca`

Columna del dominio usada para cortar los compósitos (por ejemplo DOMCU).


#### Campos numéricos

`conf_comp.json` → `campos_numericos[*]`

Columnas numéricas que se promedian ponderando por longitud (CUPCT y CUPCT_ACOT).

- **Si lo cambias:** La estimación usará alguno de estos campos como Campo Ley; si falta aquí, no existirá en los compósitos.

#### Campos categóricos

`conf_comp.json` → `campos_categoricos[*]`

Columnas de texto o códigos que no se promedian: el compósito conserva el valor mayoritario (por ejemplo DOMCU).



## Plan de sondajes

`plan` · configuración: `conf_plan.json`

Evalúa un plan de sondajes: genera muestras sintéticas a lo largo de cada sondaje planificado, les asigna dominio y una ley provisional, las compósita y las agrega a los compósitos de la base. Así la estimación y la categorización «ven» los sondajes nuevos y se puede medir cuánto mejora la categoría.

**Cuándo usarlo.** Antes de reestimar, para estimar la ganancia de categoría de un programa de perforación.

**Qué hace, paso a paso**

1. Genera una muestra cada «ancho» metros a lo largo de cada sondaje del plan
2. Crea una base temporal y los codifica con los sólidos
3. Asigna la ley provisional según el modo elegido
4. Compósita con la configuración de conf_comp.json
5. Agrega los compósitos del plan a la tabla de compósitos de la base
6. Escribe Plan_Resumen.json

**Lee:** CSV de collar y survey del plan, el modelo de bloques existente, la base y los sólidos  
**Genera:** Plan_Resumen.json, Plan_Assay_Temporal.csv, Plan_Assay_Comp.csv y Plan_Temporal.db en la carpeta de salida del plan; agrega compósitos del plan a Database.db

> ⚠ Un nombre de sondaje del plan no puede coincidir con uno de los compósitos reales (el paso se detiene).
> ⚠ Al repetirlo reemplaza los compósitos del plan anterior en la base.
> ⚠ La ley provisional casi no cambia las leyes estimadas: la ganancia refleja la cobertura de sondajes, no un cambio de ley.

### Parámetros

#### Paso activo

`conf_plan.json` → `activo` · valores: true o false

false = el flujo completo omite este paso.


#### Base de datos

`conf_plan.json` → `proyecto`

Database.db a la que se agregan los compósitos del plan.


#### Tabla de compósitos

`conf_plan.json` → `tabla_composito`

Tabla de la base que recibe los compósitos del plan (assay_comp).


#### CSV de collares del plan

`conf_plan.json` → `collar.archivo`

Un registro por sondaje planificado.


#### Columna del sondaje (collar)

`conf_plan.json` → `collar.col_sondaje`

Columna con el nombre del sondaje planificado.


#### Columna Este

`conf_plan.json` → `collar.col_este`

Columna con la coordenada Este del collar.


#### Columna Norte

`conf_plan.json` → `collar.col_norte`

Columna con la coordenada Norte del collar.


#### Columna Cota

`conf_plan.json` → `collar.col_cota`

Columna con la cota del collar.


#### Columna longitud

`conf_plan.json` → `collar.col_longitud`

Columna con la longitud total planificada del sondaje.


#### CSV de survey del plan

`conf_plan.json` → `survey.archivo`

Orientación de los sondajes planificados (un registro con profundidad 0 basta para orientación constante).


#### Columna del sondaje (survey)

`conf_plan.json` → `survey.col_sondaje`

Nombre del sondaje; debe coincidir con el del collar.


#### Columna profundidad

`conf_plan.json` → `survey.col_profundidad`

Profundidad del punto de survey.


#### Columna azimut

`conf_plan.json` → `survey.col_azimut`

Azimut en grados (0 a 360).


#### Columna dip

`conf_plan.json` → `survey.col_dip`

Inclinación en grados; negativo es hacia abajo.


#### Ancho de las muestras sintéticas

`conf_plan.json` → `muestreo.ancho` · valores: metros > 0

Longitud en metros de cada muestra sintética que se genera a lo largo del sondaje planificado.

- **Si lo cambias:** Más angosto = más muestras sintéticas y más tiempo; no cambia la cobertura, solo el detalle antes de compositar.

#### Origen de la ley provisional

`conf_plan.json` → `ley.modo` · valores: modelo, dominio o constante

Cómo se asigna una ley a las muestras sintéticas (la categorización necesita compósitos con ley para contarlos).

- **Si lo cambias:** modelo = ley del bloque que contiene la muestra (si no está estimado, respaldo por dominio); dominio = ley media del dominio; constante = ley.constante.

#### Columna de ley del modelo

`conf_plan.json` → `ley.columna_modelo`

Columna del modelo con la ley a usar en el modo modelo (por ejemplo cu_idw).


#### Ley constante

`conf_plan.json` → `ley.constante` · valores: número

Ley que se asigna a todas las muestras en el modo constante.


#### Modelo de bloques para la ley provisional

`conf_plan.json` → `modelo.archivo`

Modelo existente del que se toma la ley en el modo modelo.

- **Recomendación:** Debe ser el modelo completo, no uno recortado.

#### Columna centroide X

`conf_plan.json` → `modelo.xc`

Nombre de la columna XC del modelo.


#### Columna centroide Y

`conf_plan.json` → `modelo.yc`

Nombre de la columna YC del modelo.


#### Columna centroide Z

`conf_plan.json` → `modelo.zc`

Nombre de la columna ZC del modelo.


#### Columna tamaño X

`conf_plan.json` → `modelo.dx`

Nombre de la columna DX del modelo.


#### Columna tamaño Y

`conf_plan.json` → `modelo.dy`

Nombre de la columna DY del modelo.


#### Columna tamaño Z

`conf_plan.json` → `modelo.dz`

Nombre de la columna DZ del modelo.


#### Carpeta de salida del plan

`conf_plan.json` → `salida`

Carpeta donde se escriben Plan_Resumen.json y los archivos temporales del plan.

- **Si lo cambias:** El reporte lee Plan_Resumen.json de aquí (ganancia.resumen_plan debe apuntar a él).

#### Conservar la base temporal

`conf_plan.json` → `conservar_temporal` · valores: true o false

true = deja Plan_Temporal.db después del paso (útil para revisar); false = la borra.



## Crear modelo de bloques

`modelo` · configuración: `conf_crear_mb.json`

Crea un modelo de bloques 3D (con rotación y subbloques, formato compatible con Vulcan) a partir de un esquema de grilla. Asigna a los bloques variables como el dominio usando sólidos, y elimina los que cumplan una excepción. No estima leyes.

**Cuándo usarlo.** Cuando no tienes un modelo de bloques y quieres crearlo desde tus sólidos de dominio (conf_proyecto.json debe tener crear_modelo_bloques = true para el flujo completo; pedido a mano se ejecuta igual).

**Qué hace, paso a paso**

1. Genera la malla de bloques padre según el esquema
2. Codifica las variables con los sólidos por prioridad
3. Subdivide en subbloques los bloques que cortan los sólidos
4. Elimina los bloques que cumplen las excepciones
5. Escribe el CSV del modelo

**Lee:** Los sólidos .obj  
**Genera:** El CSV del modelo de bloques (salida)

> ⚠ Sobrescribe el CSV de salida (confirmación obligatoria).
> ⚠ La extensión es el largo en metros del modelo en cada eje, NO el número de bloques.
> ⚠ Plunge y dip del modelo no están soportados (se fijan en 0).

### Parámetros

#### Origen X

`conf_crear_mb.json` → `esquema.origen_x` · valores: metros

Coordenada Este de la esquina mínima de la grilla (metros).

- **Si lo cambias:** Debe cubrir todo lo que quieras modelar; lo que quede fuera de la grilla no tendrá bloques.

#### Origen Y

`conf_crear_mb.json` → `esquema.origen_y` · valores: metros

Coordenada Norte de la esquina mínima de la grilla (metros).


#### Origen Z

`conf_crear_mb.json` → `esquema.origen_z` · valores: metros

Cota de la esquina mínima de la grilla (metros).


#### Extensión X

`conf_crear_mb.json` → `esquema.extension_x` · valores: metros

Largo total del modelo en el eje X local, en metros (número de bloques padre × tamaño). No es el número de bloques.


#### Extensión Y

`conf_crear_mb.json` → `esquema.extension_y` · valores: metros

Largo total del modelo en el eje Y local, en metros.


#### Extensión Z

`conf_crear_mb.json` → `esquema.extension_z` · valores: metros

Largo total del modelo en el eje Z, en metros.


#### Tamaño del bloque padre X

`conf_crear_mb.json` → `esquema.x_size` · valores: metros

Dimensión X del bloque padre en metros.

- **Si lo cambias:** Bloques más grandes = modelo más liviano y estimación más suavizada; más chicos = más detalle y más tiempo y memoria.
- **Recomendación:** Suele elegirse del orden de 1/4 a 1/2 del espaciamiento de sondajes y compatible con la altura de banco.

#### Tamaño del bloque padre Y

`conf_crear_mb.json` → `esquema.y_size` · valores: metros

Dimensión Y del bloque padre en metros.


#### Tamaño del bloque padre Z

`conf_crear_mb.json` → `esquema.z_size` · valores: metros

Dimensión Z del bloque padre en metros (normalmente la altura de banco).


#### Bearing del modelo

`conf_crear_mb.json` → `esquema.bearing` · valores: grados

Azimut del eje X local del modelo en grados. 90 significa modelo sin rotación (X local = Este).

- **Si lo cambias:** Rota la grilla en planta para alinearla con la mineralización.

#### Subbloque mínimo X

`conf_crear_mb.json` → `esquema.sub_x` · valores: metros

Tamaño mínimo en X de los subbloques que se generan donde un sólido corta el bloque padre.

- **Si lo cambias:** Más chico = contactos mejor definidos pero más bloques; igual al padre = sin subbloques.

#### Subbloque mínimo Y

`conf_crear_mb.json` → `esquema.sub_y` · valores: metros

Tamaño mínimo en Y de los subbloques.


#### Subbloque mínimo Z

`conf_crear_mb.json` → `esquema.sub_z` · valores: metros

Tamaño mínimo en Z de los subbloques.


#### Nombre de la variable

`conf_crear_mb.json` → `variables[*].nombre`

Variable que se agrega al modelo (por ejemplo cu_idw para la ley estimada o domcu para el dominio).


#### Tipo de la variable

`conf_crear_mb.json` → `variables[*].tipo` · valores: Float, Integer o String

Tipo de dato de la variable.


#### Valor por defecto de la variable

`conf_crear_mb.json` → `variables[*].valor_defecto`

Valor inicial de la variable en todos los bloques. -99 significa aún no estimado o sin dominio.

- **Si lo cambias:** Las pasadas de estimación suelen seleccionar los bloques con la variable en -99.

#### Descripción de la variable

`conf_crear_mb.json` → `variables[*].descripcion`

Texto libre para documentar la variable.


#### Ruta del sólido

`conf_crear_mb.json` → `solidos[*].ruta`

Archivo .obj que codifica una variable en los bloques que caen dentro.


#### Variable que se asigna

`conf_crear_mb.json` → `solidos[*].variable`

Variable del modelo que recibe el valor del sólido (por ejemplo domcu).


#### Valor que se asigna

`conf_crear_mb.json` → `solidos[*].valor`

Valor que reciben los bloques dentro del sólido.

- **Si lo cambias:** Es el código de dominio que luego usan las pasadas.

#### Prioridad del sólido

`conf_crear_mb.json` → `solidos[*].prioridad` · valores: entero

Entero que decide qué sólido prevalece cuando se solapan; gana la mayor prioridad.


#### Expresión de excepción

`conf_crear_mb.json` → `excepciones[*].expresion`

Condición estilo Vulcan; los bloques que la cumplan se eliminan (por ejemplo domcu eq -99 elimina los bloques fuera de todo dominio).

- **Si lo cambias:** Una excepción mal escrita puede borrar bloques válidos; revisa el conteo de bloques tras crear el modelo.

#### Descripción de la excepción

`conf_crear_mb.json` → `excepciones[*].descripcion`

Texto libre.


#### Tolerancia de pared

`conf_crear_mb.json` → `tolerancia_pared` · valores: metros >= 0

Distancia en metros para considerar dentro del sólido a bloques que están muy cerca de su pared.

- **Si lo cambias:** Subirla incluye más bloques de borde en el dominio; 0 es estricto.

#### CSV del modelo de salida

`conf_crear_mb.json` → `salida`

Archivo donde se escribe el modelo creado.

- **Si lo cambias:** Debe ser el modelo que lean después recortar o estimar.

#### CSV para identificar el esquema

`conf_crear_mb.json` → `esquema_csv.archivo`

CSV de un modelo de bloques existente del que la herramienta de esquema deduce origen, extensión, tamaño y bearing. El motor de creación ignora esta clave.


#### Columna XC del CSV

`conf_crear_mb.json` → `esquema_csv.col_xc`

Nombre de la columna del centroide X en el CSV de esquema.


#### Columna YC del CSV

`conf_crear_mb.json` → `esquema_csv.col_yc`

Nombre de la columna del centroide Y.


#### Columna ZC del CSV

`conf_crear_mb.json` → `esquema_csv.col_zc`

Nombre de la columna del centroide Z.


#### Columna DX del CSV

`conf_crear_mb.json` → `esquema_csv.col_dx`

Nombre de la columna del tamaño X.


#### Columna DY del CSV

`conf_crear_mb.json` → `esquema_csv.col_dy`

Nombre de la columna del tamaño Y.


#### Columna DZ del CSV

`conf_crear_mb.json` → `esquema_csv.col_dz`

Nombre de la columna del tamaño Z.



## Recortar modelo al sólido

`recortar` · configuración: `conf_recorte.json`

Deja en el modelo solo los bloques cuyo centroide cae dentro de un sólido (por ejemplo la zona de óxidos), para estimar únicamente esa zona.

**Cuándo usarlo.** Antes de estimar, si solo te interesa una parte del modelo completo.

**Lee:** El modelo de bloques completo y el sólido de recorte  
**Genera:** El CSV del modelo recortado

> ⚠ La salida no puede ser el mismo archivo que el modelo de entrada.
> ⚠ Con recorte activo, conf_estimacion.json modelo debe apuntar a la salida de este paso.
> ⚠ Los bloques parcialmente dentro (centroide fuera) no se incluyen.

### Parámetros

#### Paso activo

`conf_recorte.json` → `activo` · valores: true o false

false = el flujo completo omite el recorte y estima el modelo completo.


#### Modelo completo (entrada)

`conf_recorte.json` → `modelo`

CSV del modelo de bloques completo.


#### Modelo recortado (salida)

`conf_recorte.json` → `salida`

CSV que se genera con los bloques que caen dentro del sólido.

- **Si lo cambias:** Debe distinguirse del archivo de entrada.

#### Sólido de recorte

`conf_recorte.json` → `solido.ruta`

Archivo .obj con la zona que se conserva.


#### Tolerancia de pared

`conf_recorte.json` → `solido.tolerancia_pared` · valores: metros >= 0

Distancia en metros para incluir bloques cuyo centroide está justo fuera de la pared del sólido.

- **Si lo cambias:** Subirla incluye más bloques de borde; 0 es estricto.

#### Columna X del centroide

`conf_recorte.json` → `solido.campos.x`

Columna del modelo con el centroide X (XC).


#### Columna Y del centroide

`conf_recorte.json` → `solido.campos.y`

Columna del modelo con el centroide Y (YC).


#### Columna Z del centroide

`conf_recorte.json` → `solido.campos.z`

Columna del modelo con el centroide Z (ZC).



## Estimar (IDW)

`estimar` · configuración: `conf_estimacion.json`

Estima la ley de los bloques por IDW en varias pasadas (cada una con su propio elipsoide, mínimo y máximo de muestras y condición) y calcula la distancia promedio a 3 sondajes, que luego usa la categorización.

**Cuándo usarlo.** Después de tener los compósitos en la base y un modelo de bloques (completo o recortado).

**Qué hace, paso a paso**

1. Lee el modelo de bloques y los compósitos
2. Reinicia la variable si se pidió (guarda la anterior)
3. Para cada pasada selecciona los bloques por su Condición y busca muestras dentro del elipsoide
4. Pondera por 1 / distancia^potencia y escribe la ley y los datos de reporte
5. Calcula la distancia promedio a 3 sondajes (d3h)
6. Escribe el CSV del modelo estimado

**Lee:** El modelo de bloques y la tabla de compósitos de Database.db  
**Genera:** El CSV del modelo estimado (con ley, nº de muestras, nº de sondajes, pasada y d3h)

> ⚠ Puede tardar horas con cientos de pasadas.
> ⚠ Sobrescribe el CSV del modelo estimado (confirmación obligatoria).
> ⚠ Una pasada que no estima nada avisa el motivo (filtro de dominio sin muestras, condición sin bloques o mínimo de muestras no alcanzado).

### Parámetros

#### Modelo de bloques de entrada

`conf_estimacion.json` → `modelo`

CSV del modelo que se estima (el completo o el recortado).

- **Si lo cambias:** Con recorte activo debe ser la salida de conf_recorte.json.

#### Base con los compósitos

`conf_estimacion.json` → `proyecto`

Database.db donde está la tabla de compósitos que usan las pasadas.


#### Columna X del compósito

`conf_estimacion.json` → `campos.x`

Columna con la coordenada X del punto medio del compósito (MIDX).


#### Columna Y del compósito

`conf_estimacion.json` → `campos.y`

Columna con la coordenada Y del punto medio (MIDY).


#### Columna Z del compósito

`conf_estimacion.json` → `campos.z`

Columna con la coordenada Z del punto medio (MIDZ).


#### Columna del sondaje

`conf_estimacion.json` → `campos.sondaje`

Columna con el nombre del sondaje del compósito (Sondaje).

- **Si lo cambias:** Se usa para contar sondajes distintos y limitar compósitos por sondaje.

#### Columna XC del bloque

`conf_estimacion.json` → `campos.bloque_xc`

Columna del centroide X en el modelo de bloques.


#### Columna YC del bloque

`conf_estimacion.json` → `campos.bloque_yc`

Columna del centroide Y en el modelo de bloques.


#### Columna ZC del bloque

`conf_estimacion.json` → `campos.bloque_zc`

Columna del centroide Z en el modelo de bloques.


#### Columna DX del bloque

`conf_estimacion.json` → `campos.bloque_dx`

Columna del tamaño X del bloque.


#### Columna DY del bloque

`conf_estimacion.json` → `campos.bloque_dy`

Columna del tamaño Y del bloque.


#### Columna DZ del bloque

`conf_estimacion.json` → `campos.bloque_dz`

Columna del tamaño Z del bloque.


#### Columna del número de muestras

`conf_estimacion.json` → `reporte.nsamp`

Nombre de la columna donde se guarda cuántas muestras estimaron cada bloque. Vacío = nombre por defecto <variable>_nsamp.


#### Columna del número de sondajes

`conf_estimacion.json` → `reporte.nholes`

Columna con cuántos sondajes distintos estimaron el bloque. Vacío = <variable>_nholes.

- **Si lo cambias:** La categorización cuenta estos sondajes (campos.nholes de conf_categ.json debe llamarse igual).

#### Columna de la pasada

`conf_estimacion.json` → `reporte.flag`

Columna con el número de pasada con que se estimó el bloque. Vacío = <variable>_pass.

- **Si lo cambias:** La categorización solo acepta Medido, Indicado o Inferido si la pasada es 3 o menor.

#### Columna de distancia a la muestra más cercana (cartesiana)

`conf_estimacion.json` → `reporte.distclose_cart`

Si tiene nombre, guarda la distancia cartesiana a la muestra más cercana.


#### Columna de distancia a la muestra más cercana (anisotrópica)

`conf_estimacion.json` → `reporte.distclose_anisot`

Si tiene nombre, guarda esa distancia medida en el espacio anisotrópico.


#### Columna del vecino más cercano

`conf_estimacion.json` → `reporte.nn`

Si tiene nombre, guarda la ley de la muestra más cercana (vecino más cercano), útil para validar.


#### Columna de distancia promedio (cartesiana)

`conf_estimacion.json` → `reporte.avgdist_cart`

Si tiene nombre, guarda la distancia promedio cartesiana a las muestras usadas.


#### Columna de distancia promedio (anisotrópica)

`conf_estimacion.json` → `reporte.avgdist_anisot`

Si tiene nombre, guarda la distancia promedio anisotrópica a las muestras usadas.


#### Columna del sondaje (reporte)

`conf_estimacion.json` → `reporte.sondaje`

Columna con el sondaje, usada para los datos de reporte por bloque.


#### Valor de bloque no estimado

`conf_estimacion.json` → `reporte.valor_defecto` · valores: número

Valor que marca un bloque aún sin estimar (normalmente -99).

- **Si lo cambias:** Las Condiciones de las pasadas (variable eq -99) dependen de este valor.

#### ID de la pasada

`conf_estimacion.json` → `pasadas[*].ID`

Identificador único de la pasada (por ejemplo cu21id1 = cobre, dominio 21, pasada 1).


#### Número de pasada

`conf_estimacion.json` → `pasadas[*].Pasada`

Orden de la pasada: 1 es la más estricta.

- **Si lo cambias:** La categorización solo acepta como Medido, Indicado o Inferido a los bloques estimados en la pasada 3 o menor.

#### Tabla de compósitos

`conf_estimacion.json` → `pasadas[*].Compósito`

Tabla de la base con los compósitos que usa la pasada (assay_comp).


#### Campo de ley

`conf_estimacion.json` → `pasadas[*].Campo Ley`

Columna de ley que se interpola.

- **Si lo cambias:** Usar la columna acotada (por ejemplo CUPCT_ACOT) aplica el tope de altas leyes; la sin acotar (CUPCT) no.

#### Peso del compósito

`conf_estimacion.json` → `pasadas[*].Peso`

Columna que pondera cada muestra, normalmente el largo del compósito (length).

- **Si lo cambias:** Con length los compósitos más largos pesan más.

#### Campo de dominio

`conf_estimacion.json` → `pasadas[*].Campo dominio`

Columna del compósito con el dominio, usada para filtrar las muestras de la pasada.


#### Valor de dominio

`conf_estimacion.json` → `pasadas[*].Valor dominio`

Dominio o dominios cuyas muestras usa la pasada. Acepta uno (21) o varios separados por coma (21, 22, 23); vacío = sin filtro.

- **Si lo cambias:** Con varios dominios sus muestras se usan juntas (contacto suave entre ellos). La Condición sigue eligiendo los bloques a estimar.

#### Variable estimada

`conf_estimacion.json` → `pasadas[*].Variable`

Columna del modelo donde se escribe la ley estimada (por ejemplo cu_idw). Las filas con la misma Variable forman un elemento multipasada.


#### Potencia del IDW

`conf_estimacion.json` → `pasadas[*].Potencia` · valores: número > 0

Exponente de la distancia en el peso 1 / distancia^potencia.

- **Si lo cambias:** Subirla da más peso a las muestras más cercanas (estimación más local y menos suavizada); bajarla suaviza más. Con potencia 1 el efecto de la distancia es débil; con valores altos se acerca al vecino más cercano.
- **Recomendación:** Valores de 2 a 3 son los habituales; valida contra una estimación de referencia o validación cruzada antes de cambiarla.

#### Radio mayor

`conf_estimacion.json` → `pasadas[*].Mayor` · valores: metros > 0

Alcance del elipsoide de búsqueda en el eje mayor (metros).

- **Si lo cambias:** Subirlo incluye muestras más lejanas (más bloques estimados en esa pasada y más suavizado); bajarlo es más estricto.
- **Recomendación:** Las pasadas siguientes suelen ampliar los radios de la anterior (por ejemplo ×1.5 y ×2).

#### Radio semi

`conf_estimacion.json` → `pasadas[*].Semi` · valores: metros > 0

Alcance del elipsoide en el eje intermedio (metros).

- **Si lo cambias:** Igual que el mayor pero en la dirección intermedia.

#### Radio menor

`conf_estimacion.json` → `pasadas[*].Menor` · valores: metros > 0

Alcance del elipsoide en el eje menor (metros).

- **Si lo cambias:** Igual que el mayor pero en la dirección de menor continuidad.

#### Bearing del elipsoide

`conf_estimacion.json` → `pasadas[*].Bearing`

Azimut de orientación del elipsoide de búsqueda: un número (igual para todos los bloques) o el nombre de una columna del modelo con el ángulo de cada bloque (por ejemplo lva_b).

- **Si lo cambias:** Orientarlo con la continuidad geológica evita mezclar muestras de direcciones sin relación; con columna del modelo la anisotropía varía bloque a bloque (el motor agrupa por cada combinación de ángulos, lo que puede tardar más).

#### Plunge del elipsoide

`conf_estimacion.json` → `pasadas[*].Plunge`

Inclinación (plunge) del eje mayor: número o columna del modelo (por ejemplo lva_p).


#### Dip del elipsoide

`conf_estimacion.json` → `pasadas[*].Dip`

Manteo (dip) del elipsoide: número o columna del modelo (por ejemplo lva_d).


#### Mínimo de muestras

`conf_estimacion.json` → `pasadas[*].Mín Samp` · valores: entero >= 1

Mínimo de compósitos que debe reunir un bloque para estimarse en esa pasada.

- **Si lo cambias:** Subirlo hace la pasada más exigente (menos bloques estimados y más estimación en pasadas posteriores); bajarlo estima más bloques con menos información, con más riesgo de sobreextrapolar.

#### Máximo de muestras

`conf_estimacion.json` → `pasadas[*].Máx Samp` · valores: entero >= 0

Máximo de compósitos que se usan por bloque (los más cercanos). 0 = sin límite.

- **Si lo cambias:** Bajarlo suaviza menos y acelera el cálculo; subirlo promedia más muestras (más suavizado).

#### Máximo por octante

`conf_estimacion.json` → `pasadas[*].Máx/oct` · valores: entero >= 0

Máximo de compósitos por octante alrededor del bloque. 0 = sin límite.

- **Si lo cambias:** Evita que una sola dirección (por ejemplo un sondaje denso) domine el promedio; con un valor chico exige muestras repartidas alrededor del bloque.

#### Máximo de compósitos por sondaje

`conf_estimacion.json` → `pasadas[*].Máx samp/sond` · valores: entero >= 0

Máximo de compósitos que puede aportar un mismo sondaje. 0 = sin límite.

- **Si lo cambias:** Bajarlo (por ejemplo a 2) obliga a usar más sondajes distintos, lo que reduce el efecto de un sondaje con altas leyes y mejora el conteo de sondajes para categorizar.

#### Discretización X

`conf_estimacion.json` → `pasadas[*].Disc X` · valores: entero >= 1

Número de puntos en X con que se discretiza el bloque; la ley del bloque promedia los puntos. 1 = solo el centroide.

- **Si lo cambias:** Más puntos representan mejor el volumen del bloque pero tardan más (nx·ny·nz puntos por bloque).

#### Discretización Y

`conf_estimacion.json` → `pasadas[*].Disc Y` · valores: entero >= 1

Número de puntos en Y con que se discretiza el bloque.


#### Discretización Z

`conf_estimacion.json` → `pasadas[*].Disc Z` · valores: entero >= 1

Número de puntos en Z con que se discretiza el bloque.


#### Condición de la pasada

`conf_estimacion.json` → `pasadas[*].Condición`

Expresión estilo Vulcan que elige los bloques que esa pasada estima, por ejemplo cu_idw eq -99 and domcu eq 21 (solo los aún no estimados del dominio 21). Operadores eq ne gt ge lt le y and or not.

- **Si lo cambias:** La condición se evalúa sobre el modelo vivo, que se actualiza tras cada pasada: por eso una pasada posterior solo rellena lo que las anteriores dejaron en -99. Una condición mal escrita deja la pasada sin estimar nada (el motor lo avisa).

#### Estimar a nivel de bloque padre

`conf_estimacion.json` → `est_parent.activo` · valores: true o false

true = en un modelo con subbloques se estima a la resolución del bloque padre y los subbloques heredan el valor (como Vulcan).

- **Si lo cambias:** Reduce mucho el tiempo con modelos subbloqueados; con false se estima cada subbloque.

#### Origen X del bloque padre

`conf_estimacion.json` → `est_parent.origen_x`

Origen X de la grilla de bloques padre (debe coincidir con el esquema del modelo).


#### Origen Y del bloque padre

`conf_estimacion.json` → `est_parent.origen_y`

Origen Y de la grilla padre.


#### Origen Z del bloque padre

`conf_estimacion.json` → `est_parent.origen_z`

Origen Z de la grilla padre.


#### Tamaño X del bloque padre

`conf_estimacion.json` → `est_parent.tam_x`

Tamaño X del bloque padre en metros.


#### Tamaño Y del bloque padre

`conf_estimacion.json` → `est_parent.tam_y`

Tamaño Y del bloque padre en metros.


#### Tamaño Z del bloque padre

`conf_estimacion.json` → `est_parent.tam_z`

Tamaño Z del bloque padre en metros.


#### Bearing del modelo

`conf_estimacion.json` → `est_parent.bearing`

Azimut del eje X local del modelo (90 = sin rotación); debe coincidir con el esquema del modelo.


#### Radio mayor de la distancia a 3 sondajes

`conf_estimacion.json` → `d3h.mayor` · valores: metros > 0

Radio mayor (metros) de la búsqueda que calcula la distancia promedio a 3 sondajes.

- **Si lo cambias:** Si es menor que la distancia de Potencial de la categorización, los bloques más lejanos quedan sin distancia y no se categorizan.
- **Recomendación:** Debe ser mayor o igual que distancias.potencial de conf_categ.json.

#### Radio semi de la distancia a 3 sondajes

`conf_estimacion.json` → `d3h.semi` · valores: metros > 0

Radio semi (metros) de esa búsqueda.


#### Radio menor de la distancia a 3 sondajes

`conf_estimacion.json` → `d3h.menor` · valores: metros > 0

Radio menor (metros) de esa búsqueda.


#### Elemento principal

`conf_estimacion.json` → `d3h.elemento_principal`

Variable (por ejemplo cu_idw) cuyas pasadas definen la estimación de la distancia a 3 sondajes y de la que hereda la orientación.

- **Si lo cambias:** Con radios iguales (esfera) se omite la orientación porque no cambia el resultado y ahorra horas.

#### Reiniciar la variable antes de estimar

`conf_estimacion.json` → `reiniciar.activo` · valores: true o false

true = antes de estimar se reinician a «no estimado» la variable y sus columnas de reporte (útil si el modelo ya trae una estimación previa).

- **Si lo cambias:** Sin reiniciar, las pasadas solo estiman los bloques que aún valen -99.

#### Conservar la estimación anterior

`conf_estimacion.json` → `reiniciar.conservar_anterior` · valores: true o false

true = la ley previa queda en <variable>_anterior al reiniciar.

- **Si lo cambias:** Permite calcular la ganancia de ley y metal fino contra lo anterior en el reporte.

#### CSV del modelo estimado

`conf_estimacion.json` → `salida`

Archivo del modelo estimado que se genera.

- **Si lo cambias:** Es la entrada de valorizar (o de categorizar si no se valoriza).


## Valorizar modelo

`valorizar` · configuración: `conf_valorizacion.json`

Agrega al modelo estimado los atributos económicos y físicos que usa el reporte: densidad, recuperación por metal, NSR y equivalente. No recalcula leyes.

**Cuándo usarlo.** Después de estimar y antes de categorizar, si quieres reportar con NSR o equivalente. Si no, usa valorizar_modelo = false.

**Qué hace, paso a paso**

1. Asigna una densidad fija si se pidió
2. Calcula la recuperación de cada metal (columna pct_rec_<clave>)
3. Evalúa las expresiones de NSR y de equivalente
4. Escribe el CSV valorizado

**Lee:** El modelo estimado  
**Genera:** El CSV del modelo valorizado

> ⚠ Sobrescribe el CSV valorizado (confirmación obligatoria).
> ⚠ Las expresiones se evalúan con los nombres de las columnas del modelo; un nombre mal escrito detiene el paso.

### Parámetros

#### Modelo estimado (entrada)

`conf_valorizacion.json` → `modelo`

CSV del modelo estimado que se valoriza.


#### Asignar una densidad fija

`conf_valorizacion.json` → `asignar_densidad` · valores: true o false

true = se escribe la misma densidad en todos los bloques; false = se respeta la densidad variable que ya trae el modelo.

- **Si lo cambias:** Con false, col_densidad debe ser la columna del modelo con la densidad (si falta, avisa) y es la que usa el reporte.

#### Densidad fija

`conf_valorizacion.json` → `densidad` · valores: t/m3

Densidad en t/m3 que se asigna a todos los bloques cuando asignar_densidad es true.

- **Si lo cambias:** Cambia directamente el tonelaje del reporte (tonelaje = densidad × volumen).

#### Columna de densidad

`conf_valorizacion.json` → `col_densidad`

Columna del modelo donde se escribe la densidad fija, o de donde se lee la densidad variable.

- **Si lo cambias:** Debe coincidir con coboDensidad de conf_reporte.json.

#### Clave del metal

`conf_valorizacion.json` → `metales[*].clave`

Identificador corto del metal (cu, au); da nombre a la columna de recuperación pct_rec_<clave>.


#### Columna de ley del metal

`conf_valorizacion.json` → `metales[*].columna`

Columna del modelo con la ley estimada del metal (por ejemplo cu_idw).


#### Expresión de recuperación

`conf_valorizacion.json` → `metales[*].exp_recuperacion`

Recuperación del metal en porcentaje: un número (por ejemplo 85) o una expresión por bloque que puede depender de la ley y admite condicionales y funciones de math.

- **Si lo cambias:** Bajarla reduce el NSR y el equivalente del metal; las recuperaciones variables con la ley permiten curvas de recuperación.

#### Expresión del NSR

`conf_valorizacion.json` → `exp_nsr`

Fórmula del NSR con las columnas de ley y de recuperación del modelo (por ejemplo cu_idw*100).

- **Si lo cambias:** El corte del reporte puede aplicarse sobre el NSR, así que esta fórmula define qué bloques entran.

#### Columna del NSR

`conf_valorizacion.json` → `col_nsr`

Nombre de la columna donde se escribe el NSR.

- **Si lo cambias:** Debe coincidir con coboNsr de conf_reporte.json si el reporte usa NSR.

#### Expresión del equivalente

`conf_valorizacion.json` → `exp_equiv`

Fórmula de la ley equivalente (por ejemplo cu_idw cuando hay un solo metal).


#### Columna del equivalente

`conf_valorizacion.json` → `col_equiv`

Nombre de la columna donde se escribe el equivalente.

- **Si lo cambias:** Debe coincidir con coboEquiv de conf_reporte.json si el corte usa el equivalente.

#### CSV del modelo valorizado

`conf_valorizacion.json` → `salida`

Archivo del modelo valorizado que se genera.

- **Si lo cambias:** Es la entrada de categorizar.


## Categorizar

`categorizar` · configuración: `conf_categ.json`

Clasifica los bloques en Medido (1), Indicado (2), Inferido (3) o Potencial (4) según la distancia promedio a 3 sondajes, el número de sondajes que los estimaron y la pasada con que se estimaron.

**Cuándo usarlo.** Después de valorizar (o de estimar si no valorizas), antes del reporte.

**Qué hace, paso a paso**

1. Asigna Potencial a todo bloque con 0 < distancia <= distancias.potencial
2. En la banda de Medido (hasta distancias.medido): Medido con 3 o más sondajes, Indicado con 2, Inferido con 1
3. En la banda de Indicado (hasta distancias.indicado): Indicado con 2 o más sondajes, Inferido con 1
4. En la banda de Inferido (hasta distancias.inferido): Inferido con 1 o más sondajes
5. Aplica el piso si está activo (la categoría nueva no puede ser peor que la anterior)

**Lee:** El modelo valorizado (o estimado) con distancia, nº de sondajes y pasada  
**Genera:** El CSV categorizado con la columna de categoría

> ⚠ Sobrescribe el CSV categorizado (confirmación obligatoria).
> ⚠ Solo bloques estimados en la pasada 3 o menor pueden ser Medido, Indicado o Inferido.
> ⚠ Las distancias de corte deben ser crecientes (medido < indicado < inferido < potencial); el motor avisa si no lo son.
> ⚠ Si la columna de distancia no tiene valores mayores que 0 el paso se detiene (no categoriza en silencio).

### Parámetros

#### Modelo de entrada

`conf_categ.json` → `modelo`

CSV del modelo valorizado (o estimado si no se valoriza).


#### Método de categorización

`conf_categ.json` → `metodo` · valores: IDW

Método de categorización (IDW).


#### Columna de distancia a 3 sondajes

`conf_categ.json` → `campos.distancia`

Columna con la distancia promedio a 3 sondajes (d3h_avgdist_anisot) calculada por la estimación.

- **Si lo cambias:** Si el modelo no la trae o no tiene valores mayores que 0, el paso se detiene.

#### Columna del número de sondajes

`conf_categ.json` → `campos.nholes`

Columna con cuántos sondajes estimaron cada bloque (por ejemplo cu_idw_nholes).

- **Si lo cambias:** Define si un bloque llega a Medido (3 o más sondajes), Indicado (2) o Inferido (1).

#### Columna de la pasada

`conf_categ.json` → `campos.pass`

Columna con la pasada con que se estimó cada bloque (por ejemplo cu_idw_pass).

- **Si lo cambias:** Solo las pasadas 1 a 3 pueden dar Medido, Indicado o Inferido.

#### Columna de la categoría nueva

`conf_categ.json` → `campos.col_categoria`

Nombre de la columna donde se escribe la categoría resultante (por ejemplo categ_nuevo).

- **Si lo cambias:** Usa un nombre distinto de la categoría anterior (categ) para no pisarla y poder comparar.

#### Piso de categoría

`conf_categ.json` → `piso.activo` · valores: true o false

true = la categoría nueva no puede ser peor que la anterior (orden Medido > Indicado > Inferido > Potencial); si la nueva no existe se conserva la anterior.

- **Si lo cambias:** Evita que agregar sondajes degrade un bloque (la estimación se detiene en la primera pasada que reúne el mínimo de muestras, no de sondajes). Con piso activo se agrega la columna con el resultado sin piso.

#### Columna de la categoría anterior

`conf_categ.json` → `piso.columna_anterior`

Columna del modelo con la categoría previa (por ejemplo categ).

- **Si lo cambias:** Si el piso está activo y el modelo no la trae, el paso se detiene con un mensaje claro.

#### Distancia máxima de Medido

`conf_categ.json` → `distancias.medido` · valores: metros

Distancia promedio a 3 sondajes (metros) hasta la cual un bloque puede ser Medido.

- **Si lo cambias:** Subirla amplía el recurso Medido (más optimista); bajarla lo reduce. Es una decisión de la persona competente y debe tener respaldo geoestadístico (alcance del variograma, continuidad).

#### Distancia máxima de Indicado

`conf_categ.json` → `distancias.indicado` · valores: metros

Distancia hasta la cual un bloque puede ser Indicado.

- **Si lo cambias:** Subirla amplía Indicado; debe ser mayor que la de Medido.

#### Distancia máxima de Inferido

`conf_categ.json` → `distancias.inferido` · valores: metros

Distancia hasta la cual un bloque puede ser Inferido.

- **Si lo cambias:** Subirla amplía Inferido; debe ser mayor que la de Indicado.

#### Distancia máxima de Potencial

`conf_categ.json` → `distancias.potencial` · valores: metros

Distancia hasta la cual un bloque se marca Potencial (sin filtro de pasada).

- **Si lo cambias:** La distancia a 3 sondajes de la estimación (d3h) debe tener un radio de búsqueda al menos igual, o los bloques lejanos no tendrán distancia.

#### CSV categorizado

`conf_categ.json` → `salida`

Archivo del modelo categorizado que se genera.

- **Si lo cambias:** Es la entrada del reporte.


## Generar reporte

`reporte` · configuración: `conf_reporte.json`

Genera el reporte de recursos en Excel (hojas Resumen, Resumen_Potencial, Comparacion_Categ y, con un plan, Ganancia_Plan) y un CSV por hoja. Resume tonelaje y leyes por dominio y categoría con el corte elegido.

**Cuándo usarlo.** Al final del flujo, para entregar los resultados.

**Qué hace, paso a paso**

1. Aplica los filtros (sólido, bloques minados)
2. Aplica el corte sobre la variable elegida
3. Calcula tonelaje = densidad × volumen y promedia leyes ponderando por tonelaje
4. Agrupa por dominio y categoría
5. Escribe el Excel y los CSV

**Lee:** El modelo categorizado (y Plan_Resumen.json si hay ganancia del plan)  
**Genera:** Un Excel con fecha y hora en el nombre y un CSV por hoja (no sobrescribe nada)

### Parámetros

#### Modelo categorizado

`conf_reporte.json` → `modelo`

CSV categorizado que se reporta.


#### Carpeta del reporte

`conf_reporte.json` → `salida`

Carpeta donde se escribe el Excel y los CSV.


#### Columna de agrupación

`conf_reporte.json` → `columnas.coboGrupo`

Columna del modelo (por ejemplo domcu) cuyos valores forman las filas del resumen.


#### Columna de categoría

`conf_reporte.json` → `columnas.coboCategoria`

Columna con la categoría de recursos (categ_nuevo).

- **Si lo cambias:** Para reportar la categoría anterior usa categ; para la nueva, categ_nuevo.

#### Columna de densidad

`conf_reporte.json` → `columnas.coboDensidad`

Columna del modelo con la densidad (t/m3) para calcular tonelaje.

- **Si lo cambias:** Debe existir en el modelo (la escribe o respeta valorizar).

#### Columna de volumen

`conf_reporte.json` → `columnas.coboVolumen`

Columna con el volumen del bloque. Vacío = se calcula como DX × DY × DZ.

- **Si lo cambias:** Úsala si el modelo trae volúmenes parciales (subbloques cortados).

#### Columna de tamaño X

`conf_reporte.json` → `columnas.coboXLen`

Columna con el tamaño X del bloque (DX).


#### Columna de tamaño Y

`conf_reporte.json` → `columnas.coboYLen`

Columna con el tamaño Y del bloque (DY).


#### Columna de tamaño Z

`conf_reporte.json` → `columnas.coboZLen`

Columna con el tamaño Z del bloque (DZ).


#### Columna del NSR

`conf_reporte.json` → `columnas.coboNsr`

Columna con el NSR (la escribe valorizar). Vacío si el modelo no está valorizado.


#### Columna del equivalente

`conf_reporte.json` → `columnas.coboEquiv`

Columna con la ley equivalente (la escribe valorizar). Vacío si no está valorizado.


#### Variable de corte

`conf_reporte.json` → `variable_corte` · valores: nsr o equiv

Variable sobre la que se aplica el cutoff.

- **Si lo cambias:** nsr o equiv usan las columnas de la valorización; si el modelo no está valorizado (valorizado = false) el corte se aplica a la ley del primer metal.

#### Ley de corte (cutoff)

`conf_reporte.json` → `cutoff` · valores: número en las unidades de la variable de corte

Valor mínimo de la variable de corte para que un bloque entre al reporte.

- **Si lo cambias:** Subirlo baja el tonelaje y sube la ley media; bajarlo incluye más material de menor ley. 0 incluye todo lo estimado.
- **Recomendación:** Fíjalo con el criterio económico del proyecto (costos y precios), no por la distribución de leyes.

#### Metal principal

`conf_reporte.json` → `metal_principal`

Nombre del metal principal; define el encabezado del equivalente y la unidad de los finos (miles de onzas para Au, millones de onzas para otros).


#### Unidad del metal principal

`conf_reporte.json` → `unidad_principal`

Unidad en que viene la ley del principal. oz = ya está en onzas; otra unidad (g/t, ppm) se convierte con 31.1035 g por onza.

- **Si lo cambias:** Una unidad mal indicada cambia los finos en onzas por un factor.

#### Columna de ley del metal

`conf_reporte.json` → `metales[*].columna`

Columna del modelo con la ley de cada metal que aparecerá en el reporte.


#### Etiqueta del metal

`conf_reporte.json` → `metales[*].etiqueta`

Nombre que se muestra en el reporte (Cu, Au).


#### Unidad del metal

`conf_reporte.json` → `metales[*].unidad`

Unidad de la ley (pct, ppm, g/t).


#### Factor de conversión

`conf_reporte.json` → `metales[*].factor` · valores: número

Factor que convierte la ley a la unidad mostrada; 1 = sin conversión.


#### Fecha del reporte

`conf_reporte.json` → `fecha_reporte`

Fecha (AAAA-MM-DD) que aparece en el título y en el período del archivo.


#### Nota de precios

`conf_reporte.json` → `etiqueta_precios`

Texto libre con los precios usados, que se imprime en el pie del reporte.


#### Reportar solo dentro de un sólido

`conf_reporte.json` → `solido.activo` · valores: true o false

true = solo se reportan los bloques cuyo centroide cae dentro del sólido; el archivo lleva el sufijo con el nombre del sólido.

- **Si lo cambias:** Útil para reportar una zona (por ejemplo óxidos) sin recortar el modelo.

#### Sólido del reporte

`conf_reporte.json` → `solido.ruta`

Archivo .obj del sólido para el filtro.


#### Tolerancia de pared del sólido

`conf_reporte.json` → `solido.tolerancia_pared` · valores: metros >= 0

Distancia en metros para incluir bloques justo fuera de la pared.


#### Columna X del centroide (sólido)

`conf_reporte.json` → `solido.campos.x`

Columna del centroide X para la prueba punto-en-sólido (XC).


#### Columna Y del centroide (sólido)

`conf_reporte.json` → `solido.campos.y`

Columna del centroide Y (YC).


#### Columna Z del centroide (sólido)

`conf_reporte.json` → `solido.campos.z`

Columna del centroide Z (ZC).


#### Usar variable de minado

`conf_reporte.json` → `minado.activo` · valores: true o false

true = se excluyen del reporte los bloques ya minados (según la variable).


#### Variable de minado

`conf_reporte.json` → `minado.variable`

Columna del modelo que indica cuánto está minado (por ejemplo topof).

- **Si lo cambias:** Los valores vacíos cuentan como no minado (el reporte avisa).

#### Tipo de variable de minado

`conf_reporte.json` → `minado.tipo` · valores: binario o porcentaje

binario = 1 minado y 0 no minado; porcentaje = porcentaje remanente (100 no minado, 0 minado).

- **Si lo cambias:** Con porcentaje un valor intermedio deja esa proporción del bloque (se reduce su tonelaje, las leyes no cambian).

#### Comparar con la categoría anterior

`conf_reporte.json` → `comparacion.activo` · valores: true o false

true = agrega la hoja Comparacion_Categ con el tonelaje anterior vs actual y la matriz de cambio de categoría.


#### Columna de la categoría anterior

`conf_reporte.json` → `comparacion.categoria_anterior`

Columna del modelo con la categoría previa (1 Medido, 2 Indicado, 3 Inferido, 4 Potencial; otro valor = sin categoría).


#### Modelo valorizado

`conf_reporte.json` → `valorizado` · valores: true o false

false = el modelo no tiene NSR ni equivalente: el reporte no los incluye y el corte se aplica a la ley del primer metal.


#### Hoja de ganancia del plan

`conf_reporte.json` → `ganancia.activo` · valores: true o false

true = agrega la hoja Ganancia_Plan: ganancia de tonelaje por categoría (categoría nueva menos anterior) y por dominio, y el ratio por metro perforado.


#### Resumen del plan

`conf_reporte.json` → `ganancia.resumen_plan`

Ruta de Plan_Resumen.json que escribe el paso plan; de ahí sale la longitud total perforada para el ratio por metro.

- **Si lo cambias:** Debe apuntar al archivo de la carpeta de salida del plan.

#### Longitud del plan manual

`conf_reporte.json` → `ganancia.longitud_manual` · valores: metros

Longitud total del plan en metros si no hay Plan_Resumen.json. Vacío = se usa el resumen.



## Identificar esquema de un modelo de bloques

`esquema_bloques`

Herramienta de solo lectura que deduce el esquema de la grilla (origen, extensión, tamaño de bloque, subbloque y bearing) a partir de un CSV de modelo de bloques existente. Sirve para rellenar el esquema de conf_crear_mb.json.

**Cuándo usarlo.** Cuando tienes un modelo de bloques de otro software y quieres reproducir su grilla.

**Lee:** El CSV del modelo con XC, YC, ZC, DX, DY, DZ  
**Genera:** Solo imprime el esquema; no modifica archivos

> ⚠ Con bloques cuadrados en planta y sin subbloques el bearing solo queda definido módulo 90.
> ⚠ No detecta plunge ni dip.
