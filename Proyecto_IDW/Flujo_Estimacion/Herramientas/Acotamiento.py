"""
Nombre : Acotamiento.py
Propósito: Motor sin Qt para el acotamiento (top-cut / capping) de altas leyes sobre la tabla de muestras
           (assay), previo a la compositación. Replica las herramientas de análisis del software Snowden /
           Datamine Supervisor que los geólogos usan para decidir el tope de una población:
             - curva de sensibilidad de la media y del coeficiente de variación (CV) frente al tope,
             - análisis de deciles / "metal en riesgo" con las banderas empíricas de Parrish,
             - puntos del gráfico de probabilidad log (posiciones de Hazen + inversa de la normal),
             - sugerencia de tope por varios métodos (percentil, media+kσ, CV objetivo, metal en riesgo),
             - resumen del efecto del tope (antes/después) y aplicación del tope generando la ley acotada.
           Toda la dispersión (σ, CV) se calcula ESTRICTAMENTE PONDERADA POR LA LONGITUD del tramo,
           reutilizando EstadisticaCodificacion._resumen_ponderado; nunca se usan desviaciones no ponderadas.
           El acotamiento se aplica solo a las leyes válidas: los valores sentinela "sin dato", los nulos y
           los BDL (negativos) se conservan tal cual en la columna acotada.
Creacion : 26-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import numpy as np
import pandas as pd

try:
    from scipy.stats import norm
    _SCIPY = True
except ImportError:  # respaldo si scipy no estuviera disponible
    norm = None
    _SCIPY = False

from Herramientas.EstadisticaCodificacion import (
    EstadisticaCodificacion, BDL_MITAD, VALORES_NULOS_DEFECTO, ETIQUETA_GLOBAL)

# Umbrales empíricos de Parrish (1997) para justificar el corte de altas leyes
PARRISH_DECIL_SUP = 40.0   # % del metal en el decil superior (90-100)
PARRISH_PERC_SUP = 10.0    # % del metal en el percentil superior (99-100)
PARRISH_SALTO = 2.0        # razón metal(99-100) / metal(98-99)

# Métodos de sugerencia de tope
METODO_PERCENTIL = 'percentil'
METODO_MEDIA_SIGMA = 'media_sigma'
METODO_CV_OBJETIVO = 'cv_objetivo'
METODO_METAL_RIESGO = 'metal_riesgo'
METODO_MANUAL = 'manual'

# Bandas de percentil con que se desglosa el decil superior (para el criterio del "salto")
BANDAS_PERCENTIL = [(0.90, 0.95, '90-95'), (0.95, 0.98, '95-98'),
                    (0.98, 0.99, '98-99'), (0.99, 1.00, '99-100')]

COLS_SENSIBILIDAD = ['tope', 'media_pond', 'cv_pond', 'n_afectados', 'pct_muestras', 'pct_metal']
COLS_DECILES = ['banda', 'q_lo', 'q_hi', 'ley_min', 'ley_max', 'n', 'metal', 'pct_metal',
                'pct_metal_acum']


class Acotamiento:
    """
    Uso típico (todas las funciones de análisis operan sobre arrays (v, w) ya limpios y filtrados que
    entrega 'preparar'):

        ac = Acotamiento()
        v, w = ac.preparar(df_assay, 'Cu', 'DOMINIO', '1')
        curva = ac.curva_sensibilidad(v, w)
        deciles, flags = ac.analisis_deciles(v, w)
        leyes, z, prob = ac.puntos_probabilidad(v, w)
        tope = ac.sugerir_tope(v, w, 'percentil', 98)
        efecto = ac.resumen_efecto(v, w, tope)
        df_out, afect = ac.aplicar_topes(df_assay, 'Cu', 'DOMINIO', {'1': 3.0, '2': None}, 'Cu_ACOT')
    """

    # ==============================================================================================
    # Extracción de la variable limpia y el peso (longitud) por dominio
    # ==============================================================================================
    @staticmethod
    def preparar(df, variable, campo_dominio=None, dominio=None,
                 valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD,
                 excluir_dominios=None):
        """Devuelve (v, w): la ley limpia y el peso=longitud como arrays numpy, FILTRADOS a muestras
        válidas (v finito, w finito y > 0). Si se indican 'campo_dominio' y 'dominio' (una etiqueta de
        _fmt_dom), restringe al dominio; con dominio None o 'GLOBAL' usa todas las muestras.

        'excluir_dominios': códigos de dominio sentinela "sin dato" (p. ej. -99, -999) cuyos tramos no
        tienen un dominio geológico real. Solo aplica al caso GLOBAL/None: se omiten esas muestras del
        pooled para no acotar contra material no codificado (en el análisis por dominio no influye,
        porque ese dominio ni siquiera se ofrece)."""
        sub = df
        if campo_dominio is not None and campo_dominio in df.columns:
            etiquetas = df[campo_dominio].map(EstadisticaCodificacion._fmt_dom)
            if dominio not in (None, ETIQUETA_GLOBAL):
                sub = df[etiquetas == dominio]
            elif excluir_dominios:
                excluir = {EstadisticaCodificacion._fmt_dom(x) for x in excluir_dominios}
                sub = df[~etiquetas.isin(excluir)]
        v, w = EstadisticaCodificacion._limpiar_serie(sub, variable, valores_nulos, tratamiento_bdl)
        v = v.to_numpy(dtype='float64')
        w = w.to_numpy(dtype='float64')
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
        return v[ok], w[ok]

    @staticmethod
    def resumen(v, w):
        """Estadística ponderada de la variable (n, media, CV, percentiles…) vía el motor existente."""
        return EstadisticaCodificacion._resumen_ponderado(np.asarray(v, 'float64'),
                                                          np.asarray(w, 'float64'))

    # ==============================================================================================
    # Curva de sensibilidad: media y CV ponderados vs tope
    # ==============================================================================================
    def curva_sensibilidad(self, v, w, n_puntos=50):
        """Para una grilla de topes candidatos (de la mediana al máximo) recalcula sobre min(v, tope)
        la media y el CV PONDERADOS POR LONGITUD, además del nº de muestras afectadas (conteo), su
        porcentaje y el % de metal removido (ponderado). Base de las curvas Media vs tope y CV vs tope."""
        v = np.asarray(v, dtype='float64')
        w = np.asarray(w, dtype='float64')
        if v.size == 0:
            return pd.DataFrame(columns=COLS_SENSIBILIDAD)

        vmax = float(v.max())
        p50 = float(np.percentile(v, 50))
        lo = p50 if p50 < vmax else vmax * 0.5
        topes = np.unique(np.concatenate([np.linspace(lo, vmax, max(2, int(n_puntos))), [vmax]]))

        metal_total = float(np.dot(w, v))
        n = v.size
        filas = []
        for t in topes:
            vc = np.minimum(v, t)
            st = EstadisticaCodificacion._resumen_ponderado(vc, w)
            afect = v > t
            n_af = int(afect.sum())
            metal_rem = float(np.dot(w[afect], (v[afect] - t))) if n_af else 0.0
            filas.append({
                'tope': float(t),
                'media_pond': st['media_pond'],
                'cv_pond': st['cv_pond'],
                'n_afectados': n_af,
                'pct_muestras': (n_af / n * 100.0) if n else np.nan,
                'pct_metal': (metal_rem / metal_total * 100.0) if metal_total > 0 else 0.0,
            })
        return pd.DataFrame(filas, columns=COLS_SENSIBILIDAD)

    # ==============================================================================================
    # Análisis de deciles / metal en riesgo (con banderas de Parrish)
    # ==============================================================================================
    def analisis_deciles(self, v, w):
        """Divide la muestra en 10 deciles de IGUAL PESO (longitud) y calcula la contribución de metal
        de cada uno; desglosa además el decil superior en bandas de percentil (90-95, 95-98, 98-99,
        99-100). Devuelve (df_deciles, flags), donde 'flags' trae los criterios de Parrish y el desglose
        de percentiles. El reparto de peso en los bordes de banda es proporcional (deciles exactos)."""
        v = np.asarray(v, dtype='float64')
        w = np.asarray(w, dtype='float64')
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
        v = v[ok]
        w = w[ok]
        if v.size == 0:
            return pd.DataFrame(columns=COLS_DECILES), {}

        orden = np.argsort(v, kind='mergesort')
        vs = v[orden]
        ws = w[orden]
        sw = float(ws.sum())
        cumw = np.cumsum(ws)
        frac_lo = (cumw - ws) / sw   # fracción de peso acumulado al inicio de cada muestra
        frac_hi = cumw / sw          # fracción de peso acumulado al final de cada muestra
        metal_total = float(np.dot(ws, vs))

        def banda(qa, qb, etq):
            # Reparto proporcional del peso de cada muestra que solapa la banda [qa, qb] de peso acumulado.
            solape = np.clip(np.minimum(frac_hi, qb) - np.maximum(frac_lo, qa), 0.0, None)
            peso = solape * sw
            metal = float(np.dot(peso, vs))
            return {
                'banda': etq, 'q_lo': qa * 100.0, 'q_hi': qb * 100.0,
                'ley_min': float(np.interp(qa, frac_hi, vs)),
                'ley_max': float(np.interp(qb, frac_hi, vs)),
                'n': int((solape > 1e-12).sum()),
                'metal': metal,
                'pct_metal': (metal / metal_total * 100.0) if metal_total > 0 else 0.0,
            }

        filas = []
        acum = 0.0
        for i in range(10):
            qa, qb = i / 10.0, (i + 1) / 10.0
            f = banda(qa, qb, f'D{i + 1} ({int(qa * 100)}-{int(qb * 100)})')
            acum += f['pct_metal']
            f['pct_metal_acum'] = acum
            filas.append(f)
        df_dec = pd.DataFrame(filas, columns=COLS_DECILES)

        df_perc = pd.DataFrame([banda(qa, qb, etq) for qa, qb, etq in BANDAS_PERCENTIL])
        m = {row['banda']: row['pct_metal'] for _, row in df_perc.iterrows()}
        pct_decil_sup = float(df_dec.iloc[9]['pct_metal'])
        m_99100 = float(m.get('99-100', 0.0))
        m_9899 = float(m.get('98-99', 0.0))

        flags = {
            'flag_decil_sup': bool(pct_decil_sup > PARRISH_DECIL_SUP),
            'flag_perc_sup': bool(m_99100 > PARRISH_PERC_SUP),
            'flag_salto': bool(m_99100 > PARRISH_SALTO * m_9899) if m_9899 > 0 else bool(m_99100 > 0),
            'pct_decil_sup': pct_decil_sup,
            'pct_perc_sup': m_99100,
            'pct_perc_prev': m_9899,
            'percentiles': df_perc,
        }
        return df_dec, flags

    # ==============================================================================================
    # Puntos del gráfico de probabilidad log (Hazen ponderado + inversa de la normal)
    # ==============================================================================================
    def puntos_probabilidad(self, v, w):
        """Devuelve (leyes_ordenadas, z, prob_acum) para el gráfico de probabilidad. 'prob_acum' usa las
        posiciones de graficado de Hazen ponderadas por longitud; 'z' es su inversa normal estándar
        (norm.ppf), de modo que una población lognormal aparece como recta al graficar z (eje Y) vs ley
        en escala log (eje X). Solo intervienen leyes > 0 (requisito del eje log)."""
        v = np.asarray(v, dtype='float64')
        w = np.asarray(w, dtype='float64')
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0) & (v > 0)
        v = v[ok]
        w = w[ok]
        if v.size == 0:
            return np.array([]), np.array([]), np.array([])

        orden = np.argsort(v, kind='mergesort')
        vs = v[orden]
        ws = w[orden]
        cumw = np.cumsum(ws)
        prob = (cumw - 0.5 * ws) / cumw[-1]          # posiciones de Hazen ponderadas
        prob = np.clip(prob, 1e-6, 1.0 - 1e-6)
        z = self._ppf_normal(prob)
        return vs, z, prob

    @staticmethod
    def _ppf_normal(p):
        """Inversa de la CDF normal estándar. Usa scipy si está disponible; si no, la aproximación
        racional de Acklam (error < 1.15e-9)."""
        p = np.asarray(p, dtype='float64')
        if _SCIPY:
            return norm.ppf(p)
        # Aproximación de Peter Acklam (respaldo sin scipy)
        a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
             1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
        b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
             6.680131188771972e+01, -1.328068155288572e+01]
        c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
             -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
        d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
             3.754408661907416e+00]
        plow, phigh = 0.02425, 1 - 0.02425
        z = np.zeros_like(p)
        lo = p < plow
        hi = p > phigh
        mid = ~(lo | hi)
        if lo.any():
            q = np.sqrt(-2 * np.log(p[lo]))
            z[lo] = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                    ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        if hi.any():
            q = np.sqrt(-2 * np.log(1 - p[hi]))
            z[hi] = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                    ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
        if mid.any():
            q = p[mid] - 0.5
            r = q * q
            z[mid] = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
                     (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
        return z

    # ==============================================================================================
    # Sugerencia de tope por distintos métodos
    # ==============================================================================================
    def sugerir_tope(self, v, w, metodo, parametro=None):
        """Sugiere un valor de tope según el método elegido. Devuelve np.nan si no hay muestras."""
        v = np.asarray(v, dtype='float64')
        w = np.asarray(w, dtype='float64')
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
        v = v[ok]
        w = w[ok]
        if v.size == 0:
            return np.nan

        if metodo == METODO_PERCENTIL:
            p = float(parametro if parametro is not None else 98.0)
            return float(EstadisticaCodificacion._percentil_ponderado(v, w, np.array([p / 100.0]))[0])

        if metodo == METODO_MEDIA_SIGMA:
            k = float(parametro if parametro is not None else 2.5)
            st = EstadisticaCodificacion._resumen_ponderado(v, w)
            return float(st['media_pond'] + k * st['desv_pond'])

        if metodo == METODO_CV_OBJETIVO:
            objetivo = float(parametro if parametro is not None else 1.5)
            curva = self.curva_sensibilidad(v, w, n_puntos=120)
            if curva.empty:
                return float(v.max())
            cand = curva[curva['cv_pond'] <= objetivo]
            if not cand.empty:
                # El tope más alto que ya cumple el CV objetivo: corta lo mínimo necesario.
                return float(cand['tope'].max())
            return float(curva.loc[curva['cv_pond'].idxmin(), 'tope'])

        if metodo == METODO_METAL_RIESGO:
            _, flags = self.analisis_deciles(v, w)
            p = 99.0 if (flags.get('flag_perc_sup') or flags.get('flag_salto')) else 98.0
            return float(EstadisticaCodificacion._percentil_ponderado(v, w, np.array([p / 100.0]))[0])

        return np.nan

    # ==============================================================================================
    # Resumen del efecto del tope (antes / después)
    # ==============================================================================================
    def resumen_efecto(self, v, w, tope):
        """Compara la estadística ponderada antes y después de aplicar el tope, y reporta nº y % de
        muestras afectadas y % de metal removido. Un tope None/NaN equivale a 'sin tope'."""
        v = np.asarray(v, dtype='float64')
        w = np.asarray(w, dtype='float64')
        ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
        v = v[ok]
        w = w[ok]
        base = {'n_afectados': 0, 'pct_muestras': np.nan, 'pct_metal': np.nan,
                'media_antes': np.nan, 'media_despues': np.nan, 'cv_antes': np.nan,
                'cv_despues': np.nan, 'max_antes': np.nan, 'max_despues': np.nan}
        if v.size == 0:
            return base

        st0 = EstadisticaCodificacion._resumen_ponderado(v, w)
        base.update(media_antes=st0['media_pond'], cv_antes=st0['cv_pond'], max_antes=float(v.max()))

        sin_tope = tope is None or (isinstance(tope, float) and tope != tope)
        if sin_tope:
            base.update(media_despues=st0['media_pond'], cv_despues=st0['cv_pond'],
                        max_despues=float(v.max()), n_afectados=0, pct_muestras=0.0, pct_metal=0.0)
            return base

        t = float(tope)
        vc = np.minimum(v, t)
        st1 = EstadisticaCodificacion._resumen_ponderado(vc, w)
        afect = v > t
        n_af = int(afect.sum())
        metal_total = float(np.dot(w, v))
        metal_rem = float(np.dot(w[afect], (v[afect] - t))) if n_af else 0.0
        base.update(media_despues=st1['media_pond'], cv_despues=st1['cv_pond'],
                    max_despues=float(vc.max()), n_afectados=n_af,
                    pct_muestras=n_af / v.size * 100.0,
                    pct_metal=(metal_rem / metal_total * 100.0) if metal_total > 0 else 0.0)
        return base

    # ==============================================================================================
    # Aplicación de los topes: genera la columna de ley acotada
    # ==============================================================================================
    def aplicar_topes(self, df, variable, campo_dominio, topes, campo_salida,
                      valores_nulos=VALORES_NULOS_DEFECTO, tratamiento_bdl=BDL_MITAD):
        """
        Devuelve (df_out, afectados_por_dominio): una copia de 'df' con la columna nueva 'campo_salida'
        = min(ley, tope_del_dominio) SOLO en las muestras de ley válida cuyo dominio tiene tope finito;
        el resto de las filas (otros dominios, sentinelas "sin dato", nulos, BDL) conservan la ley
        original sin tocar. 'topes' es {etiqueta_dominio: tope (float|None)}.
        """
        out = df.copy()
        raw = pd.to_numeric(df[variable], errors='coerce').astype('float64')

        # Ley válida = no sentinela / no "sin dato" (la limpieza mapea sentinelas a NaN). Los BDL siguen
        # siendo válidos: como son < tope, min() no los altera, conservándose tal cual.
        v_clean, _ = EstadisticaCodificacion._limpiar_serie(df, variable, valores_nulos, tratamiento_bdl)
        es_valido = v_clean.notna()

        capped = raw.copy()
        afect_por_dom = {}
        if campo_dominio in df.columns:
            etiquetas = df[campo_dominio].map(EstadisticaCodificacion._fmt_dom)
            for dom, tope in (topes or {}).items():
                if tope is None:
                    continue
                try:
                    t = float(tope)
                except (TypeError, ValueError):
                    continue
                if t != t:  # NaN
                    continue
                recortar = (etiquetas == dom) & es_valido & raw.notna() & (raw > t)
                capped = capped.mask(recortar, t)
                afect_por_dom[dom] = int(recortar.sum())

        out[campo_salida] = capped.to_numpy()
        return out, afect_por_dom
