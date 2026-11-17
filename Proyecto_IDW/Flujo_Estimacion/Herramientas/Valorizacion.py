"""
Nombre : Valorizacion.py
Propósito: Motor sin Qt para la valorización del modelo de bloques posterior a la estimación.
           Es el paso intermedio entre la Estimación/Categorización y el Reporte de Recursos:
           asigna atributos económico-físicos al modelo ya estimado para que el reporte solo los
           consuma (no los recalcule).

           Realiza tres operaciones sobre el CSV del modelo de bloques:
             1. Densidad (opcional): asigna un valor único de densidad a TODOS los bloques (columna
                'Densidad' por defecto, que es la que consume la curva tonelaje-ley). Con
                `asignar_densidad=False` no escribe nada: el modelo ya trae la densidad de cada
                bloque (variable) y el reporte la lee de su propia columna.
             2. Recuperación por metal: para cada metal evalúa una expresión de recuperación
                (porcentaje) en función de la ley del bloque y la escribe en la columna
                'pct_rec_<clave>'.
             3. NSR y Equivalente: evalúa las expresiones económicas (que referencian las columnas
                de ley y las de recuperación) y las escribe en sus columnas de salida.

           Réplica de la lógica validada de `CalculaNsr.calcular_nsr_eq_din` del Yupay clásico, pero
           con todos los parámetros ingresados desde la interfaz (antes venían de una BD SQLite):
             - La expresión de recuperación se evalúa POR FILA con `eval` (admite condicionales
               `if/else` y funciones de `math`, p. ej. `math.log`). La variable de la expresión es
               el nombre de la columna de ley (p. ej. `au_idw`), salvo que la expresión use `f.` para
               referirse a varias columnas de la fila.
             - Las expresiones de NSR y Equivalente se evalúan VECTORIZADAS con `DataFrame.eval`
               (aritmética sobre columnas: leyes y recuperaciones), igual que el original.

           No depende de Qt. La interfaz es Herramientas/FmValorizacion.py.
Creacion : 13-Julio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado :
Modificado :
"""

import math  # !!NO ELIMINAR: math se usa en la evaluación dinámica de las expresiones de recuperación!!
import time
from dataclasses import dataclass, field

import pandas as pd

from Util.Const import Const

# Prefijo de las columnas de recuperación generadas (p. ej. clave 'ag' -> 'pct_rec_ag'). Es el mismo
# prefijo que usan las expresiones de NSR/Equivalente para referirse a la recuperación de cada metal.
PREFIJO_RECUPERACION = 'pct_rec_'

# Espacio de nombres global "sandbox" para evaluar las expresiones de recuperación: sin builtins
# (por seguridad) pero con `math` disponible, que es lo único que necesitan las fórmulas típicas.
_GLOBALS_EVAL = {'__builtins__': {}, 'math': math}


@dataclass
class MetalValorizacion:
    """Definición de un metal para el cálculo de recuperación.

    - clave           : nombre corto del metal; genera la columna 'pct_rec_<clave>' que luego
                        referencian las expresiones de NSR/Equivalente (p. ej. 'ag' -> 'pct_rec_ag').
    - columna         : columna del modelo con la ley del metal (p. ej. 'au_idw'). Es también la
                        variable que usa la expresión de recuperación de una sola variable.
    - exp_recuperacion: expresión de recuperación (porcentaje) en función de la ley. Admite
                        condicionales y `math` (p. ej.
                        '28.877*au_idw if au_idw < 2.8 else 4.22*math.log(au_idw)+76.51').
                        Si se deja en blanco, la recuperación de ese metal queda en 0.
    """
    clave: str
    columna: str
    exp_recuperacion: str = ''

    def __post_init__(self):
        self.clave = str(self.clave or '').strip()
        self.columna = str(self.columna or '').strip()
        self.exp_recuperacion = str(self.exp_recuperacion or '').strip()

    @property
    def col_recuperacion(self):
        return PREFIJO_RECUPERACION + self.clave


