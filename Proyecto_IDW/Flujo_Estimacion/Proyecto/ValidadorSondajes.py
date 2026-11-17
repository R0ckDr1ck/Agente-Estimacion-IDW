"""
Nombre : ValidadorSondajes.py
Propósito: Validación lógica de las tablas de sondajes (Collar, Survey e Intervalos) independiente de
           la interfaz gráfica. Porta la lógica de FmDesurvey.validar_logica_desurvey a una función
           libre de Qt y reutilizable por el módulo Importador y cualquier otro módulo.
Creacion : 09-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import pandas as pd


# Columnas obligatorias por tipo de tabla (ya estandarizadas a nombres canónicos)
COLUMNAS_CRITICAS_COLLAR = ["Sondaje", "Este", "Norte", "Cota"]
COLUMNAS_CRITICAS_SURVEY = ["Sondaje", "Profundidad", "Azimut", "Dip"]
COLUMNAS_CRITICAS_INTERVALO = ["Sondaje", "Desde", "Hasta"]


def validar(df_collar, df_survey, intervalos=None):
    """
    Valida las tablas de sondajes y devuelve una lista de errores estructurados
    {tabla, sondaje, fila, error}. No depende de Qt.

    :param df_collar: DataFrame de Collar estandarizado (Sondaje, Este, Norte, Cota[, Profundidad]).
    :param df_survey: DataFrame de Survey estandarizado (Sondaje, Profundidad, Azimut, Dip).
    :param intervalos: dict {nombre_tabla: df} de tablas intervalares (assay, litologia, ...).
                       Cada df debe estar estandarizado (Sondaje, Desde, Hasta, + atributos).
    :return: lista de diccionarios de error.
    """
    if intervalos is None:
        intervalos = {}

    errores = []

    # ----------------------------------------------------------------------------------------------
    # 0. Campos obligatorios vacíos. Si los hay, se retorna de inmediato porque las validaciones
    #    posteriores asumen que no hay nulos en las columnas críticas.
    # ----------------------------------------------------------------------------------------------
    criticas = {"Collar": (df_collar, COLUMNAS_CRITICAS_COLLAR),
                "Survey": (df_survey, COLUMNAS_CRITICAS_SURVEY)}
    for nombre, df in intervalos.items():
        criticas[nombre] = (df, COLUMNAS_CRITICAS_INTERVALO)

    for nombre_tabla, (df, columnas) in criticas.items():
        if df is None or df.empty:
            continue
        for col in [c for c in columnas if c in df.columns]:
            for idx, row in df[df[col].isna()].iterrows():
                sondaje_val = row.get('Sondaje', 'Desconocido')
                sondaje_str = str(sondaje_val) if pd.notna(sondaje_val) else "N/A"
                errores.append({"tabla": nombre_tabla, "sondaje": sondaje_str, "fila": idx + 2,
                                "error": f"Campo obligatorio vacío: la columna '{col}' no tiene valor."})

    if errores:
        return errores

    # ----------------------------------------------------------------------------------------------
    # 1. Integridad referencial (sondajes huérfanos y collares sin survey)
    # ----------------------------------------------------------------------------------------------
    holes_collar = set(df_collar['Sondaje'].unique()) if df_collar is not None and not df_collar.empty else set()
    holes_survey = set(df_survey['Sondaje'].unique()) if df_survey is not None and not df_survey.empty else set()

    # A. Sondajes en tablas hijas que no existen en Collar (huérfanos)
    tablas_hijas = []
    if df_survey is not None and not df_survey.empty:
        tablas_hijas.append(("Survey", df_survey))
    for nombre, df in intervalos.items():
        if df is not None and not df.empty:
            tablas_hijas.append((nombre, df))

    for tabla, df in tablas_hijas:
        huerfanos = set(df['Sondaje'].unique()) - holes_collar
        for h in sorted(huerfanos):
            errores.append({"tabla": tabla, "sondaje": h, "fila": "-",
                            "error": "El sondaje no existe en la tabla Collar."})

    # B. Sondajes en Collar que no tienen Survey
    for h in sorted(holes_collar - holes_survey):
        errores.append({"tabla": "Collar", "sondaje": h, "fila": "-",
                        "error": "El sondaje no tiene registros en Survey. Es imposible calcular su trayectoria 3D."})

    # ----------------------------------------------------------------------------------------------
    # 2. Validaciones de Survey
    # ----------------------------------------------------------------------------------------------
    if df_survey is not None and not df_survey.empty:
        azimut_invalido = df_survey[(df_survey['Azimut'] < 0) | (df_survey['Azimut'] > 360)]
        for idx, row in azimut_invalido.iterrows():
            errores.append({"tabla": "Survey", "sondaje": row['Sondaje'], "fila": idx + 2,
                            "error": f"Azimut inválido ({row['Azimut']}°)."})

        dip_invalido = df_survey[(df_survey['Dip'] < -90) | (df_survey['Dip'] > 90)]
        for idx, row in dip_invalido.iterrows():
            errores.append({"tabla": "Survey", "sondaje": row['Sondaje'], "fila": idx + 2,
                            "error": f"Dip inválido ({row['Dip']}°)."})

        df_sv = df_survey.sort_values(by=['Sondaje', 'Profundidad'])
        df_sv['Prof_Prev'] = df_sv.groupby('Sondaje')['Profundidad'].shift(1)
        for idx, row in df_sv[df_sv['Profundidad'] <= df_sv['Prof_Prev']].iterrows():
            errores.append({"tabla": "Survey", "sondaje": row['Sondaje'], "fila": idx + 2,
                            "error": f"Profundidad anómala ({row['Profundidad']}). Es menor o igual a la anterior."})

    # ----------------------------------------------------------------------------------------------
    # 3. Validaciones de intervalos (assay, litologia, ...)
    # ----------------------------------------------------------------------------------------------
    for nombre_tabla, df in intervalos.items():
        if df is None or df.empty:
            continue

        for idx, row in df[df['Desde'] >= df['Hasta']].iterrows():
            errores.append({"tabla": nombre_tabla, "sondaje": row['Sondaje'], "fila": idx + 2,
                            "error": f"Intervalo negativo o nulo: Desde ({row['Desde']}) >= Hasta ({row['Hasta']})."})

        df_or = df.sort_values(by=['Sondaje', 'Desde'])
        df_or['Hasta_Prev'] = df_or.groupby('Sondaje')['Hasta'].shift(1)
        for idx, row in df_or[df_or['Desde'] < df_or['Hasta_Prev']].iterrows():
            errores.append({"tabla": nombre_tabla, "sondaje": row['Sondaje'], "fila": idx + 2,
                            "error": f"Superposición (Overlap): Desde ({row['Desde']}) menor que el Hasta anterior."})

    return errores


def clasificar_sondajes(df_collar, df_survey, intervalos=None):
    """
    Clasifica cada sondaje de Collar según su aptitud para el flujo de estimación, SIN modificar los
    datos. Es la base del paso de consistencia no destructivo: en vez de retirar sondajes de la base,
    emite un veredicto por sondaje que los módulos aguas abajo pueden consultar para filtrar.

    Un sondaje es 'estimable' cuando su trayectoria 3D es calculable, es decir cuando tiene
    coordenadas de Collar completas y al menos un registro en Survey (el desurvey salta cualquier
    sondaje sin Survey). No tener muestras en las tablas intervalares no lo vuelve no-estimable: es
    una advertencia (no hay nada que estimar en ese sondaje), no un impedimento de trayectoria.

    :return: tupla (verdictos, errores):
        - verdictos: lista de dicts {sondaje, estimable, motivo, n_survey, n_muestras}, uno por
          sondaje de Collar. 'motivo' resume los impedimentos y/o advertencias ('' si todo está bien).
        - errores: la lista estructurada de validar() sobre TODO el conjunto (alimenta el reporte).
    """
    if intervalos is None:
        intervalos = {}

    errores = validar(df_collar, df_survey, intervalos)

    verdictos = []
    if df_collar is None or df_collar.empty:
        return verdictos, errores

    tiene_survey = df_survey is not None and not df_survey.empty
    holes_survey = set(df_survey['Sondaje'].unique()) if tiene_survey else set()
    n_survey_por = df_survey.groupby('Sondaje').size() if tiene_survey else pd.Series(dtype='int64')

    # Muestras (filas intervalares) por sondaje, sumando todas las tablas intervalares cargadas.
    conteo_muestras = {}
    hay_intervalos = False
    for df in intervalos.values():
        if df is not None and not df.empty and 'Sondaje' in df.columns:
            hay_intervalos = True
            for s, n in df.groupby('Sondaje').size().items():
                conteo_muestras[s] = conteo_muestras.get(s, 0) + int(n)

    coord_cols = [c for c in ('Este', 'Norte', 'Cota') if c in df_collar.columns]

    for _, row in df_collar.iterrows():
        s = row['Sondaje']
        motivos = []

        coords_ok = bool(coord_cols) and all(pd.notna(row[c]) for c in coord_cols)
        if not coords_ok:
            motivos.append('coordenadas de Collar incompletas')

        en_survey = s in holes_survey
        if not en_survey:
            motivos.append('sin registros en Survey')

        n_mu = int(conteo_muestras.get(s, 0))
        estimable = coords_ok and en_survey

        # Advertencia no bloqueante: solo tiene sentido si ya hay tablas intervalares cargadas.
        if estimable and hay_intervalos and n_mu == 0:
            motivos.append('sin muestras en tablas intervalares')

        verdictos.append({
            'sondaje': str(s),
            'estimable': bool(estimable),
            'motivo': '; '.join(motivos),
            'n_survey': int(n_survey_por.get(s, 0)) if tiene_survey else 0,
            'n_muestras': n_mu,
        })

    return verdictos, errores
