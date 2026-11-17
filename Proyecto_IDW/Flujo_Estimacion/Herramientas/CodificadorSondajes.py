"""
Nombre : CodificadorSondajes.py
Propósito: Motor de codificación de tramos de sondajes contra sólidos de triangulación (.obj),
           equivalente al "flag drill holes" de Maptek Vulcan: calcula el punto medio de cada
           intervalo (Desde+Hasta)/2, lo georreferencia con el motor Desurvey y, si cae dentro
           de un sólido, asigna el valor configurado en el campo destino. Ante conflicto entre
           sólidos prevalece la MAYOR prioridad (5 > 1, igual que Maptek Vulcan) y, en empate,
           la fila más abajo del grid.
           El test punto-en-sólido usa Fast Winding Number (libigl) como CodificadorEspacialV2,
           con dos refinamientos de precisión: recentrado de coordenadas (condicionamiento
           numérico en UTM) y re-evaluación con winding number exacto en la banda |FWN-0.5|<=eps.
           No depende de Qt.
Creacion : 12-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import igl

from Herramientas.Desurvey import crear_desurvey, METODO_TANGENTE_VULCAN
from Herramientas.CodificadorEspacialV2 import CodificadorEspacialV2

# Tratamiento de los tramos no codificados
MODO_MANTENER = 'mantener'   # conservar los valores originales del campo (NaN si el campo es nuevo)
MODO_DEFECTO = 'defecto'     # asignar el valor por defecto


class CodificadorSondajes:
    """
    Uso típico:

        motor = CodificadorSondajes()
        df_out, resumen = motor.codificar(df_collar, df_survey, df_intervalo,
                                          solidos=[{'ruta': 'veta.obj', 'valor': '1',
                                                    'prioridad': 1, 'orden': 0}],
                                          campo_destino='DOMINIO')

    'df_out' es la tabla de intervalos con el campo destino agregado/actualizado y 'resumen'
    un dict con los conteos del proceso (por sólido, sin codificar, colisiones, sin XYZ).
    """

    def codificar(self, df_collar, df_survey, df_intervalo, solidos, campo_destino,
                  modo_no_codificado=MODO_DEFECTO, valor_defecto='-99',
                  tolerancia_pared=0.0, eps_winding=0.01):
        t0 = time.perf_counter()
        df = df_intervalo.reset_index(drop=True)
        n = len(df)
        print('=' * 70)
        print(f'  Codificación de sondajes: {n:,} tramos × {len(solidos)} sólido(s)')

        # 1. Puntos medios georreferenciados (desurvey). NaN = tramo no georreferenciable.
        t_mid = time.perf_counter()
        mids = self._puntos_medios(df_collar, df_survey, df)
        validos = ~np.isnan(mids).any(axis=1)
        n_sin_xyz = int((~validos).sum())
        sondajes_sin_xyz = sorted(set(df.loc[~validos, 'Sondaje'].astype(str))) if n_sin_xyz else []
        print(f'  Desurvey de puntos medios: {int(validos.sum()):,} tramos con XYZ, '
              f'{n_sin_xyz:,} sin XYZ [{time.perf_counter() - t_mid:.2f}s]')

        # 2. Test punto-en-sólido por prioridad con candado de asignación exclusiva:
        #    MAYOR prioridad prevalece (5 > 1, como Vulcan); en empate, la fila más abajo del grid.
        #    Procesamos de mayor a menor prioridad: el primero que reclama un tramo lo conserva.
        solidos_ord = sorted(solidos, key=lambda s: (-s['prioridad'], -s['orden']))
        asignados = np.zeros(n, dtype=bool)
        valores = np.full(n, None, dtype=object)
        n_colisiones = 0
        por_solido = []

        for s in solidos_ord:
            ruta = Path(s['ruta'])
            print('-' * 50)
            try:
                mesh = CodificadorEspacialV2.load_solid(ruta)
            except Exception as ex:
                raise RuntimeError(f"No se pudo cargar el sólido '{ruta.name}': {ex}") from ex

            inside = self._puntos_en_solido(mids, validos, mesh, tolerancia_pared, eps_winding)
            nuevos = inside & ~asignados
            colisiones = int((inside & asignados).sum())
            n_colisiones += colisiones
            valores[nuevos] = s['valor']
            asignados |= nuevos
            por_solido.append({'nombre': ruta.stem, 'valor': s['valor'],
                               'prioridad': s['prioridad'], 'n': int(nuevos.sum())})
            print(f"  '{ruta.stem}' (prioridad {s['prioridad']}): {int(nuevos.sum()):,} tramos "
                  f"codificados con '{s['valor']}'")
            if colisiones:
                print(f'  ⚠ {colisiones:,} tramos ya asignados a un sólido de mayor (o igual) prioridad.')

        # 3. Serie del campo destino con inferencia de tipo.
        serie, campo_numerico = self._serie_destino(df, campo_destino, asignados, valores,
                                                    [s['valor'] for s in solidos_ord],
                                                    modo_no_codificado, valor_defecto)
        df = df.copy()
        df[campo_destino] = serie

        n_cod = int(asignados.sum())
        resumen = {
            'por_solido': por_solido,
            'n_filas': n,
            'n_codificados': n_cod,
            'n_sin_codificar': n - n_cod,
            'n_colisiones': n_colisiones,
            'n_sin_xyz': n_sin_xyz,
            'sondajes_sin_xyz': sondajes_sin_xyz,
            'campo_numerico': campo_numerico,
        }
        print('=' * 70)
        print(f'Codificación completada en {time.perf_counter() - t0:.2f}s: '
              f'{n_cod:,} codificados | {n - n_cod:,} sin codificar | {n_colisiones:,} colisiones')
        return df, resumen

    # ==============================================================================================
    # Puntos medios (desurvey)
    # ==============================================================================================
    @staticmethod
    def _puntos_medios(df_collar, df_survey, df_intervalo):
        """
        Coordenadas XYZ del punto medio de cada intervalo, alineadas posicionalmente con
        df_intervalo (ndarray N×3, NaN donde el sondaje no tiene collar/survey o Desde/Hasta
        es nulo). Reutiliza el motor Desurvey: la tabla de intervalos viaja como 'assay' y las
        profundidades medias se insertan en la traza, por lo que el XYZ del punto medio es exacto.

        Usa el motor de desurvey de Vulcan (curvatura mínima): el "flag drill holes" replica a
        Maptek Vulcan, así que la georreferenciación debe coincidir con la de Vulcan para que la
        codificación reproduzca la suya y sea consistente con la estimación IDW aguas abajo.
        """
        desurvey = crear_desurvey(METODO_TANGENTE_VULCAN)
        _, assays_xyz, _ = desurvey.calcular_sondajes(df_collar, df_survey,
                                                      df_intervalo.copy(), pd.DataFrame())
        partes = [v for v in (assays_xyz or {}).values() if v is not None and not v.empty]
        if partes:
            df_xyz = pd.concat(partes)
            mids = df_xyz[['X_MID', 'Y_MID', 'Z_MID']].reindex(df_intervalo.index)
        else:
            mids = pd.DataFrame(np.nan, index=df_intervalo.index,
                                columns=['X_MID', 'Y_MID', 'Z_MID'])
        return mids.to_numpy(dtype=np.float64)

    # ==============================================================================================
    # Test punto-en-sólido (Fast Winding Number con refinamientos de precisión)
    # ==============================================================================================
    @staticmethod
    def _puntos_en_solido(points, validos, mesh, tolerancia_pared, eps_winding):
        """
        Máscara booleana (N,) de los puntos que caen dentro del sólido. Pipeline:
        filtro AABB -> recentrado de coordenadas -> FWN >= 0.5 -> banda |FWN-0.5| <= eps
        re-evaluada con winding number exacto -> rescate euclidiano tolerancia_pared.
        """
        n = points.shape[0]
        inside = np.zeros(n, dtype=bool)

        # El margen del AABB debe cubrir la tolerancia de pared para no descartar
        # candidatos rescatables pegados a la superficie.
        aabb_mask = CodificadorEspacialV2.aabb_filter(points, mesh, max(0.01, tolerancia_pared))
        aabb_mask &= validos
        idx_cand = np.flatnonzero(aabb_mask)
        print(f'  AABB: {idx_cand.size:,} / {n:,} candidatos')
        if idx_cand.size == 0:
            return inside

        # Recentrado: FWN es invariante a traslación y en coordenadas UTM grandes
        # (Norte ~8.5M) trabajar respecto al centro del sólido mejora el
        # condicionamiento numérico (mismo problema documentado en el modo exacto de V2).
        centro = (mesh.bounds[0] + mesh.bounds[1]) * 0.5
        V = np.ascontiguousarray(mesh.vertices - centro, dtype=np.float64)
        F = np.ascontiguousarray(mesh.faces, dtype=np.int64)
        Q = np.ascontiguousarray(points[idx_cand] - centro, dtype=np.float64)

        winding = igl.fast_winding_number(V, F, Q)
        # >= para no perder puntos exactamente sobre la pared (FWN ~ 0.5).
        raw_inside = winding >= 0.5

        # Refinamiento de precisión: la aproximación jerárquica del FWN tiene ruido en la
        # banda alrededor de 0.5; esos pocos puntos se re-evalúan con el winding number
        # exacto (O(m) por punto, costo despreciable por ser poquísimos).
        if eps_winding > 0 and hasattr(igl, 'winding_number'):
            banda = np.abs(winding - 0.5) <= eps_winding
            if banda.any():
                try:
                    w_exacto = igl.winding_number(V, F, np.ascontiguousarray(Q[banda]))
                    raw_inside[banda] = w_exacto >= 0.5
                    print(f'  ⓘ {int(banda.sum()):,} puntos de frontera re-evaluados con '
                          'winding number exacto.')
                except Exception:
                    pass  # se conserva el resultado del FWN

        # Rescate de puntos medios sobre/muy cerca de la pared (patrón de V2): los sólidos
        # suelen modelarse ajustados a los propios tramos y el punto medio puede quedar
        # exactamente sobre la triangulación.
        if tolerancia_pared > 0:
            outside_mask = ~raw_inside
            if outside_mask.any():
                sq_d, _, _ = igl.point_mesh_squared_distance(
                    np.ascontiguousarray(Q[outside_mask]), V, F)
                near_wall = sq_d <= tolerancia_pared ** 2
                if near_wall.any():
                    outside_idx = np.flatnonzero(outside_mask)
                    raw_inside[outside_idx[near_wall]] = True
                    print(f'  ⓘ {int(near_wall.sum()):,} puntos rescatados por cercanía a la '
                          f'pared (≤ {tolerancia_pared} m).')

        inside[idx_cand[raw_inside]] = True
        return inside

    # ==============================================================================================
    # Construcción de la serie del campo destino
    # ==============================================================================================
    @staticmethod
    def _es_numero(valor):
        try:
            float(str(valor).strip())
            return True
        except (TypeError, ValueError):
            return False

    @classmethod
    def _serie_destino(cls, df, campo, asignados, valores, valores_grid,
                       modo_no_codificado, valor_defecto):
        """
        Devuelve (serie, es_numerica) para el campo destino. Reglas de tipo:
        - Campo existente no numérico -> texto siempre.
        - Campo nuevo o existente numérico -> numérica si todos los valores del grid
          (y el valor por defecto, si aplica) parsean a número; si no, texto.
        - Numérica: float64; Int64 si el campo es nuevo (o entero) y todos los valores
          resultantes son enteros, para que la BD preserve el tipo con nulos.
        """
        n = len(df)
        existe = campo in df.columns
        usa_defecto = (modo_no_codificado == MODO_DEFECTO)

        textos = list(valores_grid) + ([valor_defecto] if usa_defecto else [])
        numerico_posible = all(cls._es_numero(v) for v in textos)
        como_numero = numerico_posible and (not existe or
                                            pd.api.types.is_numeric_dtype(df[campo]))

        if como_numero:
            datos = np.full(n, np.nan, dtype=np.float64)
            if usa_defecto:
                datos[:] = float(valor_defecto)
            elif existe:
                # astype('float64') intermedio: convierte los pd.NA de columnas Int64 en NaN.
                datos = pd.to_numeric(df[campo], errors='coerce').astype('float64').to_numpy()
            if asignados.any():
                datos[asignados] = [float(v) for v in valores[asignados]]

            no_nan = datos[~np.isnan(datos)]
            es_entero = no_nan.size > 0 and bool(np.all(np.mod(no_nan, 1) == 0))
            if es_entero and (not existe or pd.api.types.is_integer_dtype(df[campo])):
                return pd.Series(datos, index=df.index).astype('float64').astype('Int64'), True
            return pd.Series(datos, index=df.index, dtype='float64'), True

        datos = np.full(n, None, dtype=object)
        if usa_defecto:
            datos[:] = str(valor_defecto)
        elif existe:
            orig = df[campo]
            con_dato = orig.notna().to_numpy()
            datos[con_dato] = orig.astype(str).to_numpy()[con_dato]
        if asignados.any():
            datos[asignados] = [str(v) for v in valores[asignados]]
        return pd.Series(datos, index=df.index, dtype=object), False
