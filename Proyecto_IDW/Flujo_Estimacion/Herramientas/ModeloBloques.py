"""
Nombre : ModeloBloques.py
Propósito: Motor de creación de un Modelo de Bloques 3D con rotación por Bearing y subbloqueo
           variable, réplica del formato BDF de Maptek Vulcan.
           Genera la malla de bloques a partir de un esquema geométrico, inicializa las
           variables del modelo con su valor por defecto, codifica variables por sólidos de
           triangulación (.obj) con prioridad (Boundaries) y recorta el modelo eliminando los
           bloques que cumplan expresiones de condición (Exceptions). NO realiza estimación
           (IDW/Kriging): solo deja el modelo de bloques listo para fases posteriores.
           No depende de Qt.

           Convenciones Vulcan validadas contra bm_40_m.csv + 40.bdf (10-Jul-2026):
             - Rotación: 'bearing' es el AZIMUT del eje X local del modelo (el eje Y local
               queda a bearing-90°, giro antihorario en planta). Con bearing=90 el modelo es
               ortogonal (X local = Este). Plunge y Dip son fijos en 0 (no soportados).
                   X = origen_x + u·sin(θ) − v·cos(θ)
                   Y = origen_y + u·cos(θ) + v·sin(θ)      (θ = bearing, u/v/w locales)
                   Z = origen_z + w
             - Subbloqueo: cada bloque padre cortado por algún sólido se subdivide en celdas
               mínimas (sub_x/sub_y/sub_z); cada celda se codifica por el estado de su
               CENTROIDE (validado: 99.998% de coincidencia con Vulcan; las diferencias son
               celdas de borde por paridad del winding number, mismo caso conocido de la
               validación de codificación de sondajes). Las celdas contiguas con los mismos
               valores se fusionan en subbloques rectangulares dentro del padre (tamaños
               múltiplos del mínimo). Un padre con todas sus celdas iguales queda como un
               único bloque de tamaño padre.
             - Fusión: greedy por cuboide de mayor volumen; desempate por ancla (w,v,u)
               ascendente y luego forma (nu,nv,nw) ascendente. Replica bit a bit el 87% de
               los padres de Vulcan; el 13% restante difiere solo en CÓMO se tesela la misma
               región (cobertura, valores y volúmenes idénticos): la heurística exacta de
               fusión de Vulcan es propietaria y dependiente del orden.

           Reutiliza el motor geométrico ya probado del aplicativo:
             - CodificadorEspacialV2.load_solid  -> carga/reparación del .obj (trimesh).
             - CodificadorSondajes._puntos_en_solido -> test punto-en-sólido con Fast Winding
               Number (libigl) + recentrado UTM + re-evaluación exacta en la banda de frontera.

           Convención de PRIORIDAD: el número MAYOR gana (prioridad 5 vence a 1); en empate,
           prevalece la regla con mayor 'orden' (la más abajo en el grid). NOTA: es la
           convención inversa a CodificadorSondajes (donde 1 = máxima); aquí se respeta la
           especificación propia de este módulo.
Creacion : 17-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 10-Julio-2026 - Rotación por Bearing (réplica Vulcan) y subbloqueo variable
Modificado :
"""

import re
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from Util.Const import Const
from Herramientas.CodificadorEspacialV2 import CodificadorEspacialV2
from Herramientas.CodificadorSondajes import CodificadorSondajes
# Reutiliza el MISMO traductor de expresiones estilo Vulcan que usa el motor de estimación
# (EstimacionIDW.traducir_condicion) para que las excepciones de este módulo acepten la misma
# sintaxis ('envau eq -99', 'au_id ge 0 and ZC gt 100', ...) que las 'Condition' del flujo.
from Herramientas.EstimacionIDW import traducir_condicion

# Tipos de variable soportados
TIPO_FLOAT = 'Float'
TIPO_INTEGER = 'Integer'
TIPO_STRING = 'String'
TIPOS_VALIDOS = (TIPO_FLOAT, TIPO_INTEGER, TIPO_STRING)

# Rotación por defecto: bearing=90 (eje X local = Este) equivale a modelo ortogonal sin
# rotación. Plunge y Dip permanecen fijos en 0 (no soportados, igual que el alcance acordado).
BEARING_DEFECTO = 90.0
PLUNGE_FIJO = 0.0
DIP_FIJO = 0.0

# Columnas de geometría del CSV de salida (las variables se agregan a continuación).
# Con subbloqueo, IX/IY/IZ son los índices del bloque PADRE (los subbloques los comparten).
COL_IX, COL_IY, COL_IZ = 'IX', 'IY', 'IZ'
COL_XC, COL_YC, COL_ZC = 'XC', 'YC', 'ZC'
COL_DX, COL_DY, COL_DZ = 'DX', 'DY', 'DZ'
COLUMNAS_GEOMETRIA = [COL_IX, COL_IY, COL_IZ, COL_XC, COL_YC, COL_ZC, COL_DX, COL_DY, COL_DZ]

# Nombres válidos de variable: identificadores usables como columnas y en expresiones.
PATRON_NOMBRE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _num_bloques(extension: float, size: float) -> int:
    """Número de bloques en un eje. Si la extensión es múltiplo del tamaño (salvo ruido
    numérico) se redondea; en caso contrario se usa el ceil para cubrir toda la extensión."""
    ratio = extension / size
    n = int(round(ratio))
    if abs(ratio - n) > 1e-6:
        n = int(math.ceil(ratio))
    return max(1, n)


