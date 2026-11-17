"""
Nombre : DesurveyLeapfrog.py
Propósito: Motor de desurvey estilo Leapfrog Geo. Interpola linealmente el azimut y el dip
           entre estaciones de survey e integra la traza con el método tangencial balanceado
           (promedio de las direcciones al inicio y al final de cada tramo). Es el motor por
           defecto del proyecto y su salida está validada como idéntica a la implementación
           original (ver _bench/run_bench.py).
Creacion : 08-Julio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
"""

import numpy as np

from Herramientas.MotorDesurvey import MotorDesurvey


class DesurveyLeapfrog(MotorDesurvey):
    """
    Desurvey por método tangencial balanceado, muy similar al que usa Leapfrog Geo.

    Cada tramo aporta un desplazamiento igual a la longitud media (largo/2) por la suma de
    las componentes direccionales evaluadas al inicio y al final del tramo. El azimut y el dip
    se interpolan linealmente entre estaciones antes de integrar.
    """

    def _integrar_trayectoria(self, trace, survey, start_x, start_y, start_z):
        # El método balanceado trabaja sobre los ángulos ya remuestreados en `trace`; no usa
        # las estaciones crudas de `survey`.
        depths = trace['Profundidad'].to_numpy(float)
        az_rad = np.radians(trace['Azimut'].interpolate().to_numpy(float))
        dip_rad = np.radians(trace['Dip'].interpolate().to_numpy(float))

        largo = np.empty_like(depths)
        largo[0] = 0.0
        largo[1:] = np.diff(depths)

        az_rad_prev = np.concatenate([[az_rad[0]], az_rad[:-1]])
        dip_rad_prev = np.concatenate([[dip_rad[0]], dip_rad[:-1]])

        term_x = (np.cos(np.abs(dip_rad)) * np.sin(az_rad) + np.cos(np.abs(dip_rad_prev)) * np.sin(az_rad_prev))
        term_y = (np.cos(np.abs(dip_rad)) * np.cos(az_rad) + np.cos(np.abs(dip_rad_prev)) * np.cos(az_rad_prev))
        term_z = np.sin(dip_rad_prev) + np.sin(dip_rad)

        return np.column_stack([start_x + np.cumsum((largo / 2.0) * term_x),
                                start_y + np.cumsum((largo / 2.0) * term_y),
                                start_z + np.cumsum((largo / 2.0) * term_z)])
