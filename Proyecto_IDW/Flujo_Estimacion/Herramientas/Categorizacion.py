"""
Nombre : Categorizacion.py
Propósito: Motor sin Qt de categorización de recursos posterior a la estimación. Replica el
           algoritmo `cat_veta_nuevo` del Yupay clásico, con las simplificaciones acordadas para
           YupAI GEO:
             - Categorización SIEMPRE a nivel de bloque padre (el modelo ya viene estimado en
               parent; no se degrada por soporte ni se subdivide).
             - Distancia de clasificación SIEMPRE la "distancia promedio a 3 sondajes": una columna
               ya presente en el modelo de bloques (p. ej. 'd3h_avganis'), calculada previamente por
               el módulo de Estimación con una corrida de 3 sondajes (máx 3 muestras, 1 por sondaje).
             - Parámetros GLOBALES para toda la corrida (una sola terna de distancias), no por veta.
             - Sin suavizado por sólidos LGEO/LINF/LPOT (Fase 2 eliminada) ni degradación por soporte
               (Fase 3) ni limpieza interior de -99 (Fase 2.1).

           El algoritmo (Fase 1 de `cat_veta_nuevo`) asigna a cada bloque una categoría a partir de
           tres campos del modelo YA estimado y cuatro distancias de corte:
             - distancia    = distancia promedio a 3 sondajes  (col_distancia)
             - nº de pasada = pasada/flag de la estimación de ley (col_pass; opcional)
             - nº de sondajes = sondajes usados en la estimación de ley (col_nholes)

           Reglas (con d = distancia, nh = nº de sondajes, pasada <= 3 salvo Potencial):
             - Potencial (4):  0 < d <= dist_potencial                         (sin filtro de pasada)
             - Banda Medido   0 < d <= dist_medido:   nh>=3 -> Medido(1), nh==2 -> Indicado(2),
                                                       nh==1 -> Inferido(3)
             - Banda Indicado dist_medido < d <= dist_indicado:  nh>=2 -> Indicado(2),
                                                                 nh==1 -> Inferido(3)
             - Banda Inferido dist_indicado < d <= dist_inferido: nh>=1 -> Inferido(3)
           El valor por defecto (sin categoría) es Const.VAL_DEFECTO (-99). Las bandas de distancia
           son disjuntas: el Potencial se asigna primero y las bandas de mayor confianza lo
           sobrescriben (réplica exacta de la secuencia de `if` del original).

           No depende de Qt. La interfaz es Herramientas/FmCategorizacion.py.
Creacion : 13-Julio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from Util.Const import Const

# Códigos de categoría (misma convención que Yupay clásico / Vulcan).
CATEG_MEDIDO = 1
CATEG_INDICADO = 2
CATEG_INFERIDO = 3
CATEG_POTENCIAL = 4

# Etiqueta legible por código (para el resumen).
ETIQUETAS_CATEG = {
    CATEG_MEDIDO: 'Medido',
    CATEG_INDICADO: 'Indicado',
    CATEG_INFERIDO: 'Inferido',
    CATEG_POTENCIAL: 'Potencial',
}

# Límite de pasada: los bloques estimados en una pasada posterior a la 3.ª no pueden ser
# Medido/Indicado/Inferido (sí Potencial). Réplica del `_pass <= 3` del original.
PASADA_MAXIMA = 3


@dataclass
class ParametrosCategorizacion:
    """Parámetros GLOBALES de una corrida de categorización.

    - col_distancia: columna del modelo con la distancia promedio a 3 sondajes.
    - col_nholes   : columna con el nº de sondajes de la estimación de ley.
    - col_pass     : columna con la pasada/flag de la estimación de ley. Opcional: si queda vacía,
                     no se aplica el filtro de pasada (equivale a pasada siempre <= 3).
    - dist_medido / dist_indicado / dist_inferido / dist_potencial: distancias de corte (deben ser
      > 0; lo habitual es que sean ascendentes, pero no se obliga: si no lo son, se avisa).
    - col_categoria: nombre de la columna de salida (por defecto 'categ').
    - valor_defecto: valor de "sin categoría" (por defecto Const.VAL_DEFECTO).
    """
    col_distancia: str
    col_nholes: str
    dist_medido: float
    dist_indicado: float
    dist_inferido: float
    dist_potencial: float
    col_pass: str = ''
    col_categoria: str = 'categ'
    valor_defecto: int = int(Const.VAL_DEFECTO)
    col_anterior: str = ''     # piso: columna con la categoría previa; vacía = sin piso
    sufijo_sin_piso: str = '_sin_piso'

    def __post_init__(self):
        self.col_anterior = str(self.col_anterior or '').strip()
        self.col_distancia = str(self.col_distancia or '').strip()
        self.col_nholes = str(self.col_nholes or '').strip()
        self.col_pass = str(self.col_pass or '').strip()
        self.col_categoria = str(self.col_categoria or '').strip() or 'categ'
        for nombre in ('dist_medido', 'dist_indicado', 'dist_inferido', 'dist_potencial'):
            setattr(self, nombre, float(getattr(self, nombre)))
        try:
            self.valor_defecto = int(self.valor_defecto)
        except (TypeError, ValueError):
            self.valor_defecto = int(Const.VAL_DEFECTO)

        if self.col_anterior and self.col_anterior == self.col_categoria:
            raise ValueError('La columna de categoría anterior (piso) no puede ser la misma que la '
                             f"columna de categoría de salida ('{self.col_categoria}').")
        if not self.col_distancia:
            raise ValueError('Debe indicar la columna de distancia promedio a 3 sondajes.')
        if not self.col_nholes:
            raise ValueError('Debe indicar la columna de número de sondajes.')
        for nombre in ('dist_medido', 'dist_indicado', 'dist_inferido', 'dist_potencial'):
            if getattr(self, nombre) <= 0:
                raise ValueError(f"La distancia '{nombre.replace('dist_', '')}' debe ser mayor "
                                 'que cero.')

    def avisos(self):
        """Avisos no fatales sobre la coherencia de las distancias (deberían ser ascendentes)."""
        avisos = []
        secuencia = [('Medido', self.dist_medido), ('Indicado', self.dist_indicado),
                     ('Inferido', self.dist_inferido), ('Potencial', self.dist_potencial)]
        for (etq_a, va), (etq_b, vb) in zip(secuencia, secuencia[1:]):
            if va > vb:
                avisos.append(f"La distancia de {etq_a} ({va:g}) es mayor que la de {etq_b} "
                              f"({vb:g}); lo esperado es que sean ascendentes "
                              '(Medido <= Indicado <= Inferido <= Potencial).')
        return avisos


class Categorizador:
    """Aplica la categorización de recursos a un modelo de bloques ya estimado.

    Uso típico:

        params = ParametrosCategorizacion(
            col_distancia='d3h_avganis', col_nholes='au_idw_nholes', col_pass='au_idw_pass',
            dist_medido=15, dist_indicado=30, dist_inferido=50, dist_potencial=100)
        df_cat, resumen = Categorizador().categorizar(df_bloques, params,
                                                       ruta_salida='modelo_categorizado.csv')
    """

    def categorizar(self, df_bloques, params, ruta_salida=None, progreso=None):
        """Devuelve (df, resumen). `df` es una copia del modelo con la columna de categoría.
        `progreso` (opcional): callable(fraccion, texto) para la barra de la GUI."""
        t0 = time.perf_counter()
        if not isinstance(params, ParametrosCategorizacion):
            raise ValueError('Se requiere un ParametrosCategorizacion válido.')
        self._validar_columnas(df_bloques, params)

        if progreso:
            progreso(0.1, 'Leyendo campos del modelo…')

        n = len(df_bloques)

        # Distancia promedio a 3 sondajes. Los valores no finitos (no estimados / sentinela) no
        # cumplen '0 < d', así que quedan sin categoría de forma natural.
        d = pd.to_numeric(df_bloques[params.col_distancia], errors='coerce').to_numpy(dtype='float64')

        # Nº de sondajes: entero; los faltantes cuentan como 0 (igual que el flag no estimado).
        nh = pd.to_numeric(df_bloques[params.col_nholes], errors='coerce').fillna(0).to_numpy(dtype='float64')
        nh = np.rint(nh).astype(np.int64)

        # Pasada (opcional): si no se mapea, no se filtra por pasada. Los faltantes cuentan como 0
        # (bloque no estimado en la variable de ley), que satisface pasada <= 3 (réplica del original).
        if params.col_pass:
            npass = pd.to_numeric(df_bloques[params.col_pass], errors='coerce').fillna(0).to_numpy(dtype='float64')
            pass_ok = np.rint(npass).astype(np.int64) <= PASADA_MAXIMA
        else:
            pass_ok = np.ones(n, dtype=bool)

        if progreso:
            progreso(0.5, 'Clasificando bloques…')

        categ = np.full(n, int(params.valor_defecto), dtype=np.int64)

        # --- Potencial: 0 < d <= dist_potencial (sin filtro de pasada). Se asigna PRIMERO. --------
        categ = np.where((d > 0) & (d <= params.dist_potencial), CATEG_POTENCIAL, categ)

        # --- Banda Medido: 0 < d <= dist_medido (con pasada <= 3) --------------------------------
        banda = (d > 0) & (d <= params.dist_medido) & pass_ok
        categ = np.where(banda & (nh >= 3), CATEG_MEDIDO, categ)
        categ = np.where(banda & (nh == 2), CATEG_INDICADO, categ)
        categ = np.where(banda & (nh == 1), CATEG_INFERIDO, categ)

        # --- Banda Indicado: dist_medido < d <= dist_indicado (con pasada <= 3) -------------------
        banda = (d > params.dist_medido) & (d <= params.dist_indicado) & pass_ok
        categ = np.where(banda & (nh >= 2), CATEG_INDICADO, categ)
        categ = np.where(banda & (nh == 1), CATEG_INFERIDO, categ)

        # --- Banda Inferido: dist_indicado < d <= dist_inferido (con pasada <= 3) ------------------
        banda = (d > params.dist_indicado) & (d <= params.dist_inferido) & pass_ok
        categ = np.where(banda & (nh >= 1), CATEG_INFERIDO, categ)

        df = df_bloques.copy()

        # --- Piso: más información no puede degradar un bloque ---------------------------------
        # Una estimación multipasada se detiene en la primera pasada que reúne las muestras mínimas
        # (no los sondajes mínimos): un sondaje nuevo puede completar ese mínimo en una pasada chica
        # y dejar el bloque con 1-2 sondajes, aunque antes se estimara con ~10. El piso conserva la
        # categoría previa cuando la nueva es peor (código mayor) o no existe.
        n_piso = 0
        if params.col_anterior:
            previa = pd.to_numeric(df_bloques[params.col_anterior], errors='coerce').to_numpy(dtype='float64')
            previa_ok = np.isin(previa, (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL))
            nueva_ok = np.isin(categ, (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL))
            peor = previa_ok & (~nueva_ok | (categ > np.where(previa_ok, previa, 0)))
            df[params.col_categoria + params.sufijo_sin_piso] = categ
            categ = np.where(peor, np.nan_to_num(previa, nan=0.0).astype('int64'), categ)
            n_piso = int(peor.sum())
        df[params.col_categoria] = categ

        if progreso:
            progreso(0.9, 'Escribiendo salida…')
        if ruta_salida:
            df.to_csv(ruta_salida, index=False, encoding='utf-8-sig')
        if progreso:
            progreso(1.0, 'Finalizado.')

        conteo = {codigo: int((categ == codigo).sum())
                  for codigo in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL)}
        n_categorizados = sum(conteo.values())
        resumen = {
            'n_bloques': n,
            'conteo': conteo,
            'n_categorizados': n_categorizados,
            'n_sin_categoria': n - n_categorizados,
            'col_categoria': params.col_categoria,
            'col_anterior': params.col_anterior,
            'n_piso': n_piso,
            'avisos': params.avisos(),
            'ruta_salida': ruta_salida,
        }
        print('=' * 70)
        print(f'Categorización completada en {time.perf_counter() - t0:.2f}s: '
              f'{n_categorizados:,} de {n:,} bloques categorizados '
              f"(M={conteo[CATEG_MEDIDO]:,} I={conteo[CATEG_INDICADO]:,} "
              f"Inf={conteo[CATEG_INFERIDO]:,} Pot={conteo[CATEG_POTENCIAL]:,})")
        return df, resumen

    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _validar_columnas(df_bloques, params):
        columnas = set(df_bloques.columns)
        faltan = []
        if params.col_distancia not in columnas:
            faltan.append(f"distancia a 3 sondajes ('{params.col_distancia}')")
        if params.col_nholes not in columnas:
            faltan.append(f"número de sondajes ('{params.col_nholes}')")
        if params.col_pass and params.col_pass not in columnas:
            faltan.append(f"número de pasada ('{params.col_pass}')")
        if params.col_anterior and params.col_anterior not in columnas:
            faltan.append(f"categoría anterior para el piso ('{params.col_anterior}'; desactive el piso "
                          'si el modelo no la trae)')
        if faltan:
            raise ValueError('El modelo de bloques no tiene la(s) columna(s): '
                             + ', '.join(faltan) + '.')
        if not (pd.to_numeric(df_bloques[params.col_distancia], errors='coerce') > 0).any():
            raise ValueError(f"La columna de distancia a 3 sondajes ('{params.col_distancia}') no tiene "
                             'valores calculados (todos <= 0 o vacíos): ningún bloque puede categorizarse. '
                             'Revise la estimación (paso estimar) y vuelva a correrla.')


# ==================================================================================================
# Resumen legible
# ==================================================================================================
def texto_resumen(resumen):
    """Texto multilínea con el resumen del proceso (para mostrar en la GUI)."""
    lineas = [f"Categorización: {resumen['n_bloques']:,} bloques.", '']
    lineas.append('Bloques por categoría:')
    for codigo in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL):
        n = resumen['conteo'][codigo]
        lineas.append(f"    {codigo} - {ETIQUETAS_CATEG[codigo]}: {n:,} bloques")
    lineas.append('')
    lineas.append(f"Total categorizado: {resumen['n_categorizados']:,} de {resumen['n_bloques']:,} "
                  f"bloques ({resumen['n_sin_categoria']:,} sin categoría).")
    lineas.append(f"Columna de categoría: {resumen['col_categoria']}")
    if resumen.get('col_anterior'):
        lineas.append(f"Piso con '{resumen['col_anterior']}': {resumen.get('n_piso', 0):,} bloques conservan "
                      f"su categoría anterior (la nueva era peor); sin piso en '{resumen['col_categoria']}_sin_piso'.")
    if resumen.get('ruta_salida'):
        lineas.append('')
        lineas.append(f"Archivo CSV: {resumen['ruta_salida']}")
    if resumen.get('avisos'):
        lineas.append('')
        lineas.append('Avisos:')
        for a in resumen['avisos']:
            lineas.append(f"    - {a}")
    return '\n'.join(lineas)