def _ratio_subbloqueo(size: float, sub: float, eje: str) -> int:
    """Celdas mínimas por bloque padre en un eje. sub<=0 significa 'sin subbloqueo'."""
    if sub is None or sub <= 0:
        return 1
    ratio = size / sub
    n = int(round(ratio))
    if n < 1 or abs(ratio - n) > 1e-6:
        raise ValueError(f"El subbloque {eje} ({sub:g}) debe dividir exactamente al tamaño "
                         f"de bloque padre ({size:g}).")
    return n


# ==================================================================================================
# Estructuras de datos (dataclasses)
# ==================================================================================================
@dataclass
class EsquemaGrilla:
    """Definición geométrica de la grilla (Parent Block + subbloqueo opcional + Bearing).

    El 'origen' es la esquina inferior del modelo en coordenadas de mundo (el punto local
    (0,0,0)); el primer centroide se ubica en origen + tamaño/2 medido sobre los ejes LOCALES
    rotados. 'bearing' es el azimut del eje X local (90 = sin rotación); Plunge y Dip son
    fijos en 0. Los subbloques (sub_x/sub_y/sub_z) son el tamaño MÍNIMO de subdivisión por
    eje; 0 = sin subbloqueo en ese eje. Deben dividir exactamente al tamaño padre.
    """
    origen_x: float
    origen_y: float
    origen_z: float
    extension_x: float
    extension_y: float
    extension_z: float
    x_size: float
    y_size: float
    z_size: float
    bearing: float = BEARING_DEFECTO
    sub_x: float = 0.0
    sub_y: float = 0.0
    sub_z: float = 0.0
    # Rotación fuera de plano fija (hardcoded), no modificable por el usuario.
    plunge: float = field(default=PLUNGE_FIJO, init=False)
    dip: float = field(default=DIP_FIJO, init=False)

    def __post_init__(self):
        for nombre in ('origen_x', 'origen_y', 'origen_z', 'extension_x', 'extension_y',
                       'extension_z', 'x_size', 'y_size', 'z_size', 'bearing',
                       'sub_x', 'sub_y', 'sub_z'):
            setattr(self, nombre, float(getattr(self, nombre)))
        for nombre in ('extension_x', 'extension_y', 'extension_z'):
            if getattr(self, nombre) <= 0:
                raise ValueError(f"La {nombre.replace('_', ' ')} debe ser mayor que cero.")
        for nombre in ('x_size', 'y_size', 'z_size'):
            if getattr(self, nombre) <= 0:
                raise ValueError(f"El tamaño de bloque {nombre} debe ser mayor que cero.")
        self.bearing = self.bearing % 360.0
        # Valida la divisibilidad del subbloqueo (falla pronto).
        _ = self.ratio_x, self.ratio_y, self.ratio_z

    @property
    def n_x(self) -> int:
        return _num_bloques(self.extension_x, self.x_size)

    @property
    def n_y(self) -> int:
        return _num_bloques(self.extension_y, self.y_size)

    @property
    def n_z(self) -> int:
        return _num_bloques(self.extension_z, self.z_size)

    @property
    def n_bloques(self) -> int:
        return self.n_x * self.n_y * self.n_z

    @property
    def ratio_x(self) -> int:
        return _ratio_subbloqueo(self.x_size, self.sub_x, 'X')

    @property
    def ratio_y(self) -> int:
        return _ratio_subbloqueo(self.y_size, self.sub_y, 'Y')

    @property
    def ratio_z(self) -> int:
        return _ratio_subbloqueo(self.z_size, self.sub_z, 'Z')

    @property
    def tiene_subbloqueo(self) -> bool:
        return self.ratio_x * self.ratio_y * self.ratio_z > 1

    @property
    def n_celdas_por_padre(self) -> int:
        return self.ratio_x * self.ratio_y * self.ratio_z

    def _seno_coseno(self):
        theta = math.radians(self.bearing)
        return math.sin(theta), math.cos(theta)

    def local_a_mundo(self, u, v, w):
        """Transforma coordenadas locales (u,v,w) a mundo (X,Y,Z). Acepta escalares o arrays."""
        s, c = self._seno_coseno()
        return (self.origen_x + u * s - v * c,
                self.origen_y + u * c + v * s,
                self.origen_z + w)

    def mundo_a_local(self, x, y, z):
        """Transforma coordenadas de mundo a locales (inversa de local_a_mundo)."""
        s, c = self._seno_coseno()
        dx, dy = x - self.origen_x, y - self.origen_y
        return dx * s + dy * c, -dx * c + dy * s, z - self.origen_z

    def avisos(self) -> list:
        """Avisos no fatales: ejes cuya extensión no es múltiplo exacto del tamaño de bloque."""
        avisos = []
        for eje, ext, size, n in (('X', self.extension_x, self.x_size, self.n_x),
                                  ('Y', self.extension_y, self.y_size, self.n_y),
                                  ('Z', self.extension_z, self.z_size, self.n_z)):
            ratio = ext / size
            if abs(ratio - round(ratio)) > 1e-6:
                avisos.append(f"La extensión {eje} ({ext:g}) no es múltiplo del tamaño de bloque "
                              f"({size:g}): se usarán {n} bloques que cubren {n * size:g}.")
        return avisos