@dataclass
class ParametrosValorizacion:
    """Parámetros de una corrida de valorización (todos ingresados desde la interfaz).

    - densidad     : valor de densidad aplicado a TODOS los bloques (> 0) cuando `asignar_densidad`.
    - col_densidad : columna de salida de la densidad (por defecto 'Densidad'). Con
                     `asignar_densidad=False` es la columna del modelo que ya trae la densidad de
                     cada bloque (solo se comprueba su existencia).
    - asignar_densidad: True (por defecto) asigna la densidad fija a todos los bloques; False respeta
                     la densidad variable que ya trae el modelo y no escribe la columna.
    - metales      : lista de MetalValorizacion (puede estar vacía si no se calcula NSR/Equiv).
    - exp_nsr      : expresión del NSR (aritmética sobre columnas de ley y 'pct_rec_*'). Opcional.
    - col_nsr      : columna de salida del NSR (por defecto 'nsr').
    - exp_equiv    : expresión del Equivalente (aritmética sobre columnas de ley). Opcional.
    - col_equiv    : columna de salida del Equivalente (por defecto 'equiv').
    """
    densidad: float
    metales: list = field(default_factory=list)
    col_densidad: str = 'Densidad'
    exp_nsr: str = ''
    col_nsr: str = 'nsr'
    exp_equiv: str = ''
    col_equiv: str = 'equiv'
    asignar_densidad: bool = True

    def __post_init__(self):
        self.asignar_densidad = bool(self.asignar_densidad)
        try:
            self.densidad = float(self.densidad)
        except (TypeError, ValueError):
            if self.asignar_densidad:
                raise ValueError('La densidad debe ser un número.') from None
            self.densidad = 0.0   # sin asignar: el valor no se usa
        self.col_densidad = str(self.col_densidad or '').strip() or 'Densidad'
        self.exp_nsr = str(self.exp_nsr or '').strip()
        self.col_nsr = str(self.col_nsr or '').strip() or 'nsr'
        self.exp_equiv = str(self.exp_equiv or '').strip()
        self.col_equiv = str(self.col_equiv or '').strip() or 'equiv'

        # Normalizar/validar la lista de metales (admite dicts o MetalValorizacion).
        metales = []
        for m in (self.metales or []):
            if isinstance(m, MetalValorizacion):
                metal = m
            elif isinstance(m, dict):
                metal = MetalValorizacion(clave=m.get('clave', ''), columna=m.get('columna', ''),
                                          exp_recuperacion=m.get('exp_recuperacion', ''))
            else:
                raise ValueError('Cada metal debe ser un MetalValorizacion o un dict.')
            # Ignorar filas totalmente vacías; validar las parcialmente llenas.
            if not metal.clave and not metal.columna and not metal.exp_recuperacion:
                continue
            if not metal.clave:
                raise ValueError('Hay un metal sin clave (nombre corto para pct_rec_).')
            if not metal.columna:
                raise ValueError(f"El metal '{metal.clave}' no tiene columna de ley asignada.")
            metales.append(metal)
        self.metales = metales

        if self.asignar_densidad and self.densidad <= 0:
            raise ValueError('La densidad debe ser un valor mayor que cero.')

        # Claves de metal duplicadas producirían columnas 'pct_rec_*' colisionadas.
        claves = [m.clave for m in self.metales]
        duplicadas = {c for c in claves if claves.count(c) > 1}
        if duplicadas:
            raise ValueError('Hay claves de metal duplicadas: ' + ', '.join(sorted(duplicadas)) + '.')


