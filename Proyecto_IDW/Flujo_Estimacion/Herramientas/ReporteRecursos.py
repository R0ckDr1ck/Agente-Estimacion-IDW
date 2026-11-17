"""
Nombre : ReporteRecursos.py
Propósito: Motor sin Qt del Reporte de Recursos (paso final del flujo). Adaptación simplificada de
           Yupay_2025/Scripts/ReporteRecursos/ReporteRecursosPlan.py, con las siguientes decisiones:
             - SIEMPRE recurso sin envolvente (opción 0): sin filtros de recurso/reserva.
             - Sin anchos de recurso/reserva.
             - Parámetros ingresados desde la interfaz (no desde BD SQLite).
             - Solo 2 hojas en el Excel: 'Resumen' (Medido/Indicado/M&I/Inferido) y
               'Resumen_Potencial'.
             - CutOff FIJO (estático), aplicable sobre NSR o sobre Equivalente (seleccionable).
             - Sin excluir por condición.
             - NSR y Equivalente NO se recalculan: se leen de columnas del modelo (las escribe el
               módulo de Valorización).
             - Agrupación por una columna de dominio/veta del modelo (cada valor = una fila del
               resumen).

           El tonelaje se calcula como Densidad * Volumen (Volumen = X·Y·Z Length si no hay columna
           de volumen). Las leyes, NSR y Equivalente se promedian ponderando por tonelaje; los finos
           equivalentes (Onz Equiv) se suman (tonelaje·equiv·factor). El factor de finos convierte a
           Miles Oz (metal principal = Au) o Millones Oz (otros), con paso ppm -> oz (/31.1035) si la
           unidad del principal no está en onzas.

           No depende de Qt. La interfaz es Herramientas/FmReporteRecursos.py.
Creacion : 13-Julio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 02-Noviembre-2026 - Variable de minado opcional (col_minado / tipo_minado): binaria
             (1 minado, 0 no minado) o porcentaje remanente (100 no minado, 0 minado). Los bloques
             minados no se reportan y los parcialmente minados reducen su tonelaje.
Modificado : 02-Noviembre-2026 - Comparación opcional con una categoría anterior del modelo
             (col_categ_anterior): hoja 'Comparacion_Categ' con tonelajes anterior vs actual por categoría y
             por grupo, y matriz de cambio de categoría. Mismos filtros que el resumen.
Modificado : 14-Noviembre-2026 - v7: (1) modelo sin valorizar (valorizado = False): sin columnas NSR / equivalente /
             Onz Equiv y cut-off sobre la ley del primer metal; (2) hoja opcional 'Ganancia_Plan' (ganancia de
             tonelaje por categoría entre la categoría anterior y la calculada con el plan de sondajes, y ratio de
             conversión = ganancia / longitud total del plan).
"""

import os
import time
import datetime
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import xlsxwriter

from Util.Const import Const
from Util.Util import Util

# Códigos y etiquetas de categoría (misma convención que Yupay clásico / Vulcan). El 5 (Medido &
# Indicado) es una fila combinada que se agrega en el cuerpo y los totales del resumen.
CATEG_MEDIDO = 1
CATEG_INDICADO = 2
CATEG_INFERIDO = 3
CATEG_POTENCIAL = 4
CATEG_MI = 5
CATEG_MII = 6                        # Medido + Indicado + Inferido (solo en la hoja de ganancia)
ETIQUETAS_CATEG = {1: 'Medido', 2: 'Indicado', 3: 'Inferido', 4: 'Potencial', 5: 'Medido & Indicado',
                   6: 'M + I + Inferido'}

# Interpretación de la variable de minado del modelo. Binario: 1 = minado, 0 = no minado.
# Porcentaje: porcentaje REMANENTE del bloque, 100 = no minado, 0 = minado (los valores
# intermedios reducen el tonelaje del bloque en proporción).
TIPO_MINADO_BINARIO = 'binario'
TIPO_MINADO_PORCENTAJE = 'porcentaje'

# Constante de conversión de gramos/ppm a onzas troy (idéntica al Yupay clásico).
GRAMOS_POR_ONZA = 31.1035

# Nombres de mes en español (independientes del locale del sistema) para el título y el período.
_MESES_ES = ['', 'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
             'setiembre', 'octubre', 'noviembre', 'diciembre']
_MESES_ABBR = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Set', 'Oct', 'Nov', 'Dic']


def _generar_titulo(fecha_str):
    """'2026-07-13' -> 'al 13 de julio del 2026' (cadena vacía si la fecha no es válida)."""
    try:
        f = datetime.datetime.strptime(str(fecha_str).strip(), '%Y-%m-%d')
        return f'al {f.day} de {_MESES_ES[f.month]} del {f.year}'
    except (TypeError, ValueError):
        return ''


def _generar_periodo(fecha_str):
    """'2026-07-13' -> 'Jul26' (cadena vacía si la fecha no es válida)."""
    try:
        f = datetime.datetime.strptime(str(fecha_str).strip(), '%Y-%m-%d')
        return f'{_MESES_ABBR[f.month]}{f.year % 100:02d}'
    except (TypeError, ValueError):
        return ''


@dataclass
class MetalReporte:
    """Metal/elemento a reportar en las columnas de ley del resumen.

    - columna : columna de ley en el modelo de bloques (p. ej. 'au_idw').
    - etiqueta: encabezado de la columna en el Excel (p. ej. 'Au'). Si se omite, se usa la columna.
    - unidad  : etiqueta de unidad bajo el encabezado (p. ej. 'g/t', 'oz', '%').
    - factor  : factor de conversión aplicado al promedio ponderado (p. ej. ppm -> oz = 0.0321507).
    """
    columna: str
    etiqueta: str = ''
    unidad: str = ''
    factor: float = 1.0

    def __post_init__(self):
        self.columna = str(self.columna or '').strip()
        self.etiqueta = str(self.etiqueta or '').strip() or self.columna
        self.unidad = str(self.unidad or '').strip()
        self.factor = float(self.factor) if str(self.factor).strip() != '' else 1.0