@dataclass
class VariableBloque:
    """Variable que existirá en cada bloque del modelo."""
    nombre: str
    tipo: str = TIPO_FLOAT
    valor_defecto: object = Const.VAL_DEFECTO
    descripcion: str = ''

    def __post_init__(self):
        self.nombre = str(self.nombre).strip()
        if not PATRON_NOMBRE.match(self.nombre):
            raise ValueError(f"'{self.nombre}' no es un nombre de variable válido. Use solo "
                             'letras, números y guion bajo, comenzando por una letra.')
        if self.tipo not in TIPOS_VALIDOS:
            raise ValueError(f"Tipo de variable inválido: {self.tipo!r}. "
                             f"Use uno de {TIPOS_VALIDOS}.")
        self.descripcion = str(self.descripcion or '')
        # Normaliza el valor por defecto al tipo declarado (falla pronto si no es convertible).
        self.valor_defecto = self.convertir(self.valor_defecto)

    @property
    def dtype_pandas(self) -> str:
        return {TIPO_FLOAT: 'float64', TIPO_INTEGER: 'Int64', TIPO_STRING: 'object'}[self.tipo]

    def convertir(self, valor):
        """Castea 'valor' al tipo de la variable. Lanza ValueError si no es convertible."""
        if self.tipo == TIPO_FLOAT:
            return float(valor)
        if self.tipo == TIPO_INTEGER:
            f = float(valor)
            if not float(f).is_integer():
                raise ValueError(f"'{valor}' no es un entero.")
            return int(f)
        return str(valor)


@dataclass
class LimiteSolido:
    """Regla de codificación (Boundary): si el centroide del bloque (o de la celda mínima,
    con subbloqueo) cae dentro del sólido '.obj', asigna 'valor' a la variable 'variable'."""
    ruta_archivo: str
    variable: str
    valor: object
    prioridad: int = 1
    orden: int = 0
    # Parámetros de Vulcan no soportados aún; documentados para uso futuro de la lógica.
    inversion: object = field(default=None, init=False)
    proyeccion: str = field(default='along_z', init=False)

    def __post_init__(self):
        self.ruta_archivo = str(self.ruta_archivo)
        self.variable = str(self.variable).strip()
        self.prioridad = int(self.prioridad)
        self.orden = int(self.orden)


@dataclass
class ExcepcionExpresion:
    """Excepción (recorte): los bloques que cumplan la condición se eliminan del modelo. La
    condición se escribe en sintaxis Vulcan (operadores de palabra 'eq/ne/ge/le/gt/lt' y
    conectores 'and/or/not', p. ej. 'envau eq -99'); se traduce a pandas.eval con
    traducir_condicion. Referencia variables y/o las columnas de geometría (XC, YC, ZC, ...).
    La expresión original se conserva sin traducir para mostrarla en la UI y en el JSON."""
    expresion: str
    descripcion: str = ''

    def __post_init__(self):
        self.expresion = str(self.expresion).strip()
        self.descripcion = str(self.descripcion or '')
        if not self.expresion:
            raise ValueError('La expresión de excepción no puede estar vacía.')


# ==================================================================================================
# Fusión de celdas en subbloques rectangulares (réplica de la clase de estructura de Vulcan)
# ==================================================================================================
class _FusionadorCeldas:
    """Descompone la grilla de celdas mínimas de un padre en cuboides homogéneos por firma.

    Greedy por volumen máximo con desempate por ancla (w,v,u) ascendente y luego forma
    (nu,nv,nw) ascendente: la variante que replica bit a bit la mayor fracción de particiones
    de Vulcan (87% en la validación contra bm_40_m.csv). Los resultados se memoizan por
    patrón de firmas: los padres cortados de forma parecida reutilizan la descomposición.
    """

    def __init__(self, rx: int, ry: int, rz: int):
        self.rx, self.ry, self.rz = rx, ry, rz
        self.n_celdas = rx * ry * rz
        self._catalogo = self._construir_catalogo(rx, ry, rz)
        self._memo = {}

    @staticmethod
    def _construir_catalogo(rx, ry, rz):
        """Todos los cuboides posibles de la grilla rx×ry×rz, ya ordenados por la regla
        greedy. Cada entrada: (mascara_bits, (u0, v0, w0, nu, nv, nw))."""
        catalogo = []
        for u0 in range(rx):
            for nu in range(1, rx - u0 + 1):
                for v0 in range(ry):
                    for nv in range(1, ry - v0 + 1):
                        for w0 in range(rz):
                            for nw in range(1, rz - w0 + 1):
                                mascara = 0
                                for d in range(w0, w0 + nw):
                                    for b in range(v0, v0 + nv):
                                        for a in range(u0, u0 + nu):
                                            mascara |= 1 << (a + rx * (b + ry * d))
                                catalogo.append((mascara, nu * nv * nw,
                                                 (u0, v0, w0, nu, nv, nw)))
        # (-volumen, ancla w/v/u ascendente, forma nu/nv/nw ascendente)
        catalogo.sort(key=lambda e: (-e[1], e[2][2], e[2][1], e[2][0],
                                     e[2][3], e[2][4], e[2][5]))
        return [(m, geo) for m, _, geo in catalogo]

    def descomponer(self, firmas: np.ndarray):
        """firmas: array (n_celdas,) con la firma (int) de cada celda, indexada por
        celda = u + rx*(v + ry*w). Devuelve lista de (u0, v0, w0, nu, nv, nw, celda_ancla)."""
        clave = firmas.tobytes()
        piezas = self._memo.get(clave)
        if piezas is not None:
            return piezas

        piezas = []
        for firma in np.unique(firmas):
            mascara = 0
            for i in np.flatnonzero(firmas == firma):
                mascara |= 1 << int(i)
            while mascara:
                for m_cubo, geo in self._catalogo:
                    if m_cubo & mascara == m_cubo:
                        u0, v0, w0, nu, nv, nw = geo
                        piezas.append((u0, v0, w0, nu, nv, nw,
                                       u0 + self.rx * (v0 + self.ry * w0)))
                        mascara &= ~m_cubo
                        break
        # Orden determinista de salida por ancla (w, v, u).
        piezas.sort(key=lambda p: (p[2], p[1], p[0]))
        self._memo[clave] = piezas
        return piezas