class Valorizador:
    """Aplica la valorización a un modelo de bloques ya estimado.

    Uso típico:

        params = ParametrosValorizacion(
            densidad=2.85,
            metales=[
                MetalValorizacion('ag', 'ag_idw', '28.877*ag_idw if ag_idw < 2.8 else 4.22*math.log(ag_idw)+76.51'),
                MetalValorizacion('pb', 'pb_idw', '228.290*pb_idw if pb_idw < 0.4 else 0.24*pb_idw+91.22'),
                MetalValorizacion('zn', 'zn_idw', '111.224*zn_idw if zn_idw < 0.55 else 11.72*math.log(zn_idw)+68.18'),
            ],
            exp_nsr='(ag_idw*13.9179469600783*pct_rec_ag + pb_idw*12.2959414069225*pct_rec_pb + '
                    'zn_idw*10.622826189487*pct_rec_zn)/100',
            exp_equiv='ag_idw + pb_idw*0.883459429942736 + zn_idw*0.763247328979228',
            col_equiv='ageq')
        df, resumen = Valorizador().valorizar(df_bloques, params, ruta_salida='modelo_valorizado.csv')
    """

    def valorizar(self, df_bloques, params, ruta_salida=None, progreso=None):
        """Devuelve (df, resumen). `df` es una copia del modelo con las columnas de densidad,
        recuperaciones, NSR y Equivalente. `progreso` (opcional): callable(fraccion, texto)."""
        t0 = time.perf_counter()
        if not isinstance(params, ParametrosValorizacion):
            raise ValueError('Se requiere un ParametrosValorizacion válido.')
        self._validar_columnas(df_bloques, params)

        n = len(df_bloques)
        df = df_bloques.copy()

        # --- Fase 1: Densidad (valor único a todos los bloques, opcional) ----------------------
        if params.asignar_densidad:
            if progreso:
                progreso(0.1, 'Asignando densidad…')
            df[params.col_densidad] = params.densidad
        elif params.col_densidad not in df.columns:
            print(f"  AVISO: no se asigna densidad y el modelo no tiene la columna '{params.col_densidad}'; "
                  'el reporte la necesita para calcular el tonelaje.')

        # --- Fase 2: Recuperación por metal ----------------------------------------------------
        if progreso:
            progreso(0.35, 'Calculando recuperaciones…')
        for metal in params.metales:
            df[metal.col_recuperacion] = self._evaluar_recuperacion(df, metal)

        # --- Fase 3: NSR y Equivalente (vectorizados) ------------------------------------------
        if progreso:
            progreso(0.7, 'Calculando NSR y Equivalente…')
        if params.exp_nsr:
            df[params.col_nsr] = self._evaluar_economica(df, params.exp_nsr, 'NSR')
        if params.exp_equiv:
            df[params.col_equiv] = self._evaluar_economica(df, params.exp_equiv, 'Equivalente')

        if progreso:
            progreso(0.9, 'Escribiendo salida…')
        if ruta_salida:
            df.to_csv(ruta_salida, index=False, encoding='utf-8-sig')
        if progreso:
            progreso(1.0, 'Finalizado.')

        resumen = self._resumen(df, params, n, ruta_salida, time.perf_counter() - t0)
        print('=' * 70)
        print(f"Valorización completada en {resumen['segundos']:.2f}s: {n:,} bloques, "
              + (f"densidad={params.densidad:g}" if params.asignar_densidad
                 else f"densidad del modelo ('{params.col_densidad}')")
              + (f", NSR '{params.col_nsr}'" if params.exp_nsr else '')
              + (f", Equiv '{params.col_equiv}'" if params.exp_equiv else '') + '.')
        return df, resumen

    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _evaluar_recuperacion(df, metal):
        """Evalúa la expresión de recuperación de un metal POR FILA (admite `if/else` y `math`).

        Réplica de la lógica del Yupay clásico: si la expresión menciona 'f.' se evalúa como función
        de la fila completa (varias variables, `f.<columna>`); si no, la variable es el nombre de la
        columna de ley del metal. Si la expresión está vacía, la recuperación es 0.
        """
        expr = metal.exp_recuperacion
        if not expr:
            return 0.0
        try:
            if 'f.' in expr:  # expresión con varias variables: se referencian como f.<columna>
                return df.apply(lambda f: eval(expr, _GLOBALS_EVAL, {'f': f}), axis=1)
            # expresión de una sola variable: la variable es la columna de ley del metal
            campo = metal.columna
            return df[campo].apply(lambda _valor: eval(expr, _GLOBALS_EVAL, {campo: _valor}))
        except Exception as ex:
            raise ValueError(f"Error al evaluar la recuperación del metal '{metal.clave}' "
                             f"(columna '{metal.columna}'):\n  {expr}\n  -> {ex}")

    @staticmethod
    def _evaluar_economica(df, expr, etiqueta):
        """Evalúa una expresión económica (NSR/Equivalente) vectorizada con `DataFrame.eval`.

        Se intenta el motor por defecto (numexpr si está disponible) y, si falla, se reintenta con el
        motor 'python', que admite más construcciones y nombres de columna.
        """
        try:
            try:
                return df.eval(expr)
            except Exception:
                return df.eval(expr, engine='python')
        except Exception as ex:
            raise ValueError(f"Error al evaluar la expresión de {etiqueta}:\n  {expr}\n  -> {ex}")

    @staticmethod
    def _validar_columnas(df_bloques, params):
        """Verifica que existan las columnas de ley de cada metal (las que sí podemos comprobar sin
        evaluar). Las columnas referenciadas dentro de las expresiones se validan al evaluarlas."""
        columnas = set(df_bloques.columns)
        faltan = []
        for metal in params.metales:
            # Solo exigimos la columna de ley cuando la recuperación es de una sola variable (usa esa
            # columna directamente). Con 'f.' el usuario referencia columnas dentro de la expresión.
            if metal.exp_recuperacion and 'f.' not in metal.exp_recuperacion:
                if metal.columna not in columnas:
                    faltan.append(f"ley de '{metal.clave}' ('{metal.columna}')")
        if faltan:
            raise ValueError('El modelo de bloques no tiene la(s) columna(s): '
                             + ', '.join(faltan) + '.')

    @staticmethod
    def _resumen(df, params, n, ruta_salida, segundos):
        def stats(col):
            if col not in df.columns:
                return None
            s = pd.to_numeric(df[col], errors='coerce')
            return {'min': float(s.min()), 'max': float(s.max()), 'media': float(s.mean())}

        return {
            'n_bloques': n,
            'asignar_densidad': params.asignar_densidad,
            'densidad': params.densidad,
            'col_densidad': params.col_densidad,
            'metales': [
                {'clave': m.clave, 'columna': m.columna, 'col_recuperacion': m.col_recuperacion,
                 'stats': stats(m.col_recuperacion)}
                for m in params.metales
            ],
            'nsr': {'columna': params.col_nsr, 'expresion': params.exp_nsr,
                    'stats': stats(params.col_nsr)} if params.exp_nsr else None,
            'equiv': {'columna': params.col_equiv, 'expresion': params.exp_equiv,
                      'stats': stats(params.col_equiv)} if params.exp_equiv else None,
            'ruta_salida': ruta_salida,
            'segundos': segundos,
        }