@dataclass
class ParametrosReporte:
    """Parámetros de una corrida de Reporte de Recursos (todos ingresados desde la interfaz)."""
    col_grupo: str                              # columna de agrupación (dominio/veta)
    cutoff: float                               # CutOff fijo
    metales: list = field(default_factory=list)  # lista de MetalReporte
    variable_corte: str = 'nsr'                 # 'nsr' | 'equiv'
    col_categoria: str = 'categ'
    col_densidad: str = 'Densidad'
    col_volumen: str = ''                        # opcional; si vacío se usa X·Y·Z Length
    col_x_len: str = 'X Length'
    col_y_len: str = 'Y Length'
    col_z_len: str = 'Z Length'
    col_nsr: str = 'nsr'
    col_equiv: str = 'equiv'
    metal_principal: str = 'Au'                  # nombre del metal principal (encabezado <Nombre>Eq)
    unidad_principal: str = 'oz'                 # unidad del principal ('oz' -> sin paso a onzas)
    etiqueta_grupo: str = 'Dominio'              # encabezado de la 1.ª columna del resumen
    unidad_minera: str = ''
    fecha_reporte: str = ''                      # 'YYYY-MM-DD' (título y período del archivo)
    etiqueta_precios: str = ''                   # nota de precios en el pie
    col_minado: str = ''                         # variable de minado del modelo ('' = no se considera)
    tipo_minado: str = 'binario'                 # 'binario' (1 minado, 0 no) | 'porcentaje' (100 no minado, 0 minado)
    col_categ_anterior: str = ''                 # categoría anterior del modelo ('' = sin hoja de comparación)
    valorizado: bool = True                      # False: el modelo no trae NSR/equivalente (corte sobre la ley)
    ganancia: bool = False                       # hoja 'Ganancia_Plan' (requiere col_categ_anterior)
    longitud_plan: float = 0.0                   # longitud total del plan de sondajes (m)
    info_plan: dict = None                       # datos del plan (n_sondajes, n_compositos, modo_ley, ...)
    sufijo_ley_anterior: str = '_anterior'       # <columna de ley><sufijo> = ley anterior (opcional)

    def __post_init__(self):
        self.col_grupo = str(self.col_grupo or '').strip()
        self.col_minado = str(self.col_minado or '').strip()
        self.col_categ_anterior = str(self.col_categ_anterior or '').strip()
        tipo = str(self.tipo_minado or 'binario').strip().lower()
        if tipo.startswith(('bin', 'bool')):
            self.tipo_minado = TIPO_MINADO_BINARIO
        elif tipo.startswith(('porc', 'pct', 'perc', '%')):
            self.tipo_minado = TIPO_MINADO_PORCENTAJE
        else:
            raise ValueError(f"Tipo de variable de minado no válido: «{self.tipo_minado}» "
                             "(use 'binario' o 'porcentaje').")
        self.variable_corte = str(self.variable_corte or 'nsr').strip().lower()
        if self.variable_corte not in ('nsr', 'equiv'):
            self.variable_corte = 'nsr'
        self.valorizado = bool(self.valorizado)
        if not self.valorizado:
            self.variable_corte = 'ley'
        self.ganancia = bool(self.ganancia)
        self.longitud_plan = float(self.longitud_plan or 0.0)
        self.info_plan = dict(self.info_plan or {})
        self.sufijo_ley_anterior = str(self.sufijo_ley_anterior or '').strip()
        if self.ganancia and not self.col_categ_anterior:
            raise ValueError('La ganancia del plan de sondajes necesita la columna de categoría anterior '
                             '(comparación con la categoría anterior).')
        self.col_categoria = str(self.col_categoria or 'categ').strip() or 'categ'
        self.col_densidad = str(self.col_densidad or 'Densidad').strip() or 'Densidad'
        self.col_volumen = str(self.col_volumen or '').strip()
        self.col_x_len = str(self.col_x_len or '').strip()
        self.col_y_len = str(self.col_y_len or '').strip()
        self.col_z_len = str(self.col_z_len or '').strip()
        self.col_nsr = str(self.col_nsr or 'nsr').strip() or 'nsr'
        self.col_equiv = str(self.col_equiv or 'equiv').strip() or 'equiv'
        self.metal_principal = str(self.metal_principal or 'Au').strip() or 'Au'
        self.unidad_principal = str(self.unidad_principal or 'oz').strip()
        self.etiqueta_grupo = str(self.etiqueta_grupo or 'Dominio').strip() or 'Dominio'
        self.unidad_minera = str(self.unidad_minera or '').strip() or Const.MINA
        self.fecha_reporte = str(self.fecha_reporte or '').strip()
        self.etiqueta_precios = str(self.etiqueta_precios or '').strip()
        self.cutoff = float(self.cutoff)

        # Normalizar metales (admite MetalReporte o dict).
        metales = []
        for m in (self.metales or []):
            if isinstance(m, MetalReporte):
                metal = m
            elif isinstance(m, dict):
                metal = MetalReporte(columna=m.get('columna', ''), etiqueta=m.get('etiqueta', ''),
                                     unidad=m.get('unidad', ''), factor=m.get('factor', 1.0))
            else:
                raise ValueError('Cada metal debe ser un MetalReporte o un dict.')
            if not metal.columna:
                continue  # fila vacía
            metales.append(metal)
        self.metales = metales

        if not self.col_grupo:
            raise ValueError('Debe indicar la columna de agrupación (dominio/veta).')
        if not self.col_volumen and not (self.col_x_len and self.col_y_len and self.col_z_len):
            raise ValueError('Debe indicar la columna de Volumen o, en su defecto, las tres '
                             'columnas X/Y/Z Length.')

    # -- Factores de finos (Onz Equiv) -----------------------------------------------------------
    @property
    def fact_miles(self):
        return 1000.0 if self.metal_principal.lower() == 'au' else 1000000.0

    @property
    def und_finos(self):
        return 'Miles Oz' if self.metal_principal.lower() == 'au' else 'Millones Oz'

    @property
    def fact_onzas(self):
        return 1.0 if self.unidad_principal.lower() == 'oz' else GRAMOS_POR_ONZA

    @property
    def fact_onz_equiv(self):
        return 1.0 / (self.fact_onzas * self.fact_miles)

    @property
    def col_corte(self):
        if not self.valorizado:
            return self.metales[0].columna if self.metales else ''
        return self.col_nsr if self.variable_corte == 'nsr' else self.col_equiv

    @property
    def encabezado_equiv(self):
        return self.metal_principal.capitalize() + 'Eq'


def _norm_clave_grupo(valor):
    """Clave canónica de un valor de grupo: los numéricos enteros se muestran sin '.0'."""
    if valor is None:
        return ''
    if isinstance(valor, float) and float(valor).is_integer():
        return str(int(valor))
    s = str(valor).strip()
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def _orden_grupo(clave):
    """Ordena los grupos numéricamente cuando es posible, si no alfabéticamente."""
    try:
        return (0, float(clave), '')
    except (TypeError, ValueError):
        return (1, 0.0, str(clave))