# ==================================================================================================
# Motor de generación
# ==================================================================================================
class GeneradorModeloBloques:
    """Genera el modelo de bloques y devuelve (DataFrame, resumen).

    Uso típico:

        esquema = EsquemaGrilla(origen_x=0, origen_y=0, origen_z=0,
                                extension_x=100, extension_y=100, extension_z=50,
                                x_size=10, y_size=10, z_size=10,
                                bearing=37.0, sub_x=2.5, sub_y=2.5, sub_z=2.5)
        variables = [VariableBloque('au_idw', 'Float', -99.0),
                     VariableBloque('rock_type', 'Integer', -99)]
        limites = [LimiteSolido('veta.obj', 'rock_type', 1, prioridad=2, orden=0)]
        excepciones = [ExcepcionExpresion('rock_type == -99')]
        df, resumen = GeneradorModeloBloques().generar(esquema, variables, limites,
                                                       excepciones, ruta_salida='modelo.csv')
    """

    def generar(self, esquema, variables, limites=None, excepciones=None,
                ruta_salida=None, tolerancia_pared=0.0, eps_winding=0.01):
        limites = list(limites or [])
        excepciones = list(excepciones or [])
        t0 = time.perf_counter()

        # 1. Validación cruzada (referencias, duplicados, expresiones) antes de construir nada.
        var_por_nombre = self._validar(esquema, variables, limites)
        self._validar_excepciones(variables, excepciones)

        print('=' * 70)
        print(f'  Modelo de bloques: {esquema.n_x} × {esquema.n_y} × {esquema.n_z} = '
              f'{esquema.n_bloques:,} bloques padre | bearing {esquema.bearing:g}° | '
              f'subbloqueo {esquema.ratio_x}×{esquema.ratio_y}×{esquema.ratio_z} | '
              f'{len(variables)} variable(s) | {len(limites)} sólido(s) | '
              f'{len(excepciones)} excepción(es)')

        # 2. Grilla de centroides de bloques padre (locales + mundo).
        ix, iy, iz, ul, vl, wl = self._grilla_padres(esquema)
        X, Y, Z = esquema.local_a_mundo(ul, vl, wl)
        puntos_padre = np.column_stack([X, Y, Z])

        # 3. Codificación por sólidos (a nivel padre y, con subbloqueo, por celda mínima en
        #    los padres candidatos a estar cortados por alguna superficie).
        (codigos_par, valores_por_var, idx_cand, codigos_sub,
         resumen_limites, n_colisiones) = self._codificar(
            esquema, variables, limites, var_por_nombre, puntos_padre,
            tolerancia_pared, eps_winding)

        # 4. Ensamblaje de filas: padres uniformes enteros + padres mixtos descompuestos en
        #    subbloques rectangulares fusionados por firma de valores.
        df, n_padres_mixtos, n_subbloques = self._ensamblar(
            esquema, variables, ix, iy, iz, codigos_par, valores_por_var,
            idx_cand, codigos_sub)
        n_total = len(df)

        # 5. Excepciones: recorte por expresión (se elimina el bloque que cumpla cualquiera).
        resumen_excepciones, df = self._aplicar_excepciones(df, excepciones)
        n_eliminados = n_total - len(df)

        # 6. Salida CSV.
        if ruta_salida:
            df.to_csv(ruta_salida, index=False, encoding='utf-8-sig')

        resumen = {
            'n_x': esquema.n_x, 'n_y': esquema.n_y, 'n_z': esquema.n_z,
            'bearing': esquema.bearing,
            'subbloqueo': (esquema.ratio_x, esquema.ratio_y, esquema.ratio_z),
            'n_padres': esquema.n_bloques,
            'n_padres_mixtos': n_padres_mixtos,
            'n_subbloques': n_subbloques,
            'n_bloques_iniciales': n_total,
            'por_limite': resumen_limites,
            'n_colisiones': n_colisiones,
            'por_excepcion': resumen_excepciones,
            'n_eliminados': n_eliminados,
            'n_bloques_finales': len(df),
            'avisos': esquema.avisos(),
            'ruta_salida': ruta_salida,
        }
        print('=' * 70)
        print(f'Modelo generado en {time.perf_counter() - t0:.2f}s: '
              f'{n_total:,} bloques creados ({n_padres_mixtos:,} padres subdivididos en '
              f'{n_subbloques:,} subbloques) | {n_eliminados:,} eliminados por excepción | '
              f'{len(df):,} finales')
        return df, resumen

    # ----------------------------------------------------------------------------------------------
    # Validación
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _validar(esquema, variables, limites):
        """Valida referencias y devuelve {nombre_variable: VariableBloque}."""
        if not isinstance(esquema, EsquemaGrilla):
            raise ValueError('Se requiere un EsquemaGrilla válido.')
        if not variables:
            raise ValueError('Debe definir al menos una variable para el modelo de bloques.')

        nombres = [v.nombre for v in variables]
        duplicados = sorted({n for n in nombres if nombres.count(n) > 1})
        if duplicados:
            raise ValueError(f"Variables con nombre duplicado: {', '.join(duplicados)}.")
        choque = sorted(set(nombres) & set(COLUMNAS_GEOMETRIA))
        if choque:
            raise ValueError('Estos nombres de variable colisionan con columnas de geometría '
                             f"({', '.join(COLUMNAS_GEOMETRIA)}): {', '.join(choque)}.")

        var_por_nombre = {v.nombre: v for v in variables}
        for s in limites:
            nombre_obj = Path(s.ruta_archivo).name or s.ruta_archivo
            if s.variable not in var_por_nombre:
                raise ValueError(f"El sólido '{nombre_obj}' codifica la variable '{s.variable}', "
                                 'que no está definida en la lista de variables.')
            if not s.ruta_archivo or not Path(s.ruta_archivo).exists():
                raise ValueError(f"No existe el archivo de triangulación: {s.ruta_archivo}")
            # Falla pronto si el valor del sólido no es convertible al tipo de su variable.
            try:
                var_por_nombre[s.variable].convertir(s.valor)
            except (TypeError, ValueError) as ex:
                raise ValueError(f"El valor '{s.valor}' del sólido '{nombre_obj}' no es "
                                 f"convertible al tipo {var_por_nombre[s.variable].tipo} de la "
                                 f"variable '{s.variable}'.") from ex
        return var_por_nombre

    def _validar_excepciones(self, variables, excepciones):
        """Valida cada expresión sobre un DataFrame de muestra (1 fila) con las mismas columnas
        y dtypes que el modelo, para detectar columnas inexistentes o sintaxis inválida pronto."""
        if not excepciones:
            return
        datos = {
            COL_IX: pd.Series([0], dtype='int64'), COL_IY: pd.Series([0], dtype='int64'),
            COL_IZ: pd.Series([0], dtype='int64'), COL_XC: pd.Series([0.0], dtype='float64'),
            COL_YC: pd.Series([0.0], dtype='float64'), COL_ZC: pd.Series([0.0], dtype='float64'),
            COL_DX: pd.Series([1.0], dtype='float64'), COL_DY: pd.Series([1.0], dtype='float64'),
            COL_DZ: pd.Series([1.0], dtype='float64'),
        }
        for v in variables:
            datos[v.nombre] = self._serie_inicial(v, 1)
        muestra = pd.DataFrame(datos)
        for ex in excepciones:
            try:
                res = muestra.eval(traducir_condicion(ex.expresion), engine='python')
            except Exception as e:
                raise ValueError(f"Expresión de excepción inválida: '{ex.expresion}'.\n{e}") from e
            if np.asarray(res).dtype != bool:
                raise ValueError('La expresión de excepción debe evaluar a verdadero/falso: '
                                 f"'{ex.expresion}'.")

    # ----------------------------------------------------------------------------------------------
    # Grillas de centroides
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _grilla_padres(esquema):
        """Índices y centroides LOCALES de todos los bloques padre (arrays aplanados, orden
        C con IX exterior)."""
        nx, ny, nz = esquema.n_x, esquema.n_y, esquema.n_z
        gix, giy, giz = np.meshgrid(np.arange(nx), np.arange(ny), np.arange(nz), indexing='ij')
        ix, iy, iz = gix.ravel(), giy.ravel(), giz.ravel()
        ul = (ix + 0.5) * esquema.x_size
        vl = (iy + 0.5) * esquema.y_size
        wl = (iz + 0.5) * esquema.z_size
        return ix, iy, iz, ul, vl, wl

    @staticmethod
    def _celdas_locales(esquema):
        """Offsets locales (du, dv, dw) del centroide de cada celda mínima respecto a la
        esquina inferior del padre, indexados por celda = cu + rx*(cv + ry*cw)."""
        rx, ry, rz = esquema.ratio_x, esquema.ratio_y, esquema.ratio_z
        sx, sy, sz = esquema.x_size / rx, esquema.y_size / ry, esquema.z_size / rz
        cw, cv, cu = np.meshgrid(np.arange(rz), np.arange(ry), np.arange(rx), indexing='ij')
        du = (cu.ravel() + 0.5) * sx
        dv = (cv.ravel() + 0.5) * sy
        dw = (cw.ravel() + 0.5) * sz
        return du, dv, dw

    @staticmethod
    def _serie_inicial(variable, n):
        """Columna inicial de la variable: n veces su valor por defecto, con el dtype correcto."""
        if variable.tipo == TIPO_FLOAT:
            return pd.Series(np.full(n, float(variable.valor_defecto), dtype='float64'))
        if variable.tipo == TIPO_INTEGER:
            return pd.Series(np.full(n, int(variable.valor_defecto), dtype='int64'), dtype='Int64')
        return pd.Series(np.full(n, variable.valor_defecto, dtype=object))

    # ----------------------------------------------------------------------------------------------
    # Detección de padres candidatos (posiblemente cortados por alguna superficie)
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _padres_candidatos(esquema, meshes, margen):
        """Máscara (n_padres,) de los padres cuya caja puede estar cruzada por algún triángulo
        de algún sólido (test conservador por AABB del triángulo en coordenadas locales).
        Un padre no cruzado tiene todas sus celdas mínimas con el mismo estado dentro/fuera,
        por lo que NO necesita evaluación por celda."""
        nx, ny, nz = esquema.n_x, esquema.n_y, esquema.n_z
        marca = np.zeros((nx, ny, nz), dtype=bool)
        for mesh in meshes:
            vx, vy, vz = mesh.vertices[:, 0], mesh.vertices[:, 1], mesh.vertices[:, 2]
            lu, lv, lw = esquema.mundo_a_local(vx, vy, vz)
            tri = mesh.faces
            tu, tv, tw = lu[tri], lv[tri], lw[tri]
            i0 = np.floor((tu.min(axis=1) - margen) / esquema.x_size).astype(np.int64)
            i1 = np.floor((tu.max(axis=1) + margen) / esquema.x_size).astype(np.int64)
            j0 = np.floor((tv.min(axis=1) - margen) / esquema.y_size).astype(np.int64)
            j1 = np.floor((tv.max(axis=1) + margen) / esquema.y_size).astype(np.int64)
            k0 = np.floor((tw.min(axis=1) - margen) / esquema.z_size).astype(np.int64)
            k1 = np.floor((tw.max(axis=1) + margen) / esquema.z_size).astype(np.int64)

            # Descarta triángulos totalmente fuera de la grilla y recorta el resto.
            dentro = (i1 >= 0) & (i0 < nx) & (j1 >= 0) & (j0 < ny) & (k1 >= 0) & (k0 < nz)
            i0, i1 = np.clip(i0[dentro], 0, nx - 1), np.clip(i1[dentro], 0, nx - 1)
            j0, j1 = np.clip(j0[dentro], 0, ny - 1), np.clip(j1[dentro], 0, ny - 1)
            k0, k1 = np.clip(k0[dentro], 0, nz - 1), np.clip(k1[dentro], 0, nz - 1)

            # Marca los rangos por grupos de desplazamiento (los triángulos suelen abarcar
            # 1-2 padres por eje, así que los bucles son cortos).
            su, sv, sw = i1 - i0, j1 - j0, k1 - k0
            for a in range(int(su.max()) + 1 if su.size else 0):
                ma = su >= a
                for b in range(int(sv[ma].max()) + 1 if ma.any() else 0):
                    mb = ma & (sv >= b)
                    for d in range(int(sw[mb].max()) + 1 if mb.any() else 0):
                        md = mb & (sw >= d)
                        marca[i0[md] + a, j0[md] + b, k0[md] + d] = True
        return marca.ravel()

    # ----------------------------------------------------------------------------------------------
    # Codificación por sólidos (Boundaries)
    # ----------------------------------------------------------------------------------------------
    def _codificar(self, esquema, variables, limites, var_por_nombre, puntos_padre,
                   tolerancia_pared, eps_winding):
        """Codifica variables a nivel padre y, con subbloqueo, a nivel de celda mínima en los
        padres candidatos. Devuelve códigos por variable (0 = valor por defecto; k = k-ésimo
        valor asignado por un límite), el mapa código→valor, los candidatos y el resumen.

        La resolución por prioridad es por variable: si dos sólidos codifican la MISMA
        variable, gana el de mayor prioridad (empate: mayor 'orden') vía last-write-wins."""
        n_par = len(puntos_padre)
        codigos_par = {v.nombre: np.zeros(n_par, dtype=np.int16) for v in variables}
        valores_por_var = {v.nombre: [v.valor_defecto] for v in variables}
        idx_cand = np.array([], dtype=np.int64)
        codigos_sub = {}
        resumen = []
        n_colisiones_total = 0
        if not limites:
            return codigos_par, valores_por_var, idx_cand, codigos_sub, resumen, 0

        # Carga de mallas (cache por ruta: un mismo .obj puede codificar varias variables).
        cache_mesh = {}
        for s in limites:
            if s.ruta_archivo not in cache_mesh:
                cache_mesh[s.ruta_archivo] = CodificadorEspacialV2.load_solid(
                    Path(s.ruta_archivo))

        # Subbloqueo: padres candidatos y centroides de sus celdas mínimas.
        subbloqueo = esquema.tiene_subbloqueo
        puntos_sub = None
        if subbloqueo:
            margen = tolerancia_pared + 1e-3
            candidatos = self._padres_candidatos(esquema, cache_mesh.values(), margen)
            idx_cand = np.flatnonzero(candidatos)
            n_cel = esquema.n_celdas_por_padre
            print(f'  Subbloqueo: {idx_cand.size:,} padres candidatos de {n_par:,} '
                  f'({n_cel} celdas mínimas c/u)')
            if idx_cand.size:
                du, dv, dw = self._celdas_locales(esquema)
                # Esquina inferior local de cada padre candidato + offset de cada celda.
                ix, iy, iz, _, _, _ = self._grilla_padres(esquema)
                bu = ix[idx_cand] * esquema.x_size
                bv = iy[idx_cand] * esquema.y_size
                bw = iz[idx_cand] * esquema.z_size
                cu_l = (bu[:, None] + du[None, :]).ravel()
                cv_l = (bv[:, None] + dv[None, :]).ravel()
                cw_l = (bw[:, None] + dw[None, :]).ravel()
                Xs, Ys, Zs = esquema.local_a_mundo(cu_l, cv_l, cw_l)
                puntos_sub = np.column_stack([Xs, Ys, Zs])
            codigos_sub = {v.nombre: np.zeros((idx_cand.size, esquema.n_celdas_por_padre),
                                              dtype=np.int16) for v in variables}

        validos_par = np.ones(n_par, dtype=bool)
        no_cand = np.ones(n_par, dtype=bool)
        if idx_cand.size:
            no_cand[idx_cand] = False
        n_cel = esquema.n_celdas_por_padre

        # Agrupar por variable preservando el orden de aparición.
        por_variable = {}
        for s in limites:
            por_variable.setdefault(s.variable, []).append(s)

        cache_inside_par = {}
        cache_inside_sub = {}
        for nombre_var, grupo in por_variable.items():
            variable = var_por_nombre[nombre_var]
            # Orden ascendente por (prioridad, orden): el de MAYOR prioridad se aplica al final
            # y por lo tanto prevalece (last-write-wins).
            for s in sorted(grupo, key=lambda x: (x.prioridad, x.orden)):
                mesh = cache_mesh[s.ruta_archivo]
                inside_par = cache_inside_par.get(s.ruta_archivo)
                if inside_par is None:
                    inside_par = CodificadorSondajes._puntos_en_solido(
                        puntos_padre, validos_par, mesh, tolerancia_pared, eps_winding)
                    cache_inside_par[s.ruta_archivo] = inside_par

                valor_cast = variable.convertir(s.valor)
                valores_por_var[nombre_var].append(valor_cast)
                codigo = len(valores_por_var[nombre_var]) - 1

                cod_par = codigos_par[nombre_var]
                colisiones = int((inside_par & no_cand & (cod_par > 0)).sum()) * n_cel
                n_dentro = int((inside_par & no_cand).sum()) * n_cel
                cod_par[inside_par] = codigo

                if subbloqueo and puntos_sub is not None:
                    inside_sub = cache_inside_sub.get(s.ruta_archivo)
                    if inside_sub is None:
                        inside_sub = CodificadorSondajes._puntos_en_solido(
                            puntos_sub, np.ones(len(puntos_sub), dtype=bool), mesh,
                            tolerancia_pared, eps_winding).reshape(idx_cand.size, n_cel)
                        cache_inside_sub[s.ruta_archivo] = inside_sub
                    cod_sub = codigos_sub[nombre_var]
                    colisiones += int((inside_sub & (cod_sub > 0)).sum())
                    n_dentro += int(inside_sub.sum())
                    cod_sub[inside_sub] = codigo

                n_colisiones_total += colisiones
                resumen.append({'nombre': Path(s.ruta_archivo).stem, 'variable': nombre_var,
                                'valor': valor_cast, 'prioridad': s.prioridad,
                                'n_dentro': n_dentro, 'n_colisiones': colisiones})
                print(f"  '{Path(s.ruta_archivo).stem}' -> {nombre_var}={valor_cast} "
                      f"(prioridad {s.prioridad}): {n_dentro:,} celdas mínimas dentro" +
                      (f', {colisiones:,} sobre otro sólido de la misma variable'
                       if colisiones else ''))
        return codigos_par, valores_por_var, idx_cand, codigos_sub, resumen, n_colisiones_total

    # ----------------------------------------------------------------------------------------------
    # Ensamblaje de filas (padres enteros + subbloques fusionados)
    # ----------------------------------------------------------------------------------------------
    def _ensamblar(self, esquema, variables, ix, iy, iz, codigos_par, valores_por_var,
                   idx_cand, codigos_sub):
        """Construye el DataFrame final. Los padres candidatos con celdas uniformes vuelven a
        ser un único bloque padre; los mixtos se descomponen en subbloques rectangulares."""
        n_par = len(ix)
        rx, ry, rz = esquema.ratio_x, esquema.ratio_y, esquema.ratio_z
        sx, sy, sz = esquema.x_size / rx, esquema.y_size / ry, esquema.z_size / rz

        idx_mixtos = np.array([], dtype=np.int64)
        pos_mixtos = np.array([], dtype=np.int64)
        if idx_cand.size:
            mixto = np.zeros(idx_cand.size, dtype=bool)
            for nombre, cod_sub in codigos_sub.items():
                mixto |= (cod_sub != cod_sub[:, :1]).any(axis=1)
            # Candidatos uniformes: su valor real es el de sus celdas (no el del centroide
            # del padre, que en casos de borde podría diferir).
            uni = ~mixto
            if uni.any():
                for nombre, cod_sub in codigos_sub.items():
                    codigos_par[nombre][idx_cand[uni]] = cod_sub[uni, 0]
            idx_mixtos = idx_cand[mixto]
            pos_mixtos = np.flatnonzero(mixto)

        es_padre_entero = np.ones(n_par, dtype=bool)
        es_padre_entero[idx_mixtos] = False

        # --- Padres enteros (vectorizado) -----------------------------------------------------
        ixe, iye, ize = ix[es_padre_entero], iy[es_padre_entero], iz[es_padre_entero]
        ule = (ixe + 0.5) * esquema.x_size
        vle = (iye + 0.5) * esquema.y_size
        wle = (ize + 0.5) * esquema.z_size
        Xe, Ye, Ze = esquema.local_a_mundo(ule, vle, wle)
        n_ent = len(ixe)
        col_ent = {
            COL_IX: ixe, COL_IY: iye, COL_IZ: ize, COL_XC: Xe, COL_YC: Ye, COL_ZC: Ze,
            COL_DX: np.full(n_ent, esquema.x_size), COL_DY: np.full(n_ent, esquema.y_size),
            COL_DZ: np.full(n_ent, esquema.z_size),
        }
        codigos_ent = {nombre: cod[es_padre_entero] for nombre, cod in codigos_par.items()}

        # --- Padres mixtos: descomposición en subbloques --------------------------------------
        n_padres_mixtos = len(idx_mixtos)
        n_subbloques = 0
        col_sub = {c: [] for c in COLUMNAS_GEOMETRIA}
        codigos_sub_filas = {v.nombre: [] for v in variables}
        if n_padres_mixtos:
            fusionador = _FusionadorCeldas(rx, ry, rz)
            nombres_vars = [v.nombre for v in variables]
            # Firma combinada de la celda: códigos de todas las variables en base mixta.
            bases = [len(valores_por_var[n]) for n in nombres_vars]
            firmas_todas = np.zeros((n_padres_mixtos, esquema.n_celdas_por_padre),
                                    dtype=np.int64)
            for nombre, base in zip(nombres_vars, bases):
                firmas_todas = firmas_todas * base + codigos_sub[nombre][pos_mixtos]

            for fila, (ipar, pos) in enumerate(zip(idx_mixtos, pos_mixtos)):
                piezas = fusionador.descomponer(firmas_todas[fila])
                bu = ix[ipar] * esquema.x_size
                bv = iy[ipar] * esquema.y_size
                bw = iz[ipar] * esquema.z_size
                for (u0, v0, w0, nu, nv, nw, celda_ancla) in piezas:
                    ulp = bu + (u0 + nu * 0.5) * sx
                    vlp = bv + (v0 + nv * 0.5) * sy
                    wlp = bw + (w0 + nw * 0.5) * sz
                    Xp, Yp, Zp = esquema.local_a_mundo(ulp, vlp, wlp)
                    col_sub[COL_IX].append(ix[ipar]); col_sub[COL_IY].append(iy[ipar])
                    col_sub[COL_IZ].append(iz[ipar])
                    col_sub[COL_XC].append(Xp); col_sub[COL_YC].append(Yp)
                    col_sub[COL_ZC].append(Zp)
                    col_sub[COL_DX].append(nu * sx); col_sub[COL_DY].append(nv * sy)
                    col_sub[COL_DZ].append(nw * sz)
                    for nombre in nombres_vars:
                        codigos_sub_filas[nombre].append(
                            codigos_sub[nombre][pos, celda_ancla])
                n_subbloques += len(piezas)

        # --- Concatenación y orden determinista (índice lineal del padre) ---------------------
        ny, nz = esquema.n_y, esquema.n_z
        lin_ent = (col_ent[COL_IX] * ny + col_ent[COL_IY]) * nz + col_ent[COL_IZ]
        lin_sub = ((np.array(col_sub[COL_IX], dtype=np.int64) * ny +
                    np.array(col_sub[COL_IY], dtype=np.int64)) * nz +
                   np.array(col_sub[COL_IZ], dtype=np.int64))
        # Dentro de un padre las piezas ya vienen en orden (w,v,u); mergesort es estable.
        lin = np.concatenate([lin_ent, lin_sub])
        orden = np.argsort(lin, kind='stable')

        datos = {}
        for c in COLUMNAS_GEOMETRIA:
            serie = np.concatenate([np.asarray(col_ent[c], dtype='float64'),
                                    np.asarray(col_sub[c], dtype='float64')])
            if c in (COL_IX, COL_IY, COL_IZ):
                datos[c] = pd.Series(serie[orden].astype(np.int64))
            else:
                datos[c] = pd.Series(serie[orden])

        for v in variables:
            codigos_fila = np.concatenate([
                codigos_ent[v.nombre].astype(np.int64),
                np.asarray(codigos_sub_filas[v.nombre], dtype=np.int64)])[orden]
            valores = valores_por_var[v.nombre]
            if v.tipo == TIPO_FLOAT:
                datos[v.nombre] = pd.Series(np.array(valores, dtype='float64')[codigos_fila])
            elif v.tipo == TIPO_INTEGER:
                datos[v.nombre] = pd.Series(np.array(valores, dtype='int64')[codigos_fila],
                                            dtype='Int64')
            else:
                datos[v.nombre] = pd.Series(np.array(valores, dtype=object)[codigos_fila])

        df = pd.DataFrame(datos)
        return df, n_padres_mixtos, n_subbloques

    # ----------------------------------------------------------------------------------------------
    # Excepciones (recorte por expresión)
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _aplicar_excepciones(df, excepciones):
        resumen = []
        if not excepciones:
            return resumen, df
        mask_total = np.zeros(len(df), dtype=bool)
        for ex in excepciones:
            try:
                res = df.eval(traducir_condicion(ex.expresion), engine='python')
            except Exception as e:
                raise ValueError(f"Expresión de excepción inválida: '{ex.expresion}'.\n{e}") from e
            mask = np.asarray(res)
            if mask.dtype != bool:
                raise ValueError('La expresión de excepción debe evaluar a verdadero/falso: '
                                 f"'{ex.expresion}'.")
            n_ex = int(mask.sum())
            resumen.append({'expresion': ex.expresion, 'n_eliminados': n_ex})
            mask_total |= mask
            print(f"  Excepción '{ex.expresion}': {n_ex:,} bloques marcados para eliminar")
        df_out = df.loc[~mask_total].reset_index(drop=True)
        return resumen, df_out


