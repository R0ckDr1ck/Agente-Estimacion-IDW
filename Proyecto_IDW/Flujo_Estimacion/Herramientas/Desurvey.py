"""
Nombre : Desurvey.py
Propósito: Punto de entrada (fachada) de los motores de desurvey de sondajes. Expone las
           constantes de método, la fábrica `crear_desurvey` para seleccionar el motor, y los
           motores concretos. Mantiene el alias `Desurvey` = motor Leapfrog para conservar la
           compatibilidad con el código existente (`Desurvey().calcular_sondajes(...)`).

           Arquitectura de motores intercambiables:
             MotorDesurvey (base abstracta)  ->  Herramientas/MotorDesurvey.py
               ├── DesurveyLeapfrog          ->  Herramientas/DesurveyLeapfrog.py       (por defecto)
               └── DesurveyTangenteVulcan    ->  Herramientas/DesurveyTangenteVulcan.py (curvatura mínima, réplica de Vulcan)

Creacion : 23-Febrero-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 08-Julio-2026 - Refactor a arquitectura de motores intercambiables (Leapfrog / Tangente Vulcan).
"""

import pandas as pd

from Herramientas.MotorDesurvey import MotorDesurvey
from Herramientas.DesurveyLeapfrog import DesurveyLeapfrog
from Herramientas.DesurveyTangenteVulcan import DesurveyTangenteVulcan

# Métodos de desurvey disponibles (usar como argumento de `crear_desurvey`)
METODO_LEAPFROG = "Leapfrog"
METODO_TANGENTE_VULCAN = "Tangente Vulcan"

# Registro método -> clase de motor
_MOTORES = {
    METODO_LEAPFROG: DesurveyLeapfrog,
    METODO_TANGENTE_VULCAN: DesurveyTangenteVulcan,
}

# Coordenadas que el desurvey agrega a una tabla intervalar: inicio, fin y punto medio.
COLUMNAS_DESURVEY_INTERVALO = [
    'X_START', 'Y_START', 'Z_START',
    'X_END', 'Y_END', 'Z_END',
    'X_MID', 'Y_MID', 'Z_MID',
]


def crear_desurvey(metodo=METODO_LEAPFROG):
    """
    Fábrica de motores de desurvey.

    :param metodo: uno de METODO_LEAPFROG o METODO_TANGENTE_VULCAN.
    :return: instancia del motor de desurvey correspondiente.
    """
    try:
        return _MOTORES[metodo]()
    except KeyError:
        disponibles = ', '.join(_MOTORES)
        raise ValueError(f"Método de desurvey desconocido: {metodo!r}. Disponibles: {disponibles}.")


def desurveyar_intervalos(df_collar, df_survey, df_intervalos,
                          metodo=METODO_TANGENTE_VULCAN, step=1.0):
    """
    Devuelve una copia de ``df_intervalos`` con las coordenadas XYZ de inicio, fin y centro.

    Conserva todas las filas y su orden original. Los intervalos cuyo sondaje no pueda procesarse
    (por ejemplo, porque no tiene Collar o Survey) también se conservan, con las nueve coordenadas
    vacías. El identificador auxiliar evita depender del índice que traiga el DataFrame.

    Esta función es la entrada reutilizable para exportaciones: usa exactamente los mismos motores
    que :class:`FmDesurvey`, pero no depende de Qt ni de archivos CSV.
    """
    df_salida = df_intervalos.copy()
    if df_salida.empty:
        for columna in COLUMNAS_DESURVEY_INTERVALO:
            df_salida[columna] = pd.Series(dtype='float64')
        return df_salida

    marcador = '__orden_original_desurvey__'
    while marcador in df_salida.columns:
        marcador = '_' + marcador

    df_motor = df_salida.copy()
    df_motor[marcador] = range(len(df_motor))
    _, resultados, _ = crear_desurvey(metodo).calcular_sondajes(
        df_collar, df_survey, df_motor, pd.DataFrame(), step=step)

    partes = [df for df in (resultados or {}).values() if df is not None and not df.empty]
    if partes:
        procesados = pd.concat(partes, ignore_index=True)
        coordenadas = (procesados.set_index(marcador)[COLUMNAS_DESURVEY_INTERVALO]
                        .reindex(range(len(df_salida))))
    else:
        coordenadas = pd.DataFrame(
            float('nan'), index=range(len(df_salida)), columns=COLUMNAS_DESURVEY_INTERVALO)

    for columna in COLUMNAS_DESURVEY_INTERVALO:
        df_salida[columna] = coordenadas[columna].to_numpy()
    return df_salida


# Alias retrocompatible: el código existente instancia `Desurvey()` esperando el motor Leapfrog.
Desurvey = DesurveyLeapfrog

__all__ = [
    'MotorDesurvey',
    'DesurveyLeapfrog',
    'DesurveyTangenteVulcan',
    'METODO_LEAPFROG',
    'METODO_TANGENTE_VULCAN',
    'COLUMNAS_DESURVEY_INTERVALO',
    'crear_desurvey',
    'desurveyar_intervalos',
    'Desurvey',
]