# ==================================================================================================
# Resumen legible
# ==================================================================================================
def texto_resumen(resumen):
    """Texto multilínea con el resumen del proceso (para mostrar en la GUI)."""
    def fmt(stats):
        if not stats:
            return ''
        return f" (mín {stats['min']:.4g} | media {stats['media']:.4g} | máx {stats['max']:.4g})"

    lineas = [f"Valorización: {resumen['n_bloques']:,} bloques.", '']
    if resumen.get('asignar_densidad', True):
        lineas.append(f"Densidad asignada: {resumen['densidad']:g}  ->  columna '{resumen['col_densidad']}'")
    else:
        lineas.append(f"Densidad: se usa la del modelo (columna '{resumen['col_densidad']}'); no se asignó ninguna.")

    if resumen['metales']:
        lineas.append('')
        lineas.append('Recuperaciones:')
        for m in resumen['metales']:
            lineas.append(f"    {m['clave']} ({m['columna']}) -> '{m['col_recuperacion']}'"
                          + fmt(m['stats']))

    if resumen.get('nsr'):
        lineas.append('')
        lineas.append(f"NSR -> columna '{resumen['nsr']['columna']}'" + fmt(resumen['nsr']['stats']))
    if resumen.get('equiv'):
        lineas.append(f"Equivalente -> columna '{resumen['equiv']['columna']}'"
                      + fmt(resumen['equiv']['stats']))

    if resumen.get('ruta_salida'):
        lineas.append('')
        lineas.append(f"Archivo CSV: {resumen['ruta_salida']}")
    return '\n'.join(lineas)