class ReporteRecursos:
    """Genera el Reporte de Recursos (2 hojas) a partir de un modelo de bloques valorizado y
    categorizado (CSV)."""

    def __init__(self, params):
        if not isinstance(params, ParametrosReporte):
            raise ValueError('Se requiere un ParametrosReporte válido.')
        self.p = params
        self._minado = None                                         # resumen de la variable de minado
        self._n_categorizados = 0                                   # bloques con categoría 1..4 (tras filtros)
        self.labels = [m.etiqueta for m in params.metales]          # encabezados de metales
        self.cols_valor = self.labels + (['nsr', 'equiv'] if params.valorizado else [])  # promedio ponderado

    # ==============================================================================================
    # Cálculo
    # ==============================================================================================
    def generar(self, df_bloques, ruta_salida, progreso=None):
        """Calcula y escribe el Excel. Devuelve un resumen (dict) para la GUI."""
        t0 = time.perf_counter()
        p = self.p
        self._validar_columnas(df_bloques)

        if progreso:
            progreso(0.1, 'Preparando bloques…')
        df = self._preparar(df_bloques)

        n_categorizados = self._n_categorizados
        if progreso:
            progreso(0.4, f'Aplicando CutOff ({p.variable_corte.upper()} >= {p.cutoff:g})...')
        df = df[df['__corte'] >= p.cutoff]
        df_rep = df[df['__categ'] > 0]       # el resumen solo usa la categoría actual (1..4)
        n_sobre_cutoff = len(df_rep)

        if progreso:
            progreso(0.6, 'Agregando por grupo y categoría…')
        agg = self._agregar(df_rep)

        resumen = self._construir_resumen(agg)
        potencial = self._construir_potencial(agg)
        comparacion = self._construir_comparacion(df) if p.col_categ_anterior else None
        ganancia = self._construir_ganancia(comparacion, df) if p.ganancia and comparacion else None

        if progreso:
            progreso(0.85, 'Escribiendo Excel…')
        self._reportar_excel(resumen, potencial, ruta_salida, comparacion, ganancia)
        if progreso:
            progreso(1.0, 'Finalizado.')

        info = {
            'n_categorizados': n_categorizados,
            'n_sobre_cutoff': n_sobre_cutoff,
            'variable_corte': p.variable_corte,
            'cutoff': p.cutoff,
            'ton_recursos': resumen['totales'][CATEG_MI]['tonelaje'],
            'ton_inferido': resumen['totales'][CATEG_INFERIDO]['tonelaje'],
            'ton_potencial': potencial['total']['tonelaje'],
            'n_grupos_recurso': len(resumen['grupos']),
            'n_grupos_potencial': len(potencial['grupos']),
            'minado': self._minado,
            'comparacion': comparacion,
            'ganancia': ganancia,
            'ruta_salida': ruta_salida,
            'segundos': time.perf_counter() - t0,
        }
        print('=' * 70)
        if self._minado:
            print(texto_minado(self._minado))
        if comparacion:
            print(texto_comparacion(comparacion))
        if ganancia:
            print(texto_ganancia(ganancia))
        print(f"Reporte de Recursos en {info['segundos']:.2f}s: {n_sobre_cutoff:,}/{n_categorizados:,} "
              f"bloques sobre CutOff. Recurso M&I={info['ton_recursos']:,.0f} t, "
              f"Inferido={info['ton_inferido']:,.0f} t, Potencial={info['ton_potencial']:,.0f} t.")
        return info

    def _preparar(self, df_bloques):
        """Copia, filtra por categoría válida, calcula tonelaje, la variable de corte y limpia."""
        p = self.p
        df = df_bloques.copy()

        # Categoría a entero; 1..4 son válidas y todo lo demás (-99, 0, vacío) queda en 0. El resumen solo
        # usa los bloques con categoría actual; con comparación se conservan también los que tenían
        # categoría anterior (para ver lo que ganan o pierden).
        df['__categ'] = _categoria_valida(df[p.col_categoria])
        if p.col_categ_anterior:
            df['__categ_ant'] = _categoria_valida(df[p.col_categ_anterior])
            df = df[(df['__categ'] > 0) | (df['__categ_ant'] > 0)].copy()
        else:
            df = df[df['__categ'] > 0].copy()
        if not (df['__categ'] > 0).any():
            raise ValueError(f"Ningún bloque tiene categoría 1 a 4 en '{p.col_categoria}': el modelo no está "
                             'categorizado (revise la categorización y la distancia a 3 sondajes).')

        # Clave de grupo (dominio/veta); descartar vacíos y numéricos <= 0 (aire/desmonte).
        df['__grupo'] = df[p.col_grupo].map(_norm_clave_grupo)
        df = df[df['__grupo'] != '']
        df = df[~df['__grupo'].map(_es_grupo_invalido).astype(bool)]
        self._n_categorizados = int((df['__categ'] > 0).sum())

        # Tonelaje = Densidad * Volumen.
        densidad = pd.to_numeric(df[p.col_densidad], errors='coerce').fillna(0).clip(lower=0)
        if p.col_volumen:
            volumen = pd.to_numeric(df[p.col_volumen], errors='coerce').fillna(0).clip(lower=0)
        else:
            dx = pd.to_numeric(df[p.col_x_len], errors='coerce').fillna(0)
            dy = pd.to_numeric(df[p.col_y_len], errors='coerce').fillna(0)
            dz = pd.to_numeric(df[p.col_z_len], errors='coerce').fillna(0)
            volumen = (dx * dy * dz).clip(lower=0)
        df['__ton'] = densidad * volumen

        # Variable de minado: el tonelaje se reduce a la parte remanente del bloque y los bloques
        # totalmente minados no se reportan.
        self._minado = None
        if p.col_minado:
            fraccion, _ = fraccion_remanente(df[p.col_minado], p.tipo_minado)
            excluidos = fraccion <= 0
            # Los conteos informados son sobre los bloques con categoría actual (los del resumen).
            cat = df['__categ'] > 0
            vacios = pd.to_numeric(df[p.col_minado], errors='coerce').replace([np.inf, -np.inf], np.nan).isna()
            self._minado = {'variable': p.col_minado, 'tipo': p.tipo_minado,
                            'excluidos': int((excluidos & cat).sum()),
                            'parciales': int((((fraccion > 0) & (fraccion < 1)) & cat).sum()),
                            'invalidos': int((vacios & cat).sum())}
            df['__ton'] = df['__ton'] * fraccion
            df = df[~excluidos].copy()

        # Columnas numéricas de reporte: coercionar, reemplazar inf, recortar negativos a 0.
        columnas_num = [m.columna for m in p.metales] + ([p.col_nsr, p.col_equiv] if p.valorizado else [])
        columnas_num += [c for c in self._columnas_ley_anterior(df)]
        for col in dict.fromkeys(columnas_num):
            serie = pd.to_numeric(df[col], errors='coerce')
            serie = serie.replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0)
            df[col] = serie

        # Variable de corte (NSR o Equivalente).
        df['__corte'] = df[p.col_corte]
        return df

    def _columnas_ley_anterior(self, df):
        """Columnas <ley><sufijo> (p. ej. cu_idw_anterior) presentes en el modelo, solo si se pide la ganancia."""
        p = self.p
        if not (p.ganancia and p.sufijo_ley_anterior and p.metales):
            return []
        col = p.metales[0].columna + p.sufijo_ley_anterior
        return [col] if col in df.columns else []

    def _agregar(self, df):
        """Agrega por (grupo, categoría). Devuelve {(clave_grupo, categ): fila_dict}."""
        p = self.p
        resultado = {}
        if len(df) == 0:
            return resultado
        for (grupo, categ), sub in df.groupby(['__grupo', '__categ']):
            ton = sub['__ton'].to_numpy(dtype='float64')
            fila = {'tonelaje': float(ton.sum())}
            for m in p.metales:
                fila[m.etiqueta] = _wavg(sub[m.columna].to_numpy(dtype='float64'), ton) * m.factor
            if p.valorizado:
                fila['nsr'] = _wavg(sub[p.col_nsr].to_numpy(dtype='float64'), ton)
                fila['equiv'] = _wavg(sub[p.col_equiv].to_numpy(dtype='float64'), ton)
                fila['onz_equiv'] = float((ton * sub[p.col_equiv].to_numpy(dtype='float64')).sum()) \
                    * p.fact_onz_equiv
            else:
                fila['onz_equiv'] = 0.0
            resultado[(grupo, int(categ))] = fila
        return resultado

    def _fila_cero(self):
        fila = {'tonelaje': 0.0, 'onz_equiv': 0.0}
        for col in self.cols_valor:
            fila[col] = 0.0
        return fila

    def _combinar(self, filas):
        """Combina varias filas agregadas: tonelaje y onz_equiv se suman; el resto se promedia
        ponderando por tonelaje."""
        filas = [f for f in filas if f is not None]
        ton = sum(f['tonelaje'] for f in filas)
        fila = {'tonelaje': ton, 'onz_equiv': sum(f['onz_equiv'] for f in filas)}
        for col in self.cols_valor:
            fila[col] = (sum(f['tonelaje'] * f[col] for f in filas) / ton) if ton > 0 else 0.0
        return fila

    def _construir_resumen(self, agg):
        """Resumen de recursos (categorías Medido/Indicado/M&I/Inferido) por grupo + totales."""
        claves = sorted({g for (g, c) in agg if c in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO)},
                        key=_orden_grupo)
        grupos = []
        for g in claves:
            filas = {c: agg.get((g, c), self._fila_cero())
                     for c in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO)}
            filas[CATEG_MI] = self._combinar([filas[CATEG_MEDIDO], filas[CATEG_INDICADO]])
            grupos.append({'grupo': g, 'filas': filas})

        totales = {}
        for c in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO):
            totales[c] = self._combinar([agg[(g, c)] for g in claves if (g, c) in agg])
        totales[CATEG_MI] = self._combinar([totales[CATEG_MEDIDO], totales[CATEG_INDICADO]])
        return {'grupos': grupos, 'totales': totales}

    def _construir_potencial(self, agg):
        """Resumen de potencial (categoría 4) por grupo + total."""
        claves = sorted({g for (g, c) in agg if c == CATEG_POTENCIAL}, key=_orden_grupo)
        grupos = [{'grupo': g, 'fila': agg[(g, CATEG_POTENCIAL)]} for g in claves]
        total = self._combinar([agg[(g, CATEG_POTENCIAL)] for g in claves])
        return {'grupos': grupos, 'total': total}

    def _construir_comparacion(self, df):
        """Compara el tonelaje por categoría anterior (p.col_categ_anterior) con el de la categoría actual
        sobre los mismos bloques (mismos filtros que el resumen: sólido, minado y cut-off). `df` trae
        __categ (actual) y __categ_ant (anterior), 0 = sin categoría. Devuelve:
          - 'totales': {categoría: (anterior, actual)} para Medido, Indicado, M&I, Inferido, Potencial
                       y 'total' (1..4),
          - 'grupos' : [{'grupo', 'filas': {categoría: (anterior, actual)}}] por dominio/veta,
          - 'matriz' : {(anterior, actual): t} con 0 = sin categoría (la celda 0-0 no se calcula)."""
        t_ant = df.groupby('__categ_ant')['__ton'].sum()
        t_act = df.groupby('__categ')['__ton'].sum()
        g_ant = df.groupby(['__grupo', '__categ_ant'])['__ton'].sum()
        g_act = df.groupby(['__grupo', '__categ'])['__ton'].sum()
        cats = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL)

        def pares(ant, act, clave=lambda c: c):
            fila = {c: (float(ant.get(clave(c), 0.0)), float(act.get(clave(c), 0.0))) for c in cats}
            fila[CATEG_MI] = (fila[CATEG_MEDIDO][0] + fila[CATEG_INDICADO][0],
                              fila[CATEG_MEDIDO][1] + fila[CATEG_INDICADO][1])
            fila[CATEG_MII] = (fila[CATEG_MI][0] + fila[CATEG_INFERIDO][0], fila[CATEG_MI][1] + fila[CATEG_INFERIDO][1])
            fila['total'] = (sum(fila[c][0] for c in cats), sum(fila[c][1] for c in cats))
            return fila

        grupos = [{'grupo': g, 'filas': pares(g_ant, g_act, clave=lambda c, g=g: (g, c))}
                  for g in sorted(df['__grupo'].unique(), key=_orden_grupo)]
        matriz = {(int(a), int(c)): float(t)
                  for (a, c), t in df.groupby(['__categ_ant', '__categ'])['__ton'].sum().items()}
        return {'columna': self.p.col_categ_anterior, 'columna_actual': self.p.col_categoria,
                'totales': pares(t_ant, t_act), 'grupos': grupos, 'matriz': matriz,
                'n_bloques': int(len(df))}

    def _factor_fino(self):
        """Factor que lleva (ley del reporte x tonelaje) a toneladas de metal fino según la unidad del primer metal
        (% -> /100; g/t o ppm -> /1e6). None si la unidad no permite calcularlo."""
        if not self.p.metales:
            return None
        unidad = self.p.metales[0].unidad.strip().lower()
        if unidad in ('pct', '%', 'porcentaje'):
            return 0.01
        if unidad in ('g/t', 'gpt', 'ppm'):
            return 1e-6
        return None

    def _construir_ganancia(self, comp, df):
        """Ganancia de recursos por categoría = tonelaje de la categoría calculada - tonelaje de la categoría anterior
        (mismos bloques y filtros que el resumen), y ratio de conversión = ganancia / longitud total del plan de
        sondajes (t por metro). Si el modelo trae la ley anterior (<ley><sufijo>), agrega también el metal fino y la
        ley media. Devuelve {'longitud', 'plan', 'totales', 'fino', 'grupos', ...}."""
        p = self.p
        fino = None
        k = self._factor_fino()
        col_ant = self._columnas_ley_anterior(df)
        if k is not None and col_ant:
            m = p.metales[0]
            df = df.assign(__fino_act=df['__ton'] * df[m.columna] * m.factor * k,
                           __fino_ant=df['__ton'] * df[col_ant[0]] * m.factor * k)
            f_ant = df.groupby('__categ_ant')['__fino_ant'].sum()
            f_act = df.groupby('__categ')['__fino_act'].sum()
            cats = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL)
            fino = {c: (float(f_ant.get(c, 0.0)), float(f_act.get(c, 0.0))) for c in cats}
            fino[CATEG_MI] = (fino[CATEG_MEDIDO][0] + fino[CATEG_INDICADO][0], fino[CATEG_MEDIDO][1] + fino[CATEG_INDICADO][1])
            fino[CATEG_MII] = (fino[CATEG_MI][0] + fino[CATEG_INFERIDO][0], fino[CATEG_MI][1] + fino[CATEG_INFERIDO][1])
            fino['total'] = (sum(fino[c][0] for c in cats), sum(fino[c][1] for c in cats))
        return {'longitud': p.longitud_plan, 'plan': p.info_plan, 'totales': comp['totales'], 'grupos': comp['grupos'],
                'fino': fino, 'metal': p.metales[0].etiqueta if p.metales else '',
                'unidad_ley': p.metales[0].unidad if p.metales else '',
                'columna': comp['columna'], 'columna_actual': comp['columna_actual']}

    # ==============================================================================================
    # Escritura Excel
    # ==============================================================================================
    def _reportar_excel(self, resumen, potencial, ruta_salida, comparacion=None, ganancia=None):
        libro = xlsxwriter.Workbook(ruta_salida, {'nan_inf_to_errors': True})
        try:
            self._hoja_resumen(libro, resumen)
            self._hoja_potencial(libro, potencial)
            if comparacion:
                self._hoja_comparacion(libro, comparacion)
            if ganancia:
                self._hoja_ganancia(libro, ganancia)
        finally:
            libro.close()

    def _formatos(self, libro):
        return {
            'titulo': libro.add_format({'bold': True, 'font_size': 13}),
            'negrita': libro.add_format({'bold': True}),
            'enc_col': libro.add_format({'bold': True, 'bottom': 1, 'align': 'center'}),
            'derecha': libro.add_format({'align': 'right'}),
            'num': libro.add_format({'num_format': '0.00'}),
            'num_t': libro.add_format({'num_format': '#,##0'}),
            'num_b_t': libro.add_format({'num_format': '0.00', 'bold': True, 'top': 1}),
            'num_t_b_t': libro.add_format({'num_format': '#,##0', 'bold': True, 'top': 1}),
            'num_b_b': libro.add_format({'num_format': '0.00', 'bold': True, 'bottom': 1}),
            'num_t_b_b': libro.add_format({'num_format': '#,##0', 'bold': True, 'bottom': 1}),
            'num_b': libro.add_format({'num_format': '0.00', 'bold': True}),
            'num_t_b': libro.add_format({'num_format': '#,##0', 'bold': True}),
            'nota': libro.add_format({'italic': True, 'font_name': 'Arial', 'font_size': 8}),
            'dif': libro.add_format({'num_format': '#,##0;[Red]-#,##0;0'}),
            'pct': libro.add_format({'num_format': '+0.0%;[Red]-0.0%;0.0%', 'align': 'right'}),
            'dif_b': libro.add_format({'num_format': '#,##0;[Red]-#,##0;0', 'bold': True}),
            'pct_b': libro.add_format({'num_format': '+0.0%;[Red]-0.0%;0.0%', 'align': 'right', 'bold': True}),
            'dif_b_t': libro.add_format({'num_format': '#,##0;[Red]-#,##0;0', 'bold': True, 'top': 1}),
            'pct_b_t': libro.add_format({'num_format': '+0.0%;[Red]-0.0%;0.0%', 'align': 'right', 'bold': True,
                                         'top': 1}),
            'txt_b_t': libro.add_format({'bold': True, 'top': 1}),
            'diag': libro.add_format({'num_format': '#,##0', 'bold': True, 'bg_color': '#E8F0E8'}),
            'ratio': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0'}),
            'ratio_b': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0', 'bold': True}),
            'ratio_b_t': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0', 'bold': True, 'top': 1}),
            'ley': libro.add_format({'num_format': '0.000'}),
            'ley_b': libro.add_format({'num_format': '0.000', 'bold': True}),
            'ley_b_t': libro.add_format({'num_format': '0.000', 'bold': True, 'top': 1}),
            'fino': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0'}),
            'fino_b': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0', 'bold': True}),
            'fino_b_t': libro.add_format({'num_format': '#,##0.0;[Red]-#,##0.0;0.0', 'bold': True, 'top': 1}),
            'entero': libro.add_format({'num_format': '#,##0', 'align': 'left'}),
            'dec1': libro.add_format({'num_format': '#,##0.0', 'align': 'left'}),
        }

    def _texto_cutoff(self):
        p = self.p
        if not p.valorizado:
            m = p.metales[0] if p.metales else None
            return f'Cut-off ({m.etiqueta if m else "ley"}): {p.cutoff:g} {m.unidad if m else ""}'.rstrip()
        if p.variable_corte == 'nsr':
            return f'Cut-off: {p.cutoff:g} US$/t'
        return f'Cut-off ({p.encabezado_equiv}): {p.cutoff:g}'

    def _escribir_encabezado(self, hoja, fmt, con_categoria, etiqueta_resumen):
        """Encabezado común de una hoja. Devuelve la fila donde empiezan los datos (7)."""
        p = self.p
        hoja.set_column('A:A', 26)
        hoja.set_column('B:B', 17)
        hoja.set_column('C:S', 12)
        hoja.freeze_panes(7, 0)

        titulo = _generar_titulo(p.fecha_reporte)
        fila = 0
        hoja.write(fila, 0, ('Reporte de Recursos ' + titulo).strip(), fmt['titulo'])
        fila += 1
        hoja.write(fila, 0, 'Unidad: ' + p.unidad_minera, fmt['titulo'])
        fila += 1
        hoja.write(fila, 0, 'Fecha: ' + datetime.date.today().strftime('%d/%m/%Y'))
        fila += 1
        hoja.write(fila, 0, etiqueta_resumen, fmt['negrita'])
        col_cut = (5 if con_categoria else 4) + len(self.labels)
        hoja.write(fila, col_cut, self._texto_cutoff(), fmt['negrita'])

        # Encabezados de columnas.
        encabezados = [p.etiqueta_grupo]
        if con_categoria:
            encabezados.append('Categoria')
        encabezados.append('Tonelaje')
        encabezados.extend(self.labels)
        if p.valorizado:
            encabezados.extend(['NSR', p.encabezado_equiv, 'Onz Equiv'])
        fila += 2
        hoja.write_row(fila, 0, encabezados, fmt['enc_col'])

        # Encabezados de unidades.
        unidades = ['t'] + [m.unidad for m in p.metales]
        if p.valorizado:
            unidades += ['US$/t', p.unidad_principal, p.und_finos]
        fila += 1
        hoja.write_row(fila, 2 if con_categoria else 1, unidades, fmt['derecha'])
        return 7

    def _valores_fila(self, fila_dict):
        """Lista ordenada de valores numéricos: metales + NSR + Equiv + Onz Equiv."""
        valores = [fila_dict[lab] for lab in self.labels]
        if self.p.valorizado:
            valores += [fila_dict['nsr'], fila_dict['equiv'], fila_dict['onz_equiv']]
        return valores

    def _hoja_resumen(self, libro, resumen):
        p = self.p
        fmt = self._formatos(libro)
        hoja = libro.add_worksheet('Resumen')
        fila = self._escribir_encabezado(hoja, fmt, con_categoria=True,
                                         etiqueta_resumen='Resumen Recursos')

        orden = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_MI, CATEG_INFERIDO)
        for grupo in resumen['grupos']:
            for c in orden:
                d = grupo['filas'][c]
                if c == CATEG_MEDIDO:
                    hoja.write(fila, 0, grupo['grupo'])
                hoja.write(fila, 1, ETIQUETAS_CATEG[c])
                hoja.write(fila, 2, d['tonelaje'], fmt['num_t'])
                hoja.write_row(fila, 3, self._valores_fila(d), fmt['num'])
                fila += 1

        # Totales (Medido, Indicado, M&I, Inferido) con bordes superior/inferior.
        for c in orden:
            d = resumen['totales'][c]
            if c == CATEG_MEDIDO:
                f_num, f_ton = fmt['num_b_t'], fmt['num_t_b_t']
                hoja.write(fila, 0, 'Total', f_num)
            elif c == CATEG_INFERIDO:
                f_num, f_ton = fmt['num_b_b'], fmt['num_t_b_b']
                hoja.write(fila, 0, '', f_num)
            else:
                f_num, f_ton = fmt['num_b'], fmt['num_t_b']
            hoja.write(fila, 1, ETIQUETAS_CATEG[c], f_num)
            hoja.write(fila, 2, d['tonelaje'], f_ton)
            for j, val in enumerate(self._valores_fila(d)):
                hoja.write(fila, 3 + j, val, f_num)
            fila += 1

        self._escribir_pie(hoja, fmt, fila, 'recurso')

    def _hoja_potencial(self, libro, potencial):
        p = self.p
        fmt = self._formatos(libro)
        hoja = libro.add_worksheet('Resumen_Potencial')
        fila = self._escribir_encabezado(hoja, fmt, con_categoria=False,
                                         etiqueta_resumen='Resumen Potencial')

        for grupo in potencial['grupos']:
            d = grupo['fila']
            hoja.write(fila, 0, grupo['grupo'])
            hoja.write(fila, 1, d['tonelaje'], fmt['num_t'])
            hoja.write_row(fila, 2, self._valores_fila(d), fmt['num'])
            fila += 1

        d = potencial['total']
        hoja.write(fila, 0, 'Total', fmt['num_t_b_t'])
        hoja.write(fila, 1, d['tonelaje'], fmt['num_t_b_t'])
        for j, val in enumerate(self._valores_fila(d)):
            hoja.write(fila, 2 + j, val, fmt['num_b_t'])
        fila += 1

        self._escribir_pie(hoja, fmt, fila, 'potencial')

    # -- Comparación con la categoría anterior ---------------------------------------------------
    _ORDEN_COMPARACION = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_MI, CATEG_INFERIDO, CATEG_POTENCIAL)

    def _encabezado_comparacion(self, hoja, fmt, fila):
        hoja.write_row(fila, 0, [self.p.etiqueta_grupo, 'Categoria', 'Anterior (t)', 'Actual (t)',
                                 'Diferencia (t)', 'Variación (%)'], fmt['enc_col'])
        return fila + 1

    def _filas_comparacion(self, hoja, fmt, fila, grupo, filas):
        """Una fila por categoría (Medido, Indicado, M&I, Inferido, Potencial) y una de total (1-4)."""
        for c in self._ORDEN_COMPARACION + ('total',):
            ant, act = filas[c]
            total = c == 'total'
            f_ton, f_dif, f_pct = ((fmt['num_t_b_t'], fmt['dif_b_t'], fmt['pct_b_t']) if total else
                                   (fmt['num_t_b'], fmt['dif_b'], fmt['pct_b']) if c == CATEG_MI else
                                   (fmt['num_t'], fmt['dif'], fmt['pct']))
            if c == self._ORDEN_COMPARACION[0]:
                hoja.write(fila, 0, grupo)
            elif total:
                hoja.write(fila, 0, '', fmt['txt_b_t'])
            hoja.write(fila, 1, 'Total (1-4)' if total else ETIQUETAS_CATEG[c],
                       fmt['txt_b_t'] if total else (fmt['negrita'] if c == CATEG_MI else None))
            hoja.write(fila, 2, ant, f_ton)
            hoja.write(fila, 3, act, f_ton)
            hoja.write(fila, 4, act - ant, f_dif)
            if ant > 0:
                hoja.write(fila, 5, (act - ant) / ant, f_pct)
            else:
                hoja.write(fila, 5, '', f_pct)
            fila += 1
        return fila

    def _hoja_comparacion(self, libro, comp):
        p = self.p
        fmt = self._formatos(libro)
        hoja = libro.add_worksheet('Comparacion_Categ')
        hoja.set_column('A:A', 26)
        hoja.set_column('B:B', 17)
        hoja.set_column('C:G', 16)

        titulo = _generar_titulo(p.fecha_reporte)
        hoja.write(0, 0, ('Comparación de Categorías ' + titulo).strip(), fmt['titulo'])
        hoja.write(1, 0, 'Unidad: ' + p.unidad_minera, fmt['titulo'])
        hoja.write(2, 0, 'Fecha: ' + datetime.date.today().strftime('%d/%m/%Y'))
        hoja.write(3, 0, f"Tonelaje por categoría: anterior ('{comp['columna']}') vs "
                         f"actual ('{comp['columna_actual']}')", fmt['negrita'])
        hoja.write(3, 5, self._texto_cutoff(), fmt['negrita'])

        # 1. Total.
        fila = 5
        hoja.write(fila, 0, 'Total', fmt['negrita'])
        fila = self._encabezado_comparacion(hoja, fmt, fila + 1)
        fila = self._filas_comparacion(hoja, fmt, fila, 'Total', comp['totales'])

        # 2. Matriz de cambio de categoría (t): filas = anterior, columnas = actual.
        fila += 1
        hoja.write(fila, 0, 'Matriz de cambio de categoría (t): filas = anterior, columnas = actual', fmt['negrita'])
        fila += 1
        orden = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL, 0)
        etiqueta = {**ETIQUETAS_CATEG, 0: 'Sin categoría'}
        hoja.write_row(fila, 0, ['Anterior \\ Actual'] + [etiqueta[c] for c in orden] + ['Total anterior'],
                       fmt['enc_col'])
        fila += 1
        matriz = comp['matriz']
        for a in orden:
            hoja.write(fila, 0, etiqueta[a])
            for j, c in enumerate(orden):
                if a == 0 and c == 0:
                    hoja.write(fila, 1 + j, '-', fmt['derecha'])
                else:
                    hoja.write(fila, 1 + j, matriz.get((a, c), 0.0), fmt['diag'] if a == c else fmt['num_t'])
            hoja.write(fila, 1 + len(orden), sum(matriz.get((a, c), 0.0) for c in orden), fmt['num_t_b'])
            fila += 1
        hoja.write(fila, 0, 'Total actual', fmt['txt_b_t'])
        for j, c in enumerate(orden):
            hoja.write(fila, 1 + j, sum(matriz.get((a, c), 0.0) for a in orden), fmt['num_t_b_t'])
        hoja.write(fila, 1 + len(orden), sum(matriz.values()), fmt['num_t_b_t'])
        fila += 1

        # 3. Por dominio/veta.
        fila += 1
        hoja.write(fila, 0, f'Por {p.etiqueta_grupo.lower()}', fmt['negrita'])
        fila = self._encabezado_comparacion(hoja, fmt, fila + 1)
        hoja.freeze_panes(5, 0)
        for grupo in comp['grupos']:
            fila = self._filas_comparacion(hoja, fmt, fila, grupo['grupo'], grupo['filas'])

        fila += 1
        notas = ['Mismos filtros que el resumen (sólido, variable de minado y cut-off); el tonelaje es el mismo en '
                 'ambas columnas, solo cambia la categoría asignada.',
                 'Sin categoría = bloque sin categoría 1 a 4 en esa columna; los bloques sin categoría en ambas '
                 'no se incluyen.']
        for nota in notas:
            hoja.write(fila, 0, nota, fmt['nota'])
            fila += 1
        hoja.write(fila, 0, 'By Yupay ' + Const.VER, fmt['nota'])

    # -- Ganancia de recursos del plan de sondajes -----------------------------------------------------
    _ORDEN_GANANCIA = (CATEG_MEDIDO, CATEG_INDICADO, CATEG_MI, CATEG_INFERIDO, CATEG_MII, CATEG_POTENCIAL)
    _NEGRITA_GANANCIA = (CATEG_MI, CATEG_MII)

    def _hoja_ganancia(self, libro, g):
        p = self.p
        fmt = self._formatos(libro)
        hoja = libro.add_worksheet('Ganancia_Plan')
        hoja.set_column('A:A', 26)
        hoja.set_column('B:B', 20)
        hoja.set_column('C:N', 15)
        long = g['longitud']
        plan = g.get('plan') or {}

        hoja.write(0, 0, ('Ganancia de recursos del plan de sondajes ' + _generar_titulo(p.fecha_reporte)).strip(),
                   fmt['titulo'])
        hoja.write(1, 0, 'Unidad: ' + p.unidad_minera, fmt['titulo'])
        hoja.write(2, 0, 'Fecha: ' + datetime.date.today().strftime('%d/%m/%Y'))
        hoja.write(3, 0, f"Categoría anterior ('{g['columna']}') vs categoría con el plan ('{g['columna_actual']}')",
                   fmt['negrita'])
        hoja.write(3, 5, self._texto_cutoff(), fmt['negrita'])

        # 1. Datos del plan.
        fila = 5
        hoja.write(fila, 0, 'Plan de sondajes', fmt['negrita'])
        fila += 1
        datos = []
        if plan.get('n_sondajes') is not None:
            datos.append(('N° de sondajes', plan['n_sondajes'], fmt['entero']))
        datos.append(('Longitud total del plan (m)', long, fmt['dec1']))
        if plan.get('n_compositos') is not None:
            datos.append(('Compósitos agregados', plan['n_compositos'], fmt['entero']))
        for etiqueta, valor, formato in datos:
            hoja.write(fila, 0, etiqueta)
            hoja.write(fila, 1, valor, formato)
            fila += 1

        # 2. Ganancia por categoría.
        fila += 1
        hoja.write(fila, 0, 'Ganancia de recursos por categoría', fmt['negrita'])
        fila += 1
        fino = g.get('fino')
        encabezados = ['Categoria', 'Anterior (t)', 'Actual (t)', 'Ganancia (t)', 'Variación (%)',
                       'Ratio de conversión (t/m)']
        if fino:
            metal = g['metal']
            encabezados += [f'Ley {metal} anterior', f'Ley {metal} actual', f'{metal} fino anterior (t)',
                            f'{metal} fino actual (t)', f'Ganancia {metal} fino (t)', f'Ratio {metal} fino (t/m)']
        hoja.write_row(fila, 0, encabezados, fmt['enc_col'])
        hoja.set_row(fila, 30, libro.add_format({'text_wrap': True, 'bold': True, 'bottom': 1, 'align': 'center'}))
        fila += 1
        k = self._factor_fino() or 0
        for c in self._ORDEN_GANANCIA + ('total',):
            total = c == 'total'
            neg = c in self._NEGRITA_GANANCIA
            f_ton = fmt['num_t_b_t'] if total else fmt['num_t_b'] if neg else fmt['num_t']
            f_dif = fmt['dif_b_t'] if total else fmt['dif_b'] if neg else fmt['dif']
            f_pct = fmt['pct_b_t'] if total else fmt['pct_b'] if neg else fmt['pct']
            f_rat = fmt['ratio_b_t'] if total else fmt['ratio_b'] if neg else fmt['ratio']
            f_ley = fmt['ley_b_t'] if total else fmt['ley_b'] if neg else fmt['ley']
            f_fin = fmt['fino_b_t'] if total else fmt['fino_b'] if neg else fmt['fino']
            if total:
                ant, act = g['totales']['total']
                nombre = 'Total (1-4)'
            else:
                ant, act = g['totales'][c]
                nombre = ETIQUETAS_CATEG[c]
            hoja.write(fila, 0, nombre, fmt['txt_b_t'] if total else (fmt['negrita'] if neg else None))
            hoja.write(fila, 1, ant, f_ton)
            hoja.write(fila, 2, act, f_ton)
            hoja.write(fila, 3, act - ant, f_dif)
            hoja.write(fila, 4, (act - ant) / ant if ant > 0 else '', f_pct)
            r = _ratio(act - ant, long)
            hoja.write(fila, 5, r if r is not None else '', f_rat)
            if fino:
                fa, fc = fino[c]
                hoja.write(fila, 6, fa / (ant * k) if ant > 0 and k else '', f_ley)
                hoja.write(fila, 7, fc / (act * k) if act > 0 and k else '', f_ley)
                hoja.write(fila, 8, fa, f_fin)
                hoja.write(fila, 9, fc, f_fin)
                hoja.write(fila, 10, fc - fa, f_fin)
                rf = _ratio(fc - fa, long)
                hoja.write(fila, 11, rf if rf is not None else '', f_rat)
            fila += 1

        # 3. Por dominio/veta.
        fila += 1
        hoja.write(fila, 0, f'Por {p.etiqueta_grupo.lower()}', fmt['negrita'])
        fila += 1
        cats = self._ORDEN_GANANCIA[:-1]          # sin potencial
        hoja.write(fila, 1, 'Ganancia (t)', fmt['negrita'])
        hoja.write(fila, 1 + len(cats), 'Ratio de conversión (t/m)', fmt['negrita'])
        fila += 1
        etiquetas = [ETIQUETAS_CATEG[c] for c in cats]
        hoja.write_row(fila, 0, [p.etiqueta_grupo] + etiquetas + etiquetas, fmt['enc_col'])
        hoja.set_row(fila, 30, libro.add_format({'text_wrap': True, 'bold': True, 'bottom': 1, 'align': 'center'}))
        fila += 1
        for grupo in g['grupos']:
            hoja.write(fila, 0, grupo['grupo'])
            for j, c in enumerate(cats):
                ant, act = grupo['filas'][c]
                hoja.write(fila, 1 + j, act - ant, fmt['dif'])
                r = _ratio(act - ant, long)
                hoja.write(fila, 1 + len(cats) + j, r if r is not None else '', fmt['ratio'])
            fila += 1
        hoja.freeze_panes(5, 0)

        fila += 1
        notas = ['Ganancia = tonelaje de la categoría con el plan - tonelaje de la categoría anterior, sobre los mismos '
                 'bloques y filtros que el resumen (sólido, variable de minado y cut-off).',
                 'Ratio de conversión = ganancia / longitud total del plan de sondajes (toneladas ganadas por metro '
                 'perforado).' + ('' if long > 0 else ' No se calculó: falta la longitud del plan.')]
        if plan.get('modo_ley'):
            notas.append('Los sondajes del plan no tienen análisis: sus compósitos llevan una ley provisional '
                         f"(modo '{plan['modo_ley']}'), así que la ganancia refleja el cambio de categoría por "
                         'cobertura de sondajes, no un cambio de leyes.')
        if not fino:
            notas.append('Sin columna de ley anterior en el modelo: no se calculó el metal fino.')
        for nota in notas:
            hoja.write(fila, 0, nota, fmt['nota'])
            fila += 1
        hoja.write(fila, 0, 'By Yupay ' + Const.VER, fmt['nota'])

    def _escribir_pie(self, hoja, fmt, fila, tipo):
        fila += 1
        if tipo == 'recurso':
            hoja.write(fila, 0, 'Nota: Los recursos incluyen a las reservas, ninguna perdida de '
                                'mineral o dilucion han sido incluidas.', fmt['nota'])
            fila += 1
            hoja.write(fila, 0, 'No se han utilizado envolventes para reportar el recurso.', fmt['nota'])
        else:
            hoja.write(fila, 0, 'No se han utilizado envolventes para reportar el potencial.', fmt['nota'])
        fila += 1
        if self.p.etiqueta_precios:
            hoja.write(fila, 0, self.p.etiqueta_precios, fmt['nota'])
            fila += 1
        hoja.write(fila, 0, 'By Yupay ' + Const.VER, fmt['nota'])

    # ==============================================================================================
    def _validar_columnas(self, df_bloques):
        p = self.p
        columnas = set(df_bloques.columns)
        faltan = []

        def chequear(col, desc):
            if col and col not in columnas:
                faltan.append(f"{desc} ('{col}')")

        chequear(p.col_grupo, 'agrupación')
        chequear(p.col_categoria, 'categoría')
        chequear(p.col_densidad, 'densidad')
        if p.valorizado:
            chequear(p.col_nsr, 'NSR')
            chequear(p.col_equiv, 'equivalente')
        chequear(p.col_minado, 'variable de minado')
        chequear(p.col_categ_anterior, 'categoría anterior')
        if p.col_volumen:
            chequear(p.col_volumen, 'volumen')
        else:
            chequear(p.col_x_len, 'X Length')
            chequear(p.col_y_len, 'Y Length')
            chequear(p.col_z_len, 'Z Length')
        for m in p.metales:
            chequear(m.columna, f"ley '{m.etiqueta}'")
        if not p.metales:
            faltan.append('al menos un metal a reportar')
        if faltan:
            raise ValueError('Problemas con las columnas del modelo: ' + ', '.join(faltan) + '.')