# ==================================================================================================
# Resumen legible
# ==================================================================================================
def texto_resumen(resumen):
    """Construye un texto multilínea con el resumen del proceso (para mostrar en la GUI)."""
    rx, ry, rz = resumen.get('subbloqueo', (1, 1, 1))
    lineas = [f"Modelo de bloques generado: {resumen['n_x']} × {resumen['n_y']} × "
              f"{resumen['n_z']} = {resumen['n_padres']:,} bloques padre.",
              f"Bearing: {resumen.get('bearing', BEARING_DEFECTO):g}°  |  "
              f"Subbloqueo: {rx}×{ry}×{rz}"]
    if resumen.get('n_padres_mixtos'):
        lineas.append(f"Padres subdivididos: {resumen['n_padres_mixtos']:,} "
                      f"({resumen['n_subbloques']:,} subbloques)")
    lineas.append(f"Bloques iniciales: {resumen['n_bloques_iniciales']:,}")
    lineas.append('')
    if resumen['por_limite']:
        lineas.append('Codificación por sólidos:')
        for s in resumen['por_limite']:
            lineas.append(f"    {s['nombre']} -> {s['variable']}={s['valor']} "
                          f"(prioridad {s['prioridad']}): {s['n_dentro']:,} celdas")
        if resumen['n_colisiones']:
            lineas.append(f"    Conflictos resueltos por prioridad: {resumen['n_colisiones']:,}")
        lineas.append('')
    if resumen['por_excepcion']:
        lineas.append('Excepciones (bloques eliminados):')
        for e in resumen['por_excepcion']:
            lineas.append(f"    {e['expresion']}: {e['n_eliminados']:,} bloques")
        lineas.append('')
    lineas.append(f"Bloques eliminados por excepción: {resumen['n_eliminados']:,}")
    lineas.append(f"Bloques finales en el modelo: {resumen['n_bloques_finales']:,}")
    if resumen.get('ruta_salida'):
        lineas.append('')
        lineas.append(f"Archivo CSV: {resumen['ruta_salida']}")
    if resumen.get('avisos'):
        lineas.append('')
        lineas.append('Avisos:')
        for a in resumen['avisos']:
            lineas.append(f"    - {a}")
    return '\n'.join(lineas)
