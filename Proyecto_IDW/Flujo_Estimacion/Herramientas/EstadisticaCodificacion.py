"""
Nombre : EstadisticaCodificacion.py
Propósito: Motor sin Qt de estadística descriptiva (EDA) de tablas de intervalos de sondajes,
           orientado a la validación de dominios para estimación de recursos. Produce dos vistas:
           (1) estadística por dominio (conteo de tramos y sondajes, longitudes) y (2) estadística
           de leyes por dominio PONDERADA POR LA LONGITUD de cada tramo (media, desviación estándar,
           coeficiente de variación y percentiles ponderados, todos consistentes entre sí), más una
           fila Global. Antes de calcular limpia la variable: mapea los valores sentinela "sin dato"
           (p. ej. -99) a NaN y trata los valores bajo el límite de detección (BDL, ingresados como
           negativos, p. ej. -0.005) según una regla configurable. No depende de Qt ni de matplotlib.
Creacion : 19-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import numpy as np
import pandas as pd

# Tratamiento de los valores bajo el límite de detección (BDL; ingresan como negativos)
BDL_MITAD = 'mitad'        # sustituir por |v|/2 (convención estándar en geoquímica)
BDL_EXCLUIR = 'excluir'    # tratar como sin dato (NaN), se excluye del cálculo
BDL_CERO = 'cero'          # asignar 0
BDL_TAL_CUAL = 'tal_cual'  # dejar el valor negativo sin cambios

# Sentinelas de "sin dato" por defecto (el valor por defecto del módulo de codificación es -99)
VALORES_NULOS_DEFECTO = (-99,)

# Percentiles reportados en la estadística de leyes (porcentajes)
PERCENTILES = (10, 25, 50, 75, 90, 95)

# Columnas de las tablas resultantes
COLS_POR_DOMINIO = ['Dominio', 'N° tramos', 'N° sondajes', 'Long. total', '% Long.',
                    'Long. media', 'Long. mín', 'Long. máx']
COLS_LEYES = ['Dominio', 'n c/ley', 'Media', 'Media pond.', 'Desv. est. pond.', 'CV pond.',
              'Mín', 'P10', 'P25', 'Mediana', 'P75', 'P90', 'P95', 'Máx']

# Etiqueta para los tramos cuyo campo de dominio es nulo (no codificados)
ETIQUETA_SIN_DOMINIO = '(sin dominio)'
ETIQUETA_GLOBAL = 'GLOBAL'


class EstadisticaCodificacion:
    """
    Uso típico:

        est = EstadisticaCodificacion()
        df_dom = est.estadistica_por_dominio(df_intervalo, 'DOMINIO')
        df_ley = est.estadistica_leyes(df_intervalo, 'DOMINIO', 'Cu')
        series = est.valores_por_dominio(df_intervalo, 'DOMINIO', 'Cu')  # para histograma/box-plot

    Las tres operan sobre la tabla de intervalos (requiere las columnas estructurales
    'Sondaje', 'Desde', 'Hasta'). El peso de ponderación es la longitud del tramo (Hasta - Desde).
    """

    # ==============================================================================================
    # Limpieza de la variable (sentinelas "sin dato" + valores BDL) y pesos por longitud
    # ==============================================================================================
    @staticmethod
    def _limpiar_serie(df, variable, valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD):
        """
        Devuelve (v, w): la variable limpia como Series float y los pesos (longitud = Hasta - Desde)
        alineados al índice de 'df'. Pasos:
          1. Coerción a numérico (texto no convertible -> NaN).
          2. Sentinelas 'sin dato' (valores_nulos) -> NaN, antes de cualquier cálculo.
          3. Regla BDL sobre los negativos restantes: mitad (|v|/2), excluir (NaN), cero (0) o
             tal_cual.
        """
        v = pd.to_numeric(df[variable], errors='coerce').astype('float64')

        if valores_nulos:
            v = v.mask(v.isin([float(x) for x in valores_nulos]))

        negativos = v < 0
        if negativos.any():
            if tratamiento_bdl == BDL_MITAD:
                v = v.mask(negativos, v.abs() / 2.0)
            elif tratamiento_bdl == BDL_EXCLUIR:
                v = v.mask(negativos)
            elif tratamiento_bdl == BDL_CERO:
                v = v.mask(negativos, 0.0)
            # BDL_TAL_CUAL: no se modifica

        w = (pd.to_numeric(df['Hasta'], errors='coerce')
             - pd.to_numeric(df['Desde'], errors='coerce')).astype('float64')
        return v, w

    # ==============================================================================================
    # Estadística por dominio (conteos y longitudes)
    # ==============================================================================================
    def estadistica_por_dominio(self, df, campo_dominio):
        """Por cada valor del campo de dominio: n.º de tramos, n.º de sondajes, longitud total y
        su porcentaje, y longitud media/mín/máx. Cierra con una fila Global."""
        if df is None or df.empty or campo_dominio not in df.columns:
            return pd.DataFrame(columns=COLS_POR_DOMINIO)

        largo = (pd.to_numeric(df['Hasta'], errors='coerce')
                 - pd.to_numeric(df['Desde'], errors='coerce')).astype('float64')
        total_largo = float(np.nansum(largo.to_numpy())) if len(largo) else 0.0

        trabajo = pd.DataFrame({
            'dom': df[campo_dominio].values,
            'sondaje': df['Sondaje'].astype(str).values if 'Sondaje' in df.columns else '',
            'largo': largo.values,
        })

        filas = []
        for dom, sub in trabajo.groupby('dom', dropna=False, sort=True):
            filas.append(self._fila_dominio(self._fmt_dom(dom), sub, total_largo))
        filas.append(self._fila_dominio(ETIQUETA_GLOBAL, trabajo, total_largo))
        return pd.DataFrame(filas, columns=COLS_POR_DOMINIO)

    @staticmethod
    def _fila_dominio(etiqueta, sub, total_largo):
        largo = sub['largo'].to_numpy(dtype='float64')
        validos = largo[np.isfinite(largo)]
        long_total = float(validos.sum()) if validos.size else 0.0
        return {
            'Dominio': etiqueta,
            'N° tramos': int(len(sub)),
            'N° sondajes': int(sub['sondaje'].nunique()),
            'Long. total': long_total,
            '% Long.': (long_total / total_largo * 100.0) if total_largo > 0 else np.nan,
            'Long. media': float(validos.mean()) if validos.size else np.nan,
            'Long. mín': float(validos.min()) if validos.size else np.nan,
            'Long. máx': float(validos.max()) if validos.size else np.nan,
        }

    # ==============================================================================================
    # Estadística de leyes por dominio (ponderada por longitud)
    # ==============================================================================================
    def estadistica_leyes(self, df, campo_dominio, variable,
                          valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD):
        """Por cada dominio y para la variable elegida: n, media aritmética, media ponderada por
        longitud, desviación estándar ponderada, CV ponderado, mínimo, percentiles ponderados
        (P10/P25/P50/P75/P90/P95) y máximo. Cierra con una fila Global. Toda la dispersión es
        ponderada por la longitud del tramo para que el CV sea estadísticamente coherente con la
        media ponderada."""
        if df is None or df.empty or campo_dominio not in df.columns or variable not in df.columns:
            return pd.DataFrame(columns=COLS_LEYES)

        v, w = self._limpiar_serie(df, variable, valores_nulos, tratamiento_bdl)
        trabajo = pd.DataFrame({'dom': df[campo_dominio].values,
                                'v': v.values, 'w': w.values})

        filas = []
        for dom, sub in trabajo.groupby('dom', dropna=False, sort=True):
            filas.append(self._fila_leyes(self._fmt_dom(dom),
                                          sub['v'].to_numpy(), sub['w'].to_numpy()))
        filas.append(self._fila_leyes(ETIQUETA_GLOBAL,
                                      trabajo['v'].to_numpy(), trabajo['w'].to_numpy()))
        return pd.DataFrame(filas, columns=COLS_LEYES)

    @classmethod
    def _fila_leyes(cls, etiqueta, valores, pesos):
        st = cls._resumen_ponderado(valores, pesos)
        pp = st['percentiles']
        return {
            'Dominio': etiqueta,
            'n c/ley': st['n'],
            'Media': st['media_arit'],
            'Media pond.': st['media_pond'],
            'Desv. est. pond.': st['desv_pond'],
            'CV pond.': st['cv_pond'],
            'Mín': st['minimo'],
            'P10': pp[10], 'P25': pp[25], 'Mediana': pp[50],
            'P75': pp[75], 'P90': pp[90], 'P95': pp[95],
            'Máx': st['maximo'],
        }

    @classmethod
    def _resumen_ponderado(cls, valores, pesos):
        """Estadística ponderada por longitud de un conjunto de muestras. Solo intervienen las
        muestras con valor finito y longitud finita positiva (la ponderación requiere longitud)."""
        valores = np.asarray(valores, dtype='float64')
        pesos = np.asarray(pesos, dtype='float64')
        validos = np.isfinite(valores) & np.isfinite(pesos) & (pesos > 0)
        v = valores[validos]
        w = pesos[validos]
        n = int(v.size)

        vacio = {p: np.nan for p in PERCENTILES}
        if n == 0:
            return {'n': 0, 'media_arit': np.nan, 'media_pond': np.nan, 'desv_pond': np.nan,
                    'cv_pond': np.nan, 'minimo': np.nan, 'maximo': np.nan, 'percentiles': vacio}

        sw = w.sum()
        media_arit = float(v.mean())
        media_pond = float(np.dot(w, v) / sw)

        # Varianza ponderada con denominador insesgado de 'reliability weights':
        #   sigma^2 = Σw(v - media_pond)^2 / (Σw - Σw^2/Σw)
        denom = sw - (np.dot(w, w) / sw)
        if denom > 0:
            var_pond = float(np.dot(w, (v - media_pond) ** 2) / denom)
            desv_pond = float(np.sqrt(var_pond)) if var_pond > 0 else 0.0
        else:
            desv_pond = 0.0  # una sola muestra (o todo el peso en una): dispersión nula
        cv_pond = float(desv_pond / media_pond) if media_pond != 0 else np.nan

        qs = np.array(PERCENTILES, dtype='float64') / 100.0
        valores_pp = cls._percentil_ponderado(v, w, qs)
        percentiles = {p: float(valores_pp[i]) for i, p in enumerate(PERCENTILES)}

        return {'n': n, 'media_arit': media_arit, 'media_pond': media_pond,
                'desv_pond': desv_pond, 'cv_pond': cv_pond,
                'minimo': float(v.min()), 'maximo': float(v.max()),
                'percentiles': percentiles}

    @staticmethod
    def _percentil_ponderado(valores, pesos, qs):
        """Percentiles ponderados por longitud con interpolación lineal (método Hazen / tipo-5).
        numpy 2.x solo admite percentiles ponderados con method='inverted_cdf' (escalonado), por lo
        que se implementa la versión interpolada. 'qs' son fracciones en [0, 1]. Devuelve un array
        del mismo largo que 'qs' (NaN si no hay muestras válidas)."""
        qs = np.asarray(qs, dtype='float64')
        valores = np.asarray(valores, dtype='float64')
        pesos = np.asarray(pesos, dtype='float64')
        validos = np.isfinite(valores) & np.isfinite(pesos) & (pesos > 0)
        v = valores[validos]
        w = pesos[validos]
        if v.size == 0:
            return np.full(qs.shape, np.nan)
        if v.size == 1:
            return np.full(qs.shape, v[0])

        orden = np.argsort(v, kind='mergesort')
        v = v[orden]
        w = w[orden]
        cumw = np.cumsum(w)
        # Posiciones de graficado en [0, 1] centradas en cada muestra (plotting positions de Hazen).
        pp = (cumw - 0.5 * w) / cumw[-1]
        return np.interp(qs, pp, v)  # np.interp clampa en los extremos

    # ==============================================================================================
    # Estadística combinada (por dominio + leyes en una sola tabla)
    # ==============================================================================================
    def estadistica_combinada(self, df, campo_dominio, variable=None,
                              valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD):
        """Une en una sola tabla la estadística por dominio (conteos y longitudes) y la de leyes de
        la variable elegida, alineadas por dominio (incluida la fila Global). Si 'variable' no se
        indica o no es numérica, devuelve solo la estadística por dominio. Las columnas estructurales
        van primero y las de leyes después; no hay colisión de nombres."""
        df_dom = self.estadistica_por_dominio(df, campo_dominio)
        if (variable and df is not None and not df.empty and variable in df.columns
                and pd.api.types.is_numeric_dtype(df[variable])):
            df_ley = self.estadistica_leyes(df, campo_dominio, variable,
                                            valores_nulos, tratamiento_bdl)
            return df_dom.merge(df_ley, on='Dominio', how='left')
        return df_dom

    # ==============================================================================================
    # Valores limpios por dominio (para histograma / box-plot)
    # ==============================================================================================
    def valores_por_dominio(self, df, campo_dominio, variable,
                            valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD,
                            excluir_dominios=None):
        """Devuelve {etiqueta_dominio: ndarray de valores limpios sin NaN}, listo para graficar.
        Mantiene el orden ascendente de los dominios.

        'excluir_dominios': colección de códigos de dominio sentinela "sin dato" (p. ej. -99, -999)
        que corresponden a tramos no codificados, no a un dominio geológico real; sus grupos se
        omiten para que no aparezcan como una serie en el histograma/box-plot. La comparación es por
        etiqueta formateada, así que funciona tanto si el campo de dominio es numérico como de texto
        (p. ej. el float -99.0 y la cadena '-99' coinciden)."""
        if df is None or df.empty or campo_dominio not in df.columns or variable not in df.columns:
            return {}

        v, _ = self._limpiar_serie(df, variable, valores_nulos, tratamiento_bdl)
        trabajo = pd.DataFrame({'dom': df[campo_dominio].values, 'v': v.values})

        excluir = {self._fmt_dom(x) for x in (excluir_dominios or ())}
        series = {}
        for dom, sub in trabajo.groupby('dom', dropna=False, sort=True):
            etiqueta = self._fmt_dom(dom)
            if etiqueta in excluir:
                continue
            datos = sub['v'].to_numpy(dtype='float64')
            datos = datos[np.isfinite(datos)]
            if datos.size:
                series[etiqueta] = datos
        return series

    # ==============================================================================================
    # Auxiliares
    # ==============================================================================================
    @staticmethod
    def _fmt_dom(dom):
        """Etiqueta de dominio para mostrar: NaN -> '(sin dominio)'; los float enteros (1.0) se
        muestran sin decimales (1)."""
        if dom is None or (isinstance(dom, float) and np.isnan(dom)):
            return ETIQUETA_SIN_DOMINIO
        try:
            if pd.isna(dom):
                return ETIQUETA_SIN_DOMINIO
        except (TypeError, ValueError):
            pass
        if isinstance(dom, (int, np.integer)):
            return str(int(dom))
        if isinstance(dom, (float, np.floating)) and float(dom).is_integer():
            return str(int(dom))
        return str(dom)