def _categoria_valida(serie):
    """Categoría entera 1..4; cualquier otro valor (-99, 0, vacío, no numérico) queda en 0 (sin categoría)."""
    valores = pd.to_numeric(serie, errors='coerce')
    validas = valores.isin([CATEG_MEDIDO, CATEG_INDICADO, CATEG_INFERIDO, CATEG_POTENCIAL])
    return valores.where(validas, 0).round().astype(int)


def texto_comparacion(comp):
    """Líneas de resumen de la comparación de tonelajes por categoría (consola y GUI)."""
    lineas = [f"Comparación de tonelaje por categoría ('{comp['columna']}' -> '{comp['columna_actual']}'):"]
    for c in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_MI, CATEG_INFERIDO, CATEG_POTENCIAL, 'total'):
        ant, act = comp['totales'][c]
        pct = f', {(act - ant) / ant * 100:+.1f} %' if ant > 0 else ''
        lineas.append(f"    {'Total (1-4)' if c == 'total' else ETIQUETAS_CATEG[c]:18s}: {ant:>15,.0f} -> {act:>15,.0f} t "
                      f"({act - ant:+,.0f} t{pct})")
    return '\n'.join(lineas)


def _ratio(ganancia, longitud):
    """Ganancia por metro de sondaje (None si no hay longitud)."""
    return ganancia / longitud if longitud and longitud > 0 else None


