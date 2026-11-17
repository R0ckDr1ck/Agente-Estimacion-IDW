"""
Nombre : MotorDesurvey.py
Propósito: Clase base abstracta de los motores de desurvey de sondajes. Contiene todo el
           armazón común (armado de profundidades de interés, remuestreo del survey y
           proyección de coordenadas sobre assay/litología) y delega en cada motor concreto
           únicamente el método de integración direccional de la traza.

           Motores concretos disponibles:
             - DesurveyLeapfrog        : método tangencial balanceado, similar a Leapfrog Geo.
             - DesurveyTangenteVulcan  : método tangente de Maptek Vulcan (pendiente).

Creacion : 23-Febrero-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 08-Julio-2026 - Refactor a arquitectura de motores intercambiables.
"""

import abc

import numpy as np
import pandas as pd


class MotorDesurvey(abc.ABC):
    """
    Base común de los motores de desurvey.

    El flujo (`calcular_sondajes` -> `procesar_sondaje`) es idéntico para todos los métodos:
    se arma la lista de profundidades de interés (paso regular + estaciones de survey +
    Desde/Hasta/Mid de assay y litología), se remuestrea el survey a esas profundidades y,
    una vez calculada la traza XYZ, se proyectan las coordenadas sobre las tablas de intervalos.

    Lo único que cambia entre métodos es cómo se integra la traza a partir de los ángulos
    (azimut/dip) remuestreados. Esa parte se aísla en el método abstracto
    `_integrar_trayectoria`, que cada motor concreto implementa.
    """

    def procesar_sondaje(self, hole_id, collar, survey, assay, litho, step=1.0):
        if collar.empty or survey.empty:
            return None, None, None

        start_x = collar.iloc[0]['Este']
        start_y = collar.iloc[0]['Norte']
        start_z = collar.iloc[0]['Cota'] if pd.notnull(collar.iloc[0]['Cota']) else 0.0

        # Obtener profundidades máximas
        max_depth_survey = survey['Profundidad'].max()
        max_depth_assay = assay['Hasta'].max() if not assay.empty else 0
        max_depth_litho = litho['Hasta'].max() if not litho.empty else 0
        max_depth = max(max_depth_survey, max_depth_assay, max_depth_litho, collar.iloc[0]['Profundidad'])

        base_steps = np.arange(0, max_depth, step)
        survey_depths = survey['Profundidad'].values

        # Arrays para Assay
        assay_from, assay_to, assay_mid = np.array([]), np.array([]), np.array([])
        if not assay.empty:
            assay_from = assay['Desde'].values
            assay_to = assay['Hasta'].values
            assay['Profundidad_Mid'] = (assay['Desde'] + assay['Hasta']) / 2.0
            assay_mid = assay['Profundidad_Mid'].values

        # Arrays para Litología
        litho_from, litho_to, litho_mid = np.array([]), np.array([]), np.array([])
        if not litho.empty:
            litho_from = litho['Desde'].values
            litho_to = litho['Hasta'].values
            litho['Profundidad_Mid'] = (litho['Desde'] + litho['Hasta']) / 2.0
            litho_mid = litho['Profundidad_Mid'].values

        # Concatenar TODAS las profundidades de interés para que la interpolación sea exacta
        new_depths = np.unique(np.concatenate([
            base_steps, survey_depths,
            assay_from, assay_to, assay_mid,
            litho_from, litho_to, litho_mid
        ]))

        trace = pd.DataFrame({'Profundidad': new_depths})
        survey_to_merge = survey[['Profundidad', 'Azimut', 'Dip']].copy()
        survey_to_merge['Profundidad'] = survey_to_merge['Profundidad'].astype(float)
        trace = pd.merge_asof(trace, survey_to_merge, on='Profundidad', direction='backward')

        if pd.isna(trace.iloc[0]['Azimut']):
            trace.loc[0, ['Azimut', 'Dip']] = survey.iloc[0][['Azimut', 'Dip']].values

        # La integración direccional de la traza es lo específico de cada motor.
        depths = trace['Profundidad'].to_numpy(float)
        xyz = self._integrar_trayectoria(trace, survey, start_x, start_y, start_z)

        # Proyectar coordenadas en Assay y Litología
        if not assay.empty:
            self._proyectar_coordenadas(assay, depths, xyz, assay_from, assay_to, assay_mid)
        if not litho.empty:
            self._proyectar_coordenadas(litho, depths, xyz, litho_from, litho_to, litho_mid)

        return np.column_stack([xyz, depths]), assay, litho

    @abc.abstractmethod
    def _integrar_trayectoria(self, trace, survey, start_x, start_y, start_z):
        """
        Integra la traza XYZ del sondaje.

        :param trace: DataFrame ordenado por profundidad con las columnas 'Profundidad',
                      'Azimut' y 'Dip', remuestreado a todas las profundidades de interés. Los
                      ángulos están rellenados hacia atrás (función escalón entre estaciones).
        :param survey: DataFrame de estaciones de survey CRUDAS ('Profundidad', 'Azimut', 'Dip'),
                       ordenado por profundidad. Los motores que necesiten interpolar ángulos o
                       trabajar estación-a-estación (p. ej. curvatura mínima) lo usan directamente.
        :param start_x, start_y, start_z: coordenadas del collar (Este, Norte, Cota).
        :return: arreglo NumPy (N, 3) con las coordenadas Este/Norte/Cota de cada fila de `trace`.

        Cada motor de desurvey concreto implementa aquí su método de integración.
        """
        raise NotImplementedError

    @staticmethod
    def _proyectar_coordenadas(df, depths, xyz, prof_start, prof_end, prof_mid):
        """
        Asigna a 'df' las coordenadas X/Y/Z de inicio (_START), fin (_END) y centro (_MID)
        buscando cada profundidad en la traza con búsqueda binaria. Todas las profundidades
        existen en la traza por construcción (se incluyeron en new_depths), así que la búsqueda
        equivale a un merge exacto; una profundidad sin coincidencia (p. ej. NaN) queda con NaN.
        """
        for sufijo, prof in (('_START', prof_start), ('_END', prof_end), ('_MID', prof_mid)):
            pos = np.clip(np.searchsorted(depths, prof), 0, len(depths) - 1)
            coords = xyz[pos]
            sin_match = depths[pos] != prof
            if sin_match.any():
                coords[sin_match] = np.nan
            df['X' + sufijo] = coords[:, 0]
            df['Y' + sufijo] = coords[:, 1]
            df['Z' + sufijo] = coords[:, 2]

    def calcular_sondajes(self, df_collar, df_survey, df_assay, df_litho, step=1.0):
        collar_indexed = df_collar.set_index('Sondaje')
        survey_grouped = df_survey.sort_values('Profundidad').groupby('Sondaje')

        assay_grouped = df_assay.sort_values('Desde').groupby('Sondaje') if not df_assay.empty else None
        litho_grouped = df_litho.sort_values('Desde').groupby('Sondaje') if not df_litho.empty else None

        hole_ids = collar_indexed.index.unique()
        print(f'Desurvey: procesando {len(hole_ids)} sondajes...')

        resultados_trazas = {}
        resultados_assays = {}
        resultados_lithos = {}

        for num, hid in enumerate(hole_ids, start=1):
            if num % 500 == 0:
                print(f'Desurvey: {num}/{len(hole_ids)} sondajes procesados')
            try:
                datos_collar = collar_indexed.loc[[hid]]
                if hid in survey_grouped.groups:
                    datos_survey = survey_grouped.get_group(hid).copy()
                else:
                    continue

                datos_assay = assay_grouped.get_group(hid).copy() if (
                            assay_grouped and hid in assay_grouped.groups) else pd.DataFrame(columns=df_assay.columns)
                datos_litho = litho_grouped.get_group(hid).copy() if (
                            litho_grouped and hid in litho_grouped.groups) else pd.DataFrame(columns=df_litho.columns)

                points, assay_procesado, litho_procesado = self.procesar_sondaje(hid, datos_collar, datos_survey,
                                                                                 datos_assay, datos_litho, step)

                if points is not None and len(points) >= 2:
                    resultados_trazas[hid] = points
                    if not assay_procesado.empty:
                        resultados_assays[hid] = assay_procesado
                    if not litho_procesado.empty:
                        resultados_lithos[hid] = litho_procesado
            except Exception as e:
                print(f"Error calculando sondaje {hid}: {e}")

        return resultados_trazas, resultados_assays, resultados_lithos
