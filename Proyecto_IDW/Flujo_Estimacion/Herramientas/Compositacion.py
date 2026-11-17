"""
Nombre : Compositacion.py
Propósito: Motor de cálculo de compositación de tramos de sondajes. Replica los métodos de Maptek
           Vulcan: 'Straight' (paso directo, conserva los intervalos originales) y 'Run Length'
           (longitud fija exacta, promedio ponderado por longitud).
Creacion : 01-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import numpy as np
import pandas as pd

# Acciones para el pre-procesamiento (coinciden con los textos de los combos de la IU)
ACCION_IGNORAR = "Ignorar Tramo"
ACCION_FIJO = "Asignar valor fijo"

# Métodos (coinciden con los textos del combo coboMetodo de la IU)
METODO_RUN_LENGTH = "Run Length"
METODO_STRAIGHT = "Straight"

# Tolerancia mínima de longitud para considerar que dos cotas son distintas (evita ruido numérico)
EPS = 1e-9


class Compositacion:
    """
    Motor de compositación. El método público es `compositar`, que recibe la tabla de assay ya
    estandarizada (columnas 'Sondaje', 'Desde', 'Hasta' + campos a compositar) y los parámetros
    configurados en la pestaña Compositación de la interfaz.
    """

    def compositar(self, df_assay, metodo, longitud, tolerancia,
                   campos_num, campos_cat, campo_roca, cortar_geologia,
                   faltante_flags, faltante_accion, faltante_valor,
                   noreg_accion, noreg_valor):
        """
        Ejecuta la compositación sobre la tabla de assay.

        :param df_assay: DataFrame con 'Sondaje', 'Desde', 'Hasta' y los campos a compositar.
        :param metodo: 'Straight' o 'Run Length'.
        :param longitud: Longitud fija del compósito (solo Run Length).
        :param tolerancia: Tolerancia de longitud (regla Merge del residuo, solo Run Length).
        :param campos_num: Lista de campos numéricos a compositar (promedio ponderado).
        :param campos_cat: Lista de campos categóricos a registrar (código mayoritario + %).
        :param campo_roca: Campo de código de roca usado para cortar por geología (o None).
        :param cortar_geologia: Si True, los compósitos no cruzan límites de geología (Run Length).
        :param faltante_flags: Lista de valores que representan dato faltante (ej. [-99]).
        :param faltante_accion: ACCION_IGNORAR o ACCION_FIJO.
        :param faltante_valor: Valor fijo a asignar a los faltantes (si ACCION_FIJO).
        :param noreg_accion: ACCION_IGNORAR o ACCION_FIJO (huecos no registrados).
        :param noreg_valor: Valor fijo a asignar a los huecos (si ACCION_FIJO).
        :return: DataFrame con los intervalos compositados.
        """
        campos_num = list(campos_num or [])
        campos_cat = list(campos_cat or [])

        if df_assay is None or df_assay.empty:
            return pd.DataFrame(columns=['Sondaje', 'Desde', 'Hasta'] + campos_num + campos_cat)

        df = self._preparar(df_assay, campos_num, campos_cat, campo_roca,
                            faltante_flags, faltante_accion, faltante_valor)

        faltante_fijo = (faltante_accion == ACCION_FIJO)  # noqa: F841 (ya aplicado en _preparar)
        noreg_fijo = (noreg_accion == ACCION_FIJO)

        if metodo == METODO_STRAIGHT:
            return self._straight(df, campos_num, campos_cat, noreg_fijo, noreg_valor)
        else:
            return self._run_length(df, longitud, tolerancia, campos_num, campos_cat,
                                    campo_roca, cortar_geologia, noreg_fijo, noreg_valor)

    # ------------------------------------------------------------------------------------------
    # Pre-procesamiento común
    # ------------------------------------------------------------------------------------------
    def _preparar(self, df_assay, campos_num, campos_cat, campo_roca,
                  faltante_flags, faltante_accion, faltante_valor):
        """
        Copia la tabla, fuerza tipos y aplica el tratamiento de datos faltantes a los campos
        numéricos: los valores marcados como faltantes se reemplazan por el valor fijo
        (ACCION_FIJO) o por NaN (ACCION_IGNORAR, así quedan excluidos del promedio ponderado).
        """
        df = df_assay.copy()
        df['Sondaje'] = df['Sondaje'].astype(str).str.strip()
        df['Desde'] = pd.to_numeric(df['Desde'], errors='coerce')
        df['Hasta'] = pd.to_numeric(df['Hasta'], errors='coerce')

        faltante_fijo = (faltante_accion == ACCION_FIJO)
        flags = [f for f in (faltante_flags or []) if f is not None]

        for c in campos_num:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
                if flags:
                    mask = df[c].isin(flags)
                    df.loc[mask, c] = (faltante_valor if faltante_fijo else np.nan)

        # Campos categóricos: dejarlos como están (se manejan los nulos al calcular la mayoría)
        for c in set(campos_cat + ([campo_roca] if campo_roca else [])):
            if c in df.columns:
                df[c] = df[c].astype(object)

        # Descartar filas sin intervalo válido y ordenar
        df = df.dropna(subset=['Desde', 'Hasta'])
        df = df.sort_values(['Sondaje', 'Desde']).reset_index(drop=True)
        return df

    # ------------------------------------------------------------------------------------------
    # Método Straight (paso directo)
    # ------------------------------------------------------------------------------------------
    def _straight(self, df, campos_num, campos_cat, noreg_fijo, noreg_valor):
        """
        Conserva los intervalos originales (sin promediar). Solo se aplica el pre-procesamiento ya
        realizado y, opcionalmente, el relleno de huecos no registrados con un valor fijo.
        """
        columnas = ['Sondaje', 'Desde', 'Hasta'] + campos_num + campos_cat
        columnas = [c for c in columnas if c in df.columns]
        out = df[columnas].copy()

        if noreg_fijo:
            filas_gap = []
            for hid, dfh in out.groupby('Sondaje'):
                dfh = dfh.sort_values('Desde')
                hasta_prev = dfh['Hasta'].to_numpy(float)[:-1]
                desde_next = dfh['Desde'].to_numpy(float)[1:]
                for h_prev, d_next in zip(hasta_prev, desde_next):
                    if d_next - h_prev > EPS:
                        fila = {'Sondaje': hid, 'Desde': h_prev, 'Hasta': d_next}
                        for c in campos_num:
                            fila[c] = noreg_valor
                        for c in campos_cat:
                            fila[c] = None
                        filas_gap.append(fila)
            if filas_gap:
                out = pd.concat([out, pd.DataFrame(filas_gap)], ignore_index=True)

        out = out.sort_values(['Sondaje', 'Desde']).reset_index(drop=True)
        # Columna de longitud del compósito (inmediatamente después de 'Hasta')
        out.insert(3, 'length', out['Hasta'] - out['Desde'])
        return out

    # ------------------------------------------------------------------------------------------
    # Método Run Length (longitud fija)
    # ------------------------------------------------------------------------------------------
    def _run_length(self, df, longitud, tolerancia, campos_num, campos_cat,
                    campo_roca, cortar_geologia, noreg_fijo, noreg_valor):
        """
        Implementación vectorizada del método de longitud fija: todas las celdas de todos los
        sondajes se calculan de una sola vez con numpy (sin bucles por sondaje ni por celda),
        lo que con tablas grandes es órdenes de magnitud más rápido que el cálculo celda a celda.
        """
        L = float(longitud)
        # Tolerancia de la regla Merge: longitud ABSOLUTA (igual que Vulcan). Un residuo cuya
        # longitud sea menor que esta tolerancia se fusiona con el compósito previo.
        tol = float(tolerancia)
        cortar = bool(cortar_geologia) and bool(campo_roca) and campo_roca in df.columns
        agregar_roca = cortar and (campo_roca not in campos_cat)

        columnas_vacias = ['Sondaje', 'Desde', 'Hasta', 'length'] + campos_num
        n = len(df)
        if n == 0 or L <= 0:
            return pd.DataFrame(columns=columnas_vacias)

        # Extracción única a numpy: el df llega de _preparar ordenado por Sondaje/Desde, así que
        # cada sondaje es un bloque contiguo ya ordenado por profundidad.
        sondajes = df['Sondaje'].to_numpy()
        desde = df['Desde'].to_numpy(float)
        hasta = df['Hasta'].to_numpy(float)
        codigos = df[campo_roca].astype(object).to_numpy() if cortar else None

        # Inicios de segmento. Se inicia un nuevo segmento cuando:
        #   - cambia el sondaje,
        #   - hay un hueco entre muestras consecutivas (desde[i] > hasta[i-1]) y los huecos NO se
        #     rellenan (noreg = Ignorar Tramo): así nunca se generan tramos que no existen en el
        #     assay original. Si se rellenan (Asignar valor fijo), el hueco no rompe y el sondaje
        #     se composita de corrido usando el valor de relleno, o
        #   - cambia el código de roca respecto a la muestra anterior (si se corta por geología).
        romper_en_huecos = not noreg_fijo
        inicio = np.ones(n, dtype=bool)
        inicio[1:] = sondajes[1:] != sondajes[:-1]
        if romper_en_huecos:
            inicio[1:] |= desde[1:] > hasta[:-1] + EPS
        if codigos is not None:
            inicio[1:] |= codigos[1:] != codigos[:-1]

        seg_de_fila = np.cumsum(inicio) - 1                 # segmento al que pertenece cada muestra
        seg_primera = np.flatnonzero(inicio)                # primera muestra de cada segmento
        seg_start = desde[seg_primera]                      # mínimo 'Desde' (ordenado por Desde)
        seg_end = np.maximum.reduceat(hasta, seg_primera)   # máximo 'Hasta' del segmento

        # Celdas de compósito de cada segmento (con la regla Merge ya aplicada)
        celda_seg, celda_a, celda_b, celdas_por_seg, primera_celda = self._celdas_segmentos(
            seg_start, seg_end, L, tol)
        total = len(celda_a)
        if total == 0:
            return pd.DataFrame(columns=columnas_vacias)

        # Pares (celda, muestra) con su longitud de solape
        par_celda, par_fila, par_w = self._solapes(desde, hasta, seg_de_fila, seg_start,
                                                   celdas_por_seg, primera_celda,
                                                   celda_a, celda_b, L)

        longitud_celda = celda_b - celda_a
        cubierto = np.bincount(par_celda, weights=par_w, minlength=total)
        hueco = longitud_celda - cubierto

        # Promedio ponderado por longitud de los campos numéricos. Los NaN (faltantes con acción
        # 'Ignorar Tramo') quedan fuera del numerador y del denominador; si los huecos se rellenan,
        # el tramo no muestreado de la celda aporta el valor fijo con peso igual al hueco.
        valores_num = {}
        for f in campos_num:
            if f not in df.columns:
                valores_num[f] = np.full(total, np.nan)
                continue
            v = df[f].to_numpy(float)[par_fila]
            validos = ~np.isnan(v)
            numerador = np.bincount(par_celda[validos], weights=par_w[validos] * v[validos],
                                    minlength=total)
            denominador = np.bincount(par_celda[validos], weights=par_w[validos], minlength=total)
            if noreg_fijo:
                rellenar = hueco > EPS
                numerador[rellenar] += noreg_valor * hueco[rellenar]
                denominador[rellenar] += hueco[rellenar]
            resultado = np.full(total, np.nan)
            np.divide(numerador, denominador, out=resultado, where=denominador > EPS)
            valores_num[f] = resultado

        # Código mayoritario y porcentaje de los campos categóricos
        valores_cat = {}
        if campos_cat:
            par_prof = desde[par_fila]
            for c in campos_cat:
                if c not in df.columns:
                    valores_cat[c] = (np.full(total, None, dtype=object), np.full(total, np.nan))
                else:
                    valores_cat[c] = self._mayoria_celdas(df[c].astype(object).to_numpy(),
                                                          par_celda, par_fila, par_w, par_prof,
                                                          total, longitud_celda)

        # Celdas vacías: si la celda cayó completamente en un hueco y los huecos no se rellenan,
        # el compósito no se genera.
        mantener = np.ones(total, dtype=bool) if noreg_fijo else (cubierto > EPS)
        if not mantener.any():
            return pd.DataFrame(columns=columnas_vacias)

        datos = {'Sondaje': sondajes[seg_primera][celda_seg][mantener],
                 'Desde': celda_a[mantener],
                 'Hasta': celda_b[mantener],
                 'length': longitud_celda[mantener]}
        for f in campos_num:
            datos[f] = valores_num[f][mantener]
        for c in campos_cat:
            datos[c] = valores_cat[c][0][mantener]
            datos[c + '_PCT'] = valores_cat[c][1][mantener]
        if agregar_roca:
            datos[campo_roca] = codigos[seg_primera][celda_seg][mantener]
        return pd.DataFrame(datos)

    @staticmethod
    def _celdas_segmentos(seg_start, seg_end, L, tol):
        """
        Construye las celdas de compósito de todos los segmentos, ancladas al INICIO de cada
        segmento: seg_start, seg_start+L, ..., seg_end. Todos los compósitos miden L salvo el
        último (el residuo). Anclar al inicio del segmento (en lugar de a múltiplos globales de
        L) evita tramos parciales al comienzo de cada corrida de geología.
        Ej.: seg_start=0, seg_end=7, L=2 -> celdas [0-2, 2-4, 4-6, 6-7].

        Aplica además la regla Merge: si el residuo de un segmento con dos o más celdas es
        ESTRICTAMENTE más corto que la tolerancia, se fusiona con la celda previa (basta con
        eliminar la última celda: la penúltima se alarga hasta seg_end). Se resta EPS para que un
        residuo igual a la tolerancia (ej. 0.75 con tol 0.75) NO se fusione, evitando que el
        ruido de punto flotante lo deje un pelo por debajo del umbral.

        Devuelve (segmento_de_cada_celda, desde_celda, hasta_celda, celdas_por_segmento,
        primera_celda_de_cada_segmento).
        """
        # Cantidad de celdas por segmento: k = 0..m-1 con seg_start + k*L < seg_end - EPS. La
        # división puede errar en +-1 por punto flotante, así que se corrige contra la condición
        # exacta (la misma comparación que usaría un bucle de bordes).
        m = np.maximum(np.ceil((seg_end - seg_start - EPS) / L).astype(np.int64), 0)
        for _ in range(2):
            faltan = seg_start + m * L < seg_end - EPS
            if not faltan.any():
                break
            m[faltan] += 1
        for _ in range(2):
            sobran = (m > 0) & (seg_start + (m - 1) * L >= seg_end - EPS)
            if not sobran.any():
                break
            m[sobran] -= 1

        # Regla Merge: el residuo (última celda) mide seg_end - (seg_start + (m-1)*L)
        fusionar = (m >= 2) & ((seg_end - (seg_start + (m - 1) * L)) < tol - EPS)
        m = m - fusionar.astype(np.int64)

        total = int(m.sum())
        primera_celda = np.concatenate(([0], np.cumsum(m)[:-1]))
        celda_seg = np.repeat(np.arange(len(m)), m)
        k = np.arange(total) - primera_celda[celda_seg]
        celda_a = seg_start[celda_seg] + k * L
        ultima = (k == m[celda_seg] - 1)
        celda_b = np.where(ultima, seg_end[celda_seg], seg_start[celda_seg] + (k + 1) * L)
        return celda_seg, celda_a, celda_b, m, primera_celda

    @staticmethod
    def _solapes(desde, hasta, seg_de_fila, seg_start, celdas_por_seg, primera_celda,
                 celda_a, celda_b, L):
        """
        Empareja cada muestra con las celdas de su segmento que puede solapar: desde la celda que
        contiene su 'Desde' hasta la que contiene su 'Hasta', con margen de +-1 celda por
        seguridad de punto flotante (el filtro definitivo es siempre el solape exacto > EPS, el
        mismo criterio del cálculo celda a celda). Devuelve los arreglos paralelos
        (celda, fila_de_muestra, solape) de los pares con solape > EPS.
        """
        m_fila = celdas_por_seg[seg_de_fila]
        filas = np.flatnonzero(m_fila > 0)        # excluye segmentos degenerados sin celdas
        d = desde[filas]
        h = hasta[filas]
        s = seg_start[seg_de_fila[filas]]
        tope = m_fila[filas] - 1
        k_lo = np.clip(np.floor((d - s) / L).astype(np.int64) - 1, 0, tope)
        k_hi = np.clip(np.floor((h - s) / L).astype(np.int64) + 1, 0, tope)

        cuenta = k_hi - k_lo + 1
        total_pares = int(cuenta.sum())
        par_idx = np.repeat(np.arange(len(filas)), cuenta)
        primero = np.concatenate(([0], np.cumsum(cuenta)[:-1]))
        k_par = k_lo[par_idx] + (np.arange(total_pares) - primero[par_idx])

        par_celda = primera_celda[seg_de_fila[filas[par_idx]]] + k_par
        solape = (np.minimum(h[par_idx], celda_b[par_celda])
                  - np.maximum(d[par_idx], celda_a[par_celda]))
        con_solape = solape > EPS
        return par_celda[con_solape], filas[par_idx[con_solape]], solape[con_solape]

    @staticmethod
    def _mayoria_celdas(arr, par_celda, par_fila, par_w, par_prof, total, longitud_celda):
        """
        Determina para cada celda el código mayoritario por longitud de solape y su porcentaje
        sobre la longitud del compósito. En caso de empate gana el código que aparece a menor
        profundidad. Los valores nulos (None/NaN) no votan y los códigos se comparan por su
        representación de texto (str), igual que en el cálculo celda a celda.
        Devuelve (códigos, porcentajes) como arreglos alineados con las celdas.
        """
        codigos_celda = np.full(total, None, dtype=object)
        pct_celda = np.full(total, np.nan)

        ids_fila, unicos = pd.factorize(arr, use_na_sentinel=True)
        if len(unicos) == 0:
            return codigos_celda, pct_celda
        # Unificar códigos distintos con la misma representación textual (p. ej. 1 y '1')
        ids_texto, textos = pd.factorize(np.array([str(u) for u in unicos], dtype=object))
        n_cod = len(textos)

        ids_par = ids_fila[par_fila]
        con_voto = ids_par >= 0
        if not con_voto.any():
            return codigos_celda, pct_celda

        # Acumular peso (solape) y primera profundidad por par (celda, código)
        llave = par_celda[con_voto] * n_cod + ids_texto[ids_par[con_voto]]
        orden = np.argsort(llave, kind='stable')  # estable: conserva el orden de las muestras
        llave = llave[orden]
        corte = np.flatnonzero(np.concatenate(([True], llave[1:] != llave[:-1])))
        peso = np.add.reduceat(par_w[con_voto][orden], corte)
        prof = np.minimum.reduceat(par_prof[con_voto][orden], corte)
        celda_g = llave[corte] // n_cod
        codigo_g = llave[corte] % n_cod

        # Elegir por celda: mayor peso y, ante empate, menor profundidad de primera aparición
        eleccion = np.lexsort((prof, -peso, celda_g))
        celda_e = celda_g[eleccion]
        ganador = eleccion[np.flatnonzero(np.concatenate(([True], celda_e[1:] != celda_e[:-1])))]

        celda_w = celda_g[ganador]
        codigos_celda[celda_w] = textos[codigo_g[ganador]]
        largo = longitud_celda[celda_w]
        con_largo = largo > EPS
        pct_celda[celda_w] = np.where(con_largo,
                                      peso[ganador] / np.where(con_largo, largo, 1.0) * 100.0,
                                      np.nan)
        return codigos_celda, pct_celda