def texto_ganancia(g):
    """Líneas de resumen de la ganancia de recursos del plan de sondajes (consola y GUI)."""
    long = g['longitud']
    lineas = [f"Ganancia de recursos del plan de sondajes ('{g['columna']}' -> '{g['columna_actual']}'); "
              + (f"longitud total del plan {long:,.1f} m:" if long > 0 else 'SIN longitud del plan (no hay ratio):')]
    for c in (CATEG_MEDIDO, CATEG_INDICADO, CATEG_MI, CATEG_INFERIDO, CATEG_MII, CATEG_POTENCIAL):
        ant, act = g['totales'][c]
        r = _ratio(act - ant, long)
        lineas.append(f"    {ETIQUETAS_CATEG[c]:18s}: {act - ant:>+15,.0f} t" + (f"   ratio {r:+,.1f} t/m" if r is not None else ''))
    return '\n'.join(lineas)


def _es_grupo_invalido(clave):
    """True si la clave de grupo es un número <= 0 (aire/desmonte, p. ej. -99 o 0)."""
    try:
        return float(clave) <= 0
    except (TypeError, ValueError):
        return False


def fraccion_remanente(serie, tipo):
    """Fracción NO minada de cada bloque (1 = entero, 0 = minado por completo) y cuántos valores
    eran inválidos (vacíos o no numéricos; esos bloques se consideran sin minar).

    - binario   : 1 = minado, 0 = no minado (valores >= 0.5 cuentan como minado).
    - porcentaje: porcentaje remanente, 100 = no minado, 0 = minado; se acota a 0-100 y los
                  valores intermedios dejan esa proporción del bloque.
    """
    valores = pd.to_numeric(serie, errors='coerce').replace([np.inf, -np.inf], np.nan)
    invalidos = int(valores.isna().sum())
    if tipo == TIPO_MINADO_BINARIO:
        fraccion = np.where(valores >= 0.5, 0.0, 1.0)
    else:
        fraccion = valores.fillna(100.0).clip(0.0, 100.0).to_numpy(dtype='float64') / 100.0
    return pd.Series(fraccion, index=serie.index), invalidos


def texto_minado(minado):
    """Línea(s) de resumen de la variable de minado (consola y GUI)."""
    regla = ('1 = minado, 0 = no minado' if minado['tipo'] == TIPO_MINADO_BINARIO
             else '100 = no minado, 0 = minado')
    texto = (f"Variable de minado '{minado['variable']}' ({minado['tipo']}: {regla}): "
             f"{minado['excluidos']:,} bloques minados excluidos")
    if minado['parciales']:
        texto += f", {minado['parciales']:,} parcialmente minados (tonelaje reducido)"
    if minado['invalidos']:
        texto += f". AVISO: {minado['invalidos']:,} bloques con valor vacío se consideraron sin minar"
    return texto + '.'


def _wavg(valores, pesos):
    """Promedio ponderado robusto (0 si la suma de pesos es <= 0)."""
    sw = float(pesos.sum())
    if sw <= 0:
        return 0.0
    return float((valores * pesos).sum() / sw)


# ==================================================================================================
# Nombre de archivo y resumen legible
# ==================================================================================================
def nombre_archivo_salida(params, carpeta):
    """Construye '<carpeta>\\Recursos_<mina>_<periodo>_No_Env_<hora>.xlsx'."""
    periodo = _generar_periodo(params.fecha_reporte)
    partes = ['Recursos', params.unidad_minera]
    if periodo:
        partes.append(periodo)
    partes.extend(['No_Env', Util.hora()])
    return os.path.join(carpeta, '_'.join(str(x) for x in partes if x) + '.xlsx')


def texto_resumen(info):
    """Texto multilínea con el resumen del proceso (para mostrar en la GUI)."""
    var = {'nsr': 'NSR', 'equiv': 'Equivalente'}.get(info['variable_corte'], 'ley')
    lineas = [
        f"Reporte de Recursos generado.",
        '',
        f"Bloques categorizados (1-4): {info['n_categorizados']:,}",
        f"Sobre CutOff ({var} >= {info['cutoff']:g}): {info['n_sobre_cutoff']:,}",
    ]
    if info.get('minado'):
        lineas.extend(['', texto_minado(info['minado'])])
    if info.get('comparacion'):
        lineas.extend(['', texto_comparacion(info['comparacion'])])
    if info.get('ganancia'):
        lineas.extend(['', texto_ganancia(info['ganancia'])])
    lineas += [
        '',
        f"Tonelaje Medido & Indicado: {info['ton_recursos']:,.0f} t",
        f"Tonelaje Inferido: {info['ton_inferido']:,.0f} t",
        f"Tonelaje Potencial: {info['ton_potencial']:,.0f} t",
        '',
        f"Grupos con recurso: {info['n_grupos_recurso']}   |   con potencial: {info['n_grupos_potencial']}",
    ]
    if info.get('ruta_salida'):
        lineas.extend(['', f"Archivo: {info['ruta_salida']}"])
    return '\n'.join(lineas)
