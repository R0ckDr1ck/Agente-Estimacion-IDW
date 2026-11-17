"""
Nombre : EstimacionIDW.py
Propósito: Motor sin Qt de estimación de leyes por Inverso a la Distancia (IDW), diseñado para
           REPLICAR el IDW de Maptek Vulcan y permitir comparar los resultados de YupAI contra una
           corrida equivalente en Vulcan. Consume dos entradas:
             - un modelo de bloques (DataFrame del CSV de ModeloBloques.py: XC/YC/ZC centroides,
               DX/DY/DZ tamaños y las variables codificadas, p. ej. 'envau', 'au_id'), y
             - una tabla de compósito (DataFrame de FmCompositacion: MIDX/MIDY/MIDZ, ley, length y
               un campo de dominio, p. ej. 'ENVAU').
           Replica la mecánica de Vulcan por PASADAS: cada pasada define un elipsoide de búsqueda
           anisotrópico (radios mayor/semi/menor + rotación bearing/plunge/dip), restricciones de
           muestreo (mín/máx muestras, máx por octante, máx por sondaje, máx sondajes, distancia de
           duplicados), discretización del bloque, potencia y una anisotropía de ponderación; y una
           'Condition' (estilo Vulcan) que restringe qué bloques estima. Las condiciones se evalúan
           contra el modelo VIVO, que se actualiza tras cada pasada (comportamiento multipasada:
           'au_id eq -99' hace que una pasada solo rellene los bloques aún sin estimar).

           Rendimiento: por cada bloque se consulta UNA sola vez un cKDTree construido en el espacio
           del elipsoide (esfera unitaria); el cálculo de distancias muestra×punto-de-discretización
           se hace con broadcasting de NumPy (sin bucles Python en ese nivel).

           No depende de Qt. La interfaz es Herramientas/FmEstimacionIDW.py.
Creacion : 30-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 13-Julio-2026 - Candidatos por k-NN escalado en pasadas con tope de muestras y sin
             límite por octante (p. ej. la pasada automática de distancia a 3 sondajes, esfera de
             500 m): el ball-query materializaba casi todo el compósito por bloque. Resultado
             idéntico; las pasadas con límite por octante siguen usando la vía original validada.
Modificado : 10-Setiembre-2026 - Avisos de PASADA MUDA. Una pasada que no estima nada no lanzaba
             error: solo escribía menos de lo que el usuario creía. Ahora `_procesar_pasada` devuelve
             (n_estimados, avisos) y explica los tres motivos —el filtro de dominio no casó con
             ninguna muestra, la condición no seleccionó ningún bloque, o ningún bloque reunió el
             mínimo de muestras—, por consola y en `resumen['avisos']`, que `texto_resumen` muestra.
             Cambio puramente aditivo: ninguna fórmula ni criterio de selección se tocó, y la salida
             es byte a byte idéntica (verificado en las dos vías, directa y por bloque padre).
Modificado : 02-Noviembre-2026 - 'Valor dominio' de la pasada acepta varios dominios separados por
             coma ('21, 22, 23'); un solo valor se comporta igual que antes.
Modificado :
"""

import re
import csv
import math
import time
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from Util.Const import Const

# Columnas de geometría del modelo de bloques (CSV de ModeloBloques.py).
COL_XC, COL_YC, COL_ZC = 'XC', 'YC', 'ZC'
COL_DX, COL_DY, COL_DZ = 'DX', 'DY', 'DZ'

# Operadores de condición estilo Vulcan -> pandas. Se aplican con límites de palabra (\b) e
# insensibles a mayúsculas. Los conectores 'and'/'or'/'not' se normalizan a minúsculas porque
# pandas.eval solo los entiende así (sintaxis Python).
_OPERADORES_VULCAN = [
    (r'\bge\b', '>='), (r'\ble\b', '<='), (r'\bne\b', '!='),
    (r'\beq\b', '=='), (r'\bgt\b', '>'), (r'\blt\b', '<'),
    (r'\band\b', 'and'), (r'\bor\b', 'or'), (r'\bnot\b', 'not'),
]

# Piso numérico para la distancia de ponderación (evita división por cero cuando un punto de
# discretización coincide exactamente con una muestra).
_EPS_DIST = 1e-10


# ==================================================================================================
# Helpers geométricos y de expresión
# ==================================================================================================
def traducir_condicion(expresion):
    """Traduce una condición estilo Vulcan a sintaxis de pandas.eval.

    Sustituye los operadores de palabra 'eq/ne/ge/le/gt/lt' por '== != >= <= > <' y normaliza
    'and/or/not' a minúsculas, todo insensible a mayúsculas y sin tocar el interior de literales
    entre comillas. Mantiene los nombres de columna. Devuelve '' si la expresión está vacía.
    """
    s = str(expresion or '').strip()
    if not s:
        return ''
    # Los índices pares de este split quedan FUERA de comillas; los impares son literales.
    partes = re.split(r'("[^"]*"|\'[^\']*\')', s)
    for i in range(0, len(partes), 2):
        for patron, reemplazo in _OPERADORES_VULCAN:
            partes[i] = re.sub(patron, reemplazo, partes[i], flags=re.IGNORECASE)
    return ''.join(partes)


def lista_dominios(valor):
    """Valores de dominio de una pasada como lista de textos sin repetir y en el orden dado.

    Acepta un número ('21', 21, 21.0), varios separados por coma o punto y coma ('21, 22, 23') o una
    lista/tupla. Los números enteros se normalizan sin decimales. Devuelve [] si no hay valores
    (la pasada no filtra por dominio)."""
    if valor is None:
        return []
    if isinstance(valor, (list, tuple, set)):
        crudos = [str(v) for v in valor]
    elif isinstance(valor, float) and valor.is_integer():
        crudos = [str(int(valor))]
    else:
        crudos = re.split(r'[,;]', str(valor))
    resultado = []
    for c in crudos:
        c = c.strip()
        if c and c not in resultado:
            resultado.append(c)
    return resultado


def _matriz_rotacion(bearing, plunge, dip):
    """Matriz de rotación 3x3 del elipsoide de búsqueda (convención Vulcan, composición Z-Y-X).

    Devuelve R tal que un vector del mundo se lleva al marco principal del elipsoide con ``R @ v``.
      - bearing: giro alrededor de Z,
      - plunge : giro alrededor de Y',
      - dip    : giro alrededor de X''.

    DATUM DEL BEARING: Vulcan mide el bearing como AZIMUT (desde el Norte); un modelo/elipsoide sin
    rotar tiene bearing=90 (no 0). Por eso restamos 90°: así bearing=90 -> identidad y bearing=180
    -> +90° sobre Z. Calibrado contra una corrida real de Vulcan (dominio 40, modelo rotado):
    con este datum los bloques cuya selección coincide con Vulcan estiman con error ~0.01.
    NOTA: el caso de prueba tenía plunge=90 (la elipse horizontal tiene simetría de 180°), así que
    el SIGNO exacto del offset y las convenciones de plunge/dip aún deben confirmarse con un caso de
    ángulos distintos (bearing != 180 y plunge != 90).
    """
    b, p, d = math.radians(bearing - 90.0), math.radians(plunge), math.radians(dip)
    cb, sb = math.cos(b), math.sin(b)
    cp, sp = math.cos(p), math.sin(p)
    cd, sd = math.cos(d), math.sin(d)
    rz = np.array([[cb, sb, 0.0], [-sb, cb, 0.0], [0.0, 0.0, 1.0]])
    ry = np.array([[cp, 0.0, -sp], [0.0, 1.0, 0.0], [sp, 0.0, cp]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cd, sd], [0.0, -sd, cd]])
    return rx @ ry @ rz


def _matriz_octante(bearing, plunge, dip):
    """Matriz de rotación del marco en que Vulcan asigna los OCTANTES (opción 'Select octant rotation
    type': Cartesian/Ellipsoid/Rotate axes). Es INDEPENDIENTE del elipsoide de búsqueda.

    Calibrada contra una corrida real de Vulcan (extracción de muestras con su octante): reproduce
    la agrupación de octantes con 0 discrepancias en 6 bloques. Anclas: 'Cartesian axes' (90/0/0) ->
    identidad (octantes en ejes del mundo); 'Rotate axes' 45/0/35 -> biyección exacta con los octantes
    de Vulcan. Se expresa reutilizando ``_matriz_rotacion``:  R_oct = Rx(-dip)·Ry(plunge)·Rz(90-bearing).
    NOTA: verificado con una sola configuración no trivial (plunge=0); un caso con plunge!=0 confirmaría
    la convención de plunge.
    """
    return _matriz_rotacion(180.0 - bearing, plunge, -dip)


def _puntos_discretizacion_unitarios(nx, ny, nz):
    """Offsets relativos (fracción del tamaño de bloque, en [-0.5, 0.5]) de los puntos de
    discretización: nx*ny*nz puntos en los centros de las subceldas. (1,1,1) -> solo el centroide."""
    fx = (np.arange(max(1, int(nx))) + 0.5) / max(1, int(nx)) - 0.5
    fy = (np.arange(max(1, int(ny))) + 0.5) / max(1, int(ny)) - 0.5
    fz = (np.arange(max(1, int(nz))) + 0.5) / max(1, int(nz)) - 0.5
    gx, gy, gz = np.meshgrid(fx, fy, fz, indexing='ij')
    return np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)


# ==================================================================================================
# Estructuras de datos (dataclasses)
# ==================================================================================================
@dataclass
class MapeoCampos:
    """Nombres de columna (comunes a todas las pasadas) que enlazan compósito y modelo de bloques.

    Campos de la MUESTRA (compósito): x/y/z, ley, longitud (peso), dominio (W Field), sondaje.
    Geometría del BLOQUE: bloque_xc/yc/zc (centroides) y bloque_dx/dy/dz (tamaños); por defecto
    XC/YC/ZC/DX/DY/DZ (CSV de ModeloBloques.py), mapeables a cualquier nombre.
    Campo de salida en el BLOQUE: col_estimacion (p. ej. 'au_id') con su valor_defecto.
    Campos de reporte OPCIONALES (se escriben solo si tienen nombre): nsamp, nholes, flag,
    distclose_cart, distclose_anisot, nn, avgdist_cart, avgdist_anisot.
    """
    x: str = 'MIDX'
    y: str = 'MIDY'
    z: str = 'MIDZ'
    ley: str = ''
    longitud: str = 'length'
    dominio: str = ''
    sondaje: str = 'Sondaje'
    bloque_xc: str = COL_XC
    bloque_yc: str = COL_YC
    bloque_zc: str = COL_ZC
    bloque_dx: str = COL_DX
    bloque_dy: str = COL_DY
    bloque_dz: str = COL_DZ
    col_estimacion: str = 'au_id'
    valor_defecto: float = Const.VAL_DEFECTO
    nsamp: str = ''
    nholes: str = ''
    flag: str = ''
    distclose_cart: str = ''
    distclose_anisot: str = ''
    nn: str = ''
    avgdist_cart: str = ''
    avgdist_anisot: str = ''

    def __post_init__(self):
        for nombre in ('x', 'y', 'z', 'ley', 'longitud', 'dominio', 'sondaje', 'col_estimacion',
                       'nsamp', 'nholes', 'flag', 'distclose_cart', 'distclose_anisot', 'nn',
                       'avgdist_cart', 'avgdist_anisot'):
            setattr(self, nombre, str(getattr(self, nombre) or '').strip())
        # Geometría del bloque: si llega vacío se recupera el nombre por defecto.
        for nombre, defecto in (('bloque_xc', COL_XC), ('bloque_yc', COL_YC), ('bloque_zc', COL_ZC),
                                ('bloque_dx', COL_DX), ('bloque_dy', COL_DY), ('bloque_dz', COL_DZ)):
            setattr(self, nombre, str(getattr(self, nombre) or '').strip() or defecto)
        try:
            self.valor_defecto = float(self.valor_defecto)
        except (TypeError, ValueError):
            self.valor_defecto = float(Const.VAL_DEFECTO)
        if not self.col_estimacion:
            raise ValueError('Debe indicar el nombre de la variable a estimar (col_estimacion).')

    def columnas_reporte(self):
        """{atributo: nombre_columna} de los campos de reporte con nombre definido."""
        campos = ('nsamp', 'nholes', 'flag', 'distclose_cart', 'distclose_anisot', 'nn',
                  'avgdist_cart', 'avgdist_anisot')
        return {c: getattr(self, c) for c in campos if getattr(self, c)}


@dataclass
class PasadaIDW:
    """Una pasada de estimación (una fila del Estimation Parameter File de Vulcan)."""
    id: str
    potencia: float = 2.0
    mayor: float = 100.0
    semi: float = 100.0
    menor: float = 100.0
    bearing: float = 0.0
    plunge: float = 0.0
    dip: float = 0.0
    oct_bearing: float = 90.0      # rotación para el OCTANTE (Vulcan 'Select octant rotation type';
    oct_plunge: float = 0.0        # 90/0/0 = 'Cartesian axes' = sin rotar. Es INDEPENDIENTE de la
    oct_dip: float = 0.0           # rotación del elipsoide de búsqueda (bearing/plunge/dip).
    anis_x: float = 1.0
    anis_y: float = 1.0
    anis_z: float = 1.0
    normalizar_radios: bool = True
    min_muestras: int = 1
    max_muestras: int = 0          # 0 = sin límite
    max_octante: int = 0           # 0 = sin límite
    max_por_sondaje: int = 0       # 0 = sin límite
    max_sondajes: int = 0          # 0 = sin límite
    disc_x: int = 1
    disc_y: int = 1
    disc_z: int = 1
    dist_duplicados: float = -1.0  # < 0 = desactivado (sólo avisa); >= 0 = DETIENE si hay duplicados
    valor_flag: float = 1.0
    dominio_valor: object = None   # 'Specific W Values': uno o varios dominios ('21' o '21, 22, 23');
                                   # None/'' = sin filtro de dominio
    condicion: str = ''
    var_bearing: str = ''          # Variable del modelo de bloques con el ángulo de CADA bloque
    var_plunge: str = ''           # (p. ej. 'lva_b'); '' = se usa el valor fijo bearing/plunge/dip
    var_dip: str = ''

    def __post_init__(self):
        self.id = str(self.id).strip() or 'pasada'
        for nombre in ('var_bearing', 'var_plunge', 'var_dip'):
            setattr(self, nombre, str(getattr(self, nombre) or '').strip())
        for nombre in ('potencia', 'mayor', 'semi', 'menor', 'bearing', 'plunge', 'dip',
                       'oct_bearing', 'oct_plunge', 'oct_dip',
                       'anis_x', 'anis_y', 'anis_z', 'dist_duplicados', 'valor_flag'):
            setattr(self, nombre, float(getattr(self, nombre)))
        for nombre in ('min_muestras', 'max_muestras', 'max_octante', 'max_por_sondaje',
                       'max_sondajes', 'disc_x', 'disc_y', 'disc_z'):
            setattr(self, nombre, int(getattr(self, nombre)))
        self.normalizar_radios = bool(self.normalizar_radios)
        self.condicion = str(self.condicion or '').strip()
        if not lista_dominios(self.dominio_valor):
            self.dominio_valor = None

        if self.potencia <= 0:
            raise ValueError(f"Pasada '{self.id}': la potencia debe ser mayor que cero.")
        for nombre in ('mayor', 'semi', 'menor'):
            if getattr(self, nombre) <= 0:
                raise ValueError(f"Pasada '{self.id}': el radio '{nombre}' debe ser mayor que cero.")
        for nombre in ('anis_x', 'anis_y', 'anis_z'):
            if getattr(self, nombre) <= 0:
                raise ValueError(f"Pasada '{self.id}': la anisotropía '{nombre}' debe ser > 0.")
        for nombre in ('disc_x', 'disc_y', 'disc_z'):
            if getattr(self, nombre) < 1:
                raise ValueError(f"Pasada '{self.id}': la discretización '{nombre}' debe ser >= 1.")
        if self.min_muestras < 1:
            raise ValueError(f"Pasada '{self.id}': el mínimo de muestras debe ser >= 1.")

    @property
    def dominios(self):
        """Valores de dominio de la pasada como lista de textos (vacía = sin filtro)."""
        return lista_dominios(self.dominio_valor)

    def radios_ponderacion(self):
        """Divisores (en unidades de terreno) de la anisotropía de PONDERACIÓN IDW.

        La distancia anisotrópica de ponderación es ``||diag(1/r) @ R @ (p - c)||``. Vulcan usa
        como divisores 'r' los 'Inverse Anisotropic Radius' (anis_x/y/z) DIRECTAMENTE, sin
        multiplicarlos por los radios de búsqueda (mayor/semi/menor). Con esto la distancia queda
        en unidades de terreno —coincide con la que reporta Vulcan: ~1.0–1.43x la cartesiana para
        anis 1/0.8/0.7— y, sobre todo, su FORMA relativa reproduce los pesos IDW de Vulcan.

        Multiplicar por los radios de búsqueda (como se hacía antes) duplicaba la anisotropía del
        elipsoide dentro de la métrica de ponderación (forma 1:0.64:0.49 en vez de 1:0.8:0.7 para
        este caso) y por eso el valor estimado difería de Vulcan.

        'normalizar_radios' (Normalise Radii to Search Radii) NO participa en el cálculo. Según la
        doc de Vulcan (Univariate Estimation Editor, 2025.2), 'Normalise' es un BOTÓN del editor
        que rellena los pesos anisotrópicos a partir de los radios de búsqueda (por eso en el
        proyecto validado anis 1/0.8/0.7 == 10/8/7 normalizado: no era coincidencia); la
        ponderación se rige siempre por los anis. El flag se conserva solo por fidelidad con el
        Estimation Parameter File; no tiene columna en la grilla de la GUI.
        """
        return np.array([self.anis_x, self.anis_y, self.anis_z], dtype=float)

    def variables_orientacion(self):
        """{'bearing': var, 'plunge': var, 'dip': var} de los ángulos tomados del modelo de bloques."""
        todas = {'bearing': self.var_bearing, 'plunge': self.var_plunge, 'dip': self.var_dip}
        return {k: v for k, v in todas.items() if v}


@dataclass
class GrillaModelo:
    """Definición del block model para estimar a resolución de bloque PADRE cuando el modelo está
    SUB-BLOQUEADO. Vulcan estima cada bloque padre UNA sola vez (búsqueda, selección y ponderación
    desde el centroide del padre) y los sub-bloques heredan el resultado; estimar cada sub-bloque
    desde su propio centroide produce selecciones y leyes distintas a Vulcan.

    - origen: base (esquina mínima) del esquema del block model, en coordenadas del mundo (x0,y0,z0).
    - tam_padre: tamaño del bloque padre (DX, DY, DZ).
    - bearing: rotación (azimut) del modelo. Datum de Vulcan: 90 = SIN rotar; el marco del modelo
      gira (90 - bearing) alrededor del eje vertical. dip/plunge del modelo se asumen 0 (habitual en
      sub-bloqueo). La grilla tiene simetría de 90°, así que el signo del datum no altera la celda
      padre resultante. Verificado contra una corrida real de Vulcan (bearing 37 -> giro 53°).
    """
    origen: tuple
    tam_padre: tuple = (5.0, 5.0, 5.0)
    bearing: float = 90.0

    def _rot2d(self):
        phi = math.radians(90.0 - float(self.bearing))
        c, s = math.cos(phi), math.sin(phi)
        return np.array([[c, s], [-s, c]])

    def rotacion_modelo(self):
        """Matriz 3x3 que lleva offsets del marco del MODELO al mundo (``w = m @ R``). Es el giro
        de (90 - bearing)° sobre el eje vertical; identidad si bearing=90 (modelo sin rotar). Se
        usa para orientar los puntos de discretización según los ejes del block model, que es como
        Vulcan los aplica (verificado por ingeniería inversa de sus pesos por muestra)."""
        phi = math.radians(90.0 - float(self.bearing))
        c, s = math.cos(phi), math.sin(phi)
        return np.array([[c, s, 0.0], [-s, c, 0.0], [0.0, 0.0, 1.0]])

    def centroides_padre(self, xyz):
        """Centroide (mundo) del bloque padre que contiene cada punto de `xyz` (N,3)."""
        xyz = np.asarray(xyz, dtype=float)
        ox, oy, oz = (float(v) for v in self.origen)
        px, py, pz = (float(v) for v in self.tam_padre)
        rot = self._rot2d()
        m = (xyz[:, :2] - np.array([ox, oy])) @ rot.T
        cm = (np.floor(m / np.array([px, py])) + 0.5) * np.array([px, py])
        xy = np.array([ox, oy]) + cm @ rot
        z = oz + (np.floor((xyz[:, 2] - oz) / pz) + 0.5) * pz
        return np.column_stack([xy, z])


# ==================================================================================================
# Motor de estimación
# ==================================================================================================
class EstimadorIDW:
    """Estima por IDW un modelo de bloques a partir de un compósito, réplica de Vulcan.

    Uso típico:

        mapeo = MapeoCampos(ley='AUAC', dominio='ENVAU', col_estimacion='au_id')
        pasadas = [PasadaIDW('p1', potencia=3, mayor=10, semi=8, menor=7, min_muestras=3,
                             max_muestras=10, max_octante=2, max_por_sondaje=2, max_sondajes=10,
                             disc_x=3, disc_y=1, disc_z=3, dominio_valor=40,
                             condicion='envau eq 40'), ...]
        df_est, resumen = EstimadorIDW().estimar(df_bloques, df_comp, mapeo, pasadas,
                                                 ruta_salida='modelo_estimado.csv')
    """

    def estimar(self, df_bloques, df_muestras, mapeo, pasadas, ruta_salida=None, progreso=None,
                rot_modelo=None, verbose=True):
        """`rot_modelo` (3x3, opcional): rotación de los puntos de discretización al marco del
        block model (``mundo = offset_modelo @ rot_modelo``); None = modelo sin rotar (identidad).
        `estimar_por_padre` la deriva automáticamente del bearing de la GrillaModelo.

        `verbose` (por defecto True): imprime el resumen por consola. `estimar_por_padre` lo pone en
        False para no reportar los conteos a nivel de bloque padre (imprime luego los de sub-bloque).

        SALVEDAD (réplicas de Vulcan): si el block model está ROTADO (bearing != 90) y se estima por
        esta vía directa (sin Estimar en Parent) con discretización > 1x1x1, los puntos de
        discretización quedan alineados a los ejes del mundo mientras que Vulcan los orienta según
        la rotación del modelo -> pueden aparecer diferencias de ley por este solo motivo. Ante
        discrepancias en una réplica con modelo rotado, revisar este caso primero: pasar el
        `rot_modelo` correspondiente o estimar en Parent (vía que sí lo deriva). Con modelos sin
        rotar o discretización 1x1x1 no hay diferencia."""
        t0 = time.perf_counter()
        pasadas = list(pasadas or [])
        self._validar(df_bloques, df_muestras, mapeo, pasadas)

        df = df_bloques.reset_index(drop=True).copy()
        n_bloques = len(df)

        # Columnas de salida (estimación + reporte) con sus valores iniciales de "no estimado".
        self._preparar_columnas(df, mapeo)

        # Coerción numérica: la columna de estimación y las columnas referenciadas en condiciones,
        # para que pandas.eval no compare tipos mixtos ni emita avisos de casteo.
        df[mapeo.col_estimacion] = pd.to_numeric(df[mapeo.col_estimacion],
                                                 errors='coerce').astype('float64')
        for col in self._columnas_en_condiciones(pasadas, df.columns):
            if col == mapeo.col_estimacion or pd.api.types.is_numeric_dtype(df[col]):
                continue
            conv = pd.to_numeric(df[col], errors='coerce')
            if conv.notna().any():
                df[col] = conv

        # Validación tardía de las condiciones (una vez que existen todas las columnas).
        self._validar_condiciones(df, pasadas)

        centroides = df[[mapeo.bloque_xc, mapeo.bloque_yc, mapeo.bloque_zc]].to_numpy(dtype=float)
        tam = df[[mapeo.bloque_dx, mapeo.bloque_dy, mapeo.bloque_dz]].to_numpy(dtype=float)
        rot_m = np.eye(3) if rot_modelo is None else np.asarray(rot_modelo, dtype=float)

        if verbose:
            print('=' * 70)
            print(f'  Estimación IDW: {n_bloques:,} bloques | {len(df_muestras):,} muestras | '
                  f'variable: {mapeo.col_estimacion} | {len(pasadas)} pasada(s)')

        por_pasada, avisos = [], []
        for ip, pas in enumerate(pasadas):
            if progreso:
                progreso(ip / max(1, len(pasadas)), f'Pasada {pas.id} ({ip + 1}/{len(pasadas)})…')
            n_est, avisos_pasada = self._procesar_pasada(df, df_muestras, mapeo, pas, centroides,
                                                        tam, rot_m)
            avisos.extend(avisos_pasada)
            por_pasada.append({'id': pas.id, 'flag': pas.valor_flag, 'n_estimados': n_est})
            if verbose:
                print(f"  Pasada '{pas.id}' (flag {pas.valor_flag:g}): {n_est:,} bloques estimados")

        if ruta_salida:
            df.to_csv(ruta_salida, index=False, encoding='utf-8-sig')
        if progreso:
            progreso(1.0, 'Finalizado.')

        estim = pd.to_numeric(df[mapeo.col_estimacion], errors='coerce')
        n_estimados = int((estim.notna() & (estim != mapeo.valor_defecto)).sum())
        resumen = {
            'n_bloques': n_bloques,
            'n_muestras': len(df_muestras),
            'n_pasadas': len(pasadas),
            'por_pasada': por_pasada,
            'n_estimados': n_estimados,
            'n_sin_estimar': n_bloques - n_estimados,
            'ruta_salida': ruta_salida,
            'avisos': avisos,
        }
        if verbose:
            print('=' * 70)
            print(f'Estimación completada en {time.perf_counter() - t0:.2f}s: '
                  f'{n_estimados:,} de {n_bloques:,} bloques estimados')
        return df, resumen

    def estimar_por_padre(self, df_bloques, df_muestras, mapeo, pasadas, grilla,
                          ruta_salida=None, progreso=None):
        """Estima un modelo SUB-BLOQUEADO a resolución de bloque PADRE (réplica de Vulcan).

        Agrupa los sub-bloques por celda padre (según `grilla`, una GrillaModelo), estima UNA vez en
        el centroide del padre —con el tamaño del padre para la discretización, orientada según el
        bearing del modelo— y asigna la ley y los campos de reporte a todos los sub-bloques de esa
        celda. Devuelve (df_subbloques, resumen).

        El resumen y los mensajes de consola se expresan a nivel de SUB-BLOQUE (la resolución del df
        devuelto y del CSV), no de bloque padre: cada sub-bloque hereda el flag/pasada de su padre.
        """
        if not isinstance(grilla, GrillaModelo):
            raise ValueError('Se requiere una GrillaModelo válida para estimar por padre.')
        t0 = time.perf_counter()
        df = df_bloques.reset_index(drop=True).copy()
        pc = grilla.centroides_padre(
            df[[mapeo.bloque_xc, mapeo.bloque_yc, mapeo.bloque_zc]].to_numpy(dtype=float))
        pk = np.array([f'{x:.3f}|{y:.3f}|{z:.3f}' for x, y, z in pc])

        # Una fila representante por padre. Se copian las columnas del bloque referenciadas en las
        # condiciones (p. ej. el dominio 'envau'), salvo la de estimación, para no pre-rellenarla y
        # respetar la multipasada ('au_id eq -99').
        rep = pd.Series(pk).drop_duplicates().index.to_numpy()
        self._validar_orientaciones(df, pasadas)
        cols_cond = (self._columnas_en_condiciones(pasadas, df.columns) - {mapeo.col_estimacion}
                     | self._columnas_orientacion(df, pasadas))
        padres = pd.DataFrame({
            mapeo.bloque_xc: pc[rep, 0], mapeo.bloque_yc: pc[rep, 1], mapeo.bloque_zc: pc[rep, 2],
            mapeo.bloque_dx: float(grilla.tam_padre[0]),
            mapeo.bloque_dy: float(grilla.tam_padre[1]),
            mapeo.bloque_dz: float(grilla.tam_padre[2]),
        })
        for col in cols_cond:
            padres[col] = df[col].to_numpy()[rep]

        # verbose=False: la estimación por padre no imprime su propio resumen (los conteos a nivel de
        # padre confunden); abajo se imprime el resumen ya reexpresado a sub-bloques.
        df_pad, resumen = self.estimar(padres, df_muestras, mapeo, pasadas, progreso=progreso,
                                       rot_modelo=grilla.rotacion_modelo(), verbose=False)
        df_pad.index = pk[rep]

        cols_res = [mapeo.col_estimacion] + list(mapeo.columnas_reporte().values())
        for col in cols_res:
            df[col] = pd.Series(pk).map(df_pad[col]).to_numpy()

        if ruta_salida:
            df.to_csv(ruta_salida, index=False, encoding='utf-8-sig')

        n_padres = int(len(padres))
        resumen = self._expandir_resumen_a_subbloques(resumen, df, mapeo)
        resumen['n_padres'] = n_padres
        resumen['ruta_salida'] = ruta_salida
        self._imprimir_resumen_padre(resumen, mapeo, len(df_muestras), n_padres,
                                     time.perf_counter() - t0)
        return df, resumen

    @staticmethod
    def _expandir_resumen_a_subbloques(resumen, df, mapeo):
        """Reexpresa los conteos del resumen (a nivel de bloque padre) a nivel de SUB-BLOQUE, la
        resolución del df devuelto. Cada sub-bloque hereda el flag de pasada de su padre, así que el
        nº de sub-bloques de una pasada = sub-bloques con ese flag; el total son los sub-bloques con
        flag positivo (0 = sin estimar). Si no hay columna de flag mapeada, el total
        se deriva de la variable estimada y los conteos por pasada quedan como los del padre."""
        resumen = dict(resumen)
        resumen['por_pasada'] = [dict(p) for p in resumen.get('por_pasada', [])]
        resumen['n_bloques'] = len(df)
        flags = (pd.to_numeric(df[mapeo.flag], errors='coerce')
                 if mapeo.flag and mapeo.flag in df.columns else None)
        if flags is not None:
            for p in resumen['por_pasada']:
                p['n_estimados'] = int((flags == p['flag']).sum())
            # Total = bloques con algún flag de pasada. No es la suma de las pasadas: con varios dominios
            # las pasadas comparten flag (1..4) y cada una muestra el conteo de su flag.
            resumen['n_estimados'] = int((flags > 0).sum())
        else:
            estim = pd.to_numeric(df[mapeo.col_estimacion], errors='coerce')
            resumen['n_estimados'] = int((estim.notna() & (estim != mapeo.valor_defecto)).sum())
        resumen['n_sin_estimar'] = len(df) - resumen['n_estimados']
        return resumen

    @staticmethod
    def _imprimir_resumen_padre(resumen, mapeo, n_muestras, n_padres, elapsed):
        """Resumen de consola de una estimación por bloque padre, en conteos de SUB-BLOQUE."""
        print('=' * 70)
        print(f'  Estimación IDW: {resumen["n_bloques"]:,} bloques ({n_padres:,} padres) | '
              f'{n_muestras:,} muestras | variable: {mapeo.col_estimacion} | '
              f'{resumen["n_pasadas"]} pasada(s)')
        for p in resumen['por_pasada']:
            print(f"  Pasada '{p['id']}' (flag {p['flag']:g}): {p['n_estimados']:,} "
                  'bloques estimados')
        print('=' * 70)
        print(f'Estimación completada en {elapsed:.2f}s: '
              f'{resumen["n_estimados"]:,} de {resumen["n_bloques"]:,} bloques estimados')

    # ----------------------------------------------------------------------------------------------
    # Validación y preparación
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _col(df, nombre):
        """Nombre real de la columna: coincidencia exacta o, en su defecto, insensible a
        mayúsculas (útil al importar de Vulcan, que nombra 'LENGTH' donde el compósito tiene
        'length'). Devuelve None si no existe."""
        if not nombre:
            return None
        if nombre in df.columns:
            return nombre
        objetivo = str(nombre).strip().lower()
        for col in df.columns:
            if str(col).strip().lower() == objetivo:
                return col
        return None

    @classmethod
    def _validar(cls, df_bloques, df_muestras, mapeo, pasadas):
        if not isinstance(mapeo, MapeoCampos):
            raise ValueError('Se requiere un MapeoCampos válido.')
        if not pasadas:
            raise ValueError('Debe definir al menos una pasada de estimación.')

        ids = [p.id for p in pasadas]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"Pasadas con identificador duplicado: {', '.join(dup)}.")

        faltan_bloque = [c for c in (mapeo.bloque_xc, mapeo.bloque_yc, mapeo.bloque_zc,
                                     mapeo.bloque_dx, mapeo.bloque_dy, mapeo.bloque_dz)
                         if c not in df_bloques.columns]
        if faltan_bloque:
            raise ValueError('El modelo de bloques no tiene las columnas de geometría mapeadas: '
                             f"{', '.join(faltan_bloque)}.")

        cls._validar_orientaciones(df_bloques, pasadas)

        req = {'X': mapeo.x, 'Y': mapeo.y, 'Z': mapeo.z, 'ley': mapeo.ley}
        faltan_muestra = [f'{etq} ({nom or "sin definir"})' for etq, nom in req.items()
                          if cls._col(df_muestras, nom) is None]
        if faltan_muestra:
            raise ValueError('El compósito no tiene las columnas de muestra requeridas: '
                             f"{', '.join(faltan_muestra)}.")
        if mapeo.dominio and cls._col(df_muestras, mapeo.dominio) is None:
            raise ValueError(f"El compósito no tiene el campo de dominio '{mapeo.dominio}'.")

    @classmethod
    def _validar_orientaciones(cls, df_bloques, pasadas):
        """Las variables de bearing/plunge/dip por bloque deben existir en el modelo de bloques."""
        for p in pasadas:
            for var in p.variables_orientacion().values():
                if cls._col(df_bloques, var) is None:
                    raise ValueError(f"Pasada '{p.id}': el modelo de bloques no tiene la variable de "
                                     f"orientación '{var}' (Bearing/Plunge/Dip aceptan un número o el "
                                     'nombre de una columna del modelo).')

    @classmethod
    def _columnas_orientacion(cls, df_bloques, pasadas):
        """Columnas reales del modelo usadas como bearing/plunge/dip por bloque en alguna pasada."""
        return {cls._col(df_bloques, v) for p in pasadas for v in p.variables_orientacion().values()}

    @staticmethod
    def _preparar_columnas(df, mapeo):
        """Crea (si faltan) la columna de estimación y las de reporte con su valor inicial de
        'no estimado': valor_defecto para ley/distancias/nn; 0 para conteos y flag."""
        if mapeo.col_estimacion not in df.columns:
            df[mapeo.col_estimacion] = float(mapeo.valor_defecto)
        cero = {'nsamp', 'nholes', 'flag'}
        for attr, col in mapeo.columnas_reporte().items():
            if col not in df.columns:
                df[col] = 0.0 if attr in cero else float(mapeo.valor_defecto)

    @staticmethod
    def _columnas_en_condiciones(pasadas, columnas):
        """Columnas del bloque referenciadas (como identificadores) en alguna condición."""
        columnas = set(columnas)
        referidas = set()
        for pas in pasadas:
            for tok in re.findall(r'[A-Za-z_]\w*', traducir_condicion(pas.condicion)):
                if tok in columnas:
                    referidas.add(tok)
        return referidas

    @staticmethod
    def _validar_condiciones(df, pasadas):
        muestra = df.head(1)
        for pas in pasadas:
            cond = traducir_condicion(pas.condicion)
            if not cond:
                continue
            try:
                res = muestra.eval(cond, engine='python')
            except Exception as e:
                raise ValueError(f"Pasada '{pas.id}': condición inválida "
                                 f"'{pas.condicion}'.\n{e}") from e
            if np.asarray(res).dtype != bool:
                raise ValueError(f"Pasada '{pas.id}': la condición '{pas.condicion}' debe evaluar "
                                 'a verdadero/falso.')

    # ----------------------------------------------------------------------------------------------
    # Ejecución de una pasada
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _avisar(pas, motivo):
        """Emite un aviso de pasada por consola (mismo formato que `_chequear_duplicados`) y lo
        devuelve prefijado con el id, para que `estimar` lo acumule en el resumen: el usuario del
        módulo mira el diálogo final, no la consola."""
        print(f"  AVISO pasada '{pas.id}': {motivo}")
        return f'{pas.id}: {motivo}'

    @staticmethod
    def _motivo_sin_muestras(mapeo, pas, n_composito, n_dominio):
        """Por qué la pasada se quedó sin muestras. Distinguir el filtro de dominio del compósito
        inválido importa porque el primero es la causa habitual y no se ve en el resultado: un
        'Valor dominio' que no casa con ningún registro (p. ej. 40 contra 'VETA-40') deja la pasada
        muda sin error alguno."""
        dominios = pas.dominios
        filtra_dominio = bool(mapeo.dominio) and bool(dominios)
        etiqueta = ', '.join(dominios)
        if filtra_dominio and n_dominio == 0:
            filtro = (f'{mapeo.dominio} == {etiqueta}' if len(dominios) == 1
                      else f'{mapeo.dominio} en [{etiqueta}]')
            return (f'el filtro de dominio «{filtro}» no dejó ninguna '
                    f'de las {n_composito:,} muestras del compósito.')
        if filtra_dominio:
            return (f"de las {n_dominio:,} muestras del dominio {etiqueta}, ninguna tiene "
                    'coordenada y ley válidas.')
        return (f'ninguna de las {n_composito:,} muestras del compósito tiene coordenada y ley '
                'válidas.')

    def _procesar_pasada(self, df, df_muestras, mapeo, pas, centroides, tam, rot_modelo):
        """Devuelve (n_bloques_estimados, avisos). Los avisos explican por qué una pasada salió
        MUDA (0 bloques estimados), que es el fallo silencioso característico de este motor: una
        pasada mal configurada no lanza error, solo escribe menos de lo que el usuario cree."""
        # 1. Muestras de la pasada (filtro de dominio + limpieza de inválidos). El orden se conserva:
        #    la posición en estos arrays es el criterio de desempate determinista.
        s_xyz, s_g, s_l, s_hole, n_dominio = self._muestras_pasada(df_muestras, mapeo, pas)
        if s_xyz.shape[0] == 0:
            return 0, [self._avisar(pas, self._motivo_sin_muestras(
                mapeo, pas, len(df_muestras), n_dominio))]
        self._chequear_duplicados(s_xyz, s_hole, pas)

        # 2. Bloques objetivo según la condición evaluada sobre el modelo VIVO.
        cond = traducir_condicion(pas.condicion)
        if cond:
            mask = np.asarray(df.eval(cond, engine='python'))
            mask = np.where(pd.isna(mask), False, mask).astype(bool)
        else:
            mask = np.ones(len(df), dtype=bool)
        objetivo = np.nonzero(mask)[0]
        if objetivo.size == 0:
            if cond:
                motivo = (f'la condición «{pas.condicion}» no seleccionó ninguno de los '
                          f'{len(df):,} bloques del modelo.')
            else:
                motivo = 'el modelo de bloques está vacío: no hay nada que estimar.'
            return 0, [self._avisar(pas, motivo)]

        # 3. Orientación del elipsoide: fija (un solo grupo) o tomada de variables del modelo de
        #    bloques (un grupo por cada combinación distinta de bearing/plunge/dip).
        grupos, avisos = self._grupos_orientacion(df, objetivo, pas)
        if not grupos:
            return 0, avisos

        disc_unit = _puntos_discretizacion_unitarios(pas.disc_x, pas.disc_y, pas.disc_z)
        rot_oct = _matriz_octante(pas.oct_bearing, pas.oct_plunge, pas.oct_dip)  # marco de octantes
        res_idx, res = [], {k: [] for k in ('est', 'nsamp', 'nholes', 'dcc', 'dca', 'nn', 'acv', 'aav')}
        for pas_g, objetivo_g in grupos:
            self._estimar_grupo(pas_g, objetivo_g, s_xyz, s_g, s_l, s_hole, centroides, tam,
                                rot_modelo, disc_unit, rot_oct, res_idx, res)

        # 7. Escritura vectorizada de los resultados de la pasada.
        if res_idx:
            self._escribir_resultados(df, mapeo, pas, np.array(res_idx), res)
            return len(res_idx), avisos

        # Había muestras y había bloques, pero ninguno reunió el mínimo dentro del elipsoide: es el
        # tercer modo de pasada muda, y el único que no se ve mirando la configuración.
        return 0, avisos + [self._avisar(pas, (
            f'ninguno de los {objetivo.size:,} bloques seleccionados reunió las '
            f'{pas.min_muestras} muestra(s) mínimas dentro del elipsoide '
            f'{pas.mayor:g}/{pas.semi:g}/{pas.menor:g}.'))]

    def _grupos_orientacion(self, df, objetivo, pas):
        """Agrupa los bloques objetivo por orientación del elipsoide. Devuelve ([(pasada, bloques)],
        avisos). Sin variables de orientación es un solo grupo con la pasada tal cual. Con ellas
        (`var_bearing`/`var_plunge`/`var_dip`) cada bloque usa el ángulo de su propia fila, y se arma
        un grupo por combinación distinta; los bloques con ángulo no numérico no se estiman."""
        variables = pas.variables_orientacion()
        if not variables:
            return [(pas, objetivo)], []

        angulos = np.empty((objetivo.size, 3))
        for k, nombre in enumerate(('bearing', 'plunge', 'dip')):
            if nombre in variables:
                col = self._col(df, variables[nombre])
                valores = pd.to_numeric(df[col], errors='coerce').to_numpy(dtype=float)
                angulos[:, k] = valores[objetivo]
            else:
                angulos[:, k] = getattr(pas, nombre)
        validos = np.isfinite(angulos).all(axis=1)
        avisos = []
        if not validos.all():
            avisos.append(self._avisar(pas, (
                f'{int((~validos).sum()):,} bloques seleccionados tienen una orientación no numérica '
                f'({", ".join(variables.values())}) y no se estimaron.')))
        if not validos.any():
            return [], avisos

        unicos, inverso = np.unique(angulos[validos], axis=0, return_inverse=True)
        inverso = np.asarray(inverso).reshape(-1)
        orden = np.argsort(inverso, kind='stable')           # conserva el orden de bloques en cada grupo
        cortes = np.cumsum(np.bincount(inverso, minlength=len(unicos)))[:-1]
        partes = np.split(objetivo[validos][orden], cortes)
        print(f"  Pasada '{pas.id}': orientación por bloque ({', '.join(variables.values())}) -> "
              f'{len(unicos):,} orientación(es) distinta(s) en {int(validos.sum()):,} bloques')
        grupos = [(replace(pas, bearing=float(u[0]), plunge=float(u[1]), dip=float(u[2])), bloques)
                  for u, bloques in zip(unicos, partes)]
        return grupos, avisos

    def _estimar_grupo(self, pas, objetivo, s_xyz, s_g, s_l, s_hole, centroides, tam, rot_modelo,
                       disc_unit, rot_oct, res_idx, res):
        """Estima los bloques `objetivo` con una única orientación del elipsoide (la de `pas`) y
        agrega sus resultados a `res_idx`/`res`."""
        # 4. Transformadas: búsqueda (elipsoide -> esfera unitaria) y ponderación.
        rot = _matriz_rotacion(pas.bearing, pas.plunge, pas.dip)
        t_busq = np.diag([1.0 / pas.mayor, 1.0 / pas.semi, 1.0 / pas.menor]) @ rot
        wr = pas.radios_ponderacion()
        t_pond = np.diag(1.0 / wr) @ rot

        # 5. cKDTree en el espacio de búsqueda (distancia euclídea allí = distancia del elipsoide).
        s_busq = s_xyz @ t_busq.T
        arbol = cKDTree(s_busq)

        # Candidatos por bloque. Dos vías equivalentes:
        #      - General: query_ball_point con el radio del elipsoide (+ margen de discretización).
        #      - k-NN escalado (pasadas CON tope de muestras y SIN límite por octante, como la
        #        pasada automática de distancia a 3 sondajes): con un radio de búsqueda enorme el
        #        ball-query materializa casi todo el compósito POR BLOQUE (tiempo y memoria
        #        O(n_muestras) por bloque, más el ordenamiento); como la selección es nearest-first
        #        y se detiene en max_muestras, basta con traer el prefijo más cercano.
        b_busq = centroides[objetivo] @ t_busq.T
        if pas.max_muestras > 0 and pas.max_octante == 0:
            candidatos = self._candidatos_knn(arbol, b_busq, s_hole, pas)
        else:
            delta = self._margen_discretizacion(tam[objetivo], t_busq)
            candidatos = arbol.query_ball_point(b_busq, r=1.0 + delta)

        # 6. Estimación bloque a bloque (selección + IDW vectorizado sobre puntos de discretización).
        for j, b in enumerate(objetivo):
            cand = np.asarray(candidatos[j], dtype=int)
            if cand.size == 0:
                continue
            deltas_b = s_busq[cand] - b_busq[j]           # deltas en espacio de búsqueda
            dist_a = np.sqrt((deltas_b ** 2).sum(axis=1))  # distancia anisotrópica (elipsoide)
            dentro = dist_a <= 1.0                          # selección desde el centroide
            if not dentro.any():
                continue
            cand, dist_a = cand[dentro], dist_a[dentro]

            # Orden por distancia con desempate determinista por índice original de muestra.
            orden = np.lexsort((cand, dist_a))
            cand, dist_a = cand[orden], dist_a[orden]

            # Octante en el marco propio de Vulcan (rot_oct), desde el centro del bloque; NO el del
            # elipsoide de búsqueda. En modo padre, centroides[b] es el centroide del bloque padre.
            deltas_oct = (s_xyz[cand] - centroides[b]) @ rot_oct.T
            sel = self._seleccionar(cand, deltas_oct, s_hole, pas)
            if len(sel) < pas.min_muestras:
                continue

            valores = self._idw_bloque(cand[sel], centroides[b], tam[b], disc_unit, rot_modelo,
                                       s_xyz, s_g, s_l, s_hole, t_pond, pas.potencia)
            res_idx.append(b)
            for k, v in valores.items():
                res[k].append(v)

    def _muestras_pasada(self, df_muestras, mapeo, pas):
        """Arrays (xyz, ley, longitud, sondaje) de la pasada más `n_dominio`: filtra por dominio y
        descarta muestras con coordenada o ley no finita. Preserva el orden original (desempate
        determinista).

        `n_dominio` es cuántas muestras sobrevivieron al filtro de dominio, ANTES del descarte de no
        finitas. Solo sirve para que `_procesar_pasada` pueda decir por qué se quedó sin muestras
        (filtro de dominio que no casa vs. compósito con coordenadas o leyes inválidas); no
        interviene en el cálculo."""
        col_x, col_y, col_z = (self._col(df_muestras, c) for c in (mapeo.x, mapeo.y, mapeo.z))
        col_ley = self._col(df_muestras, mapeo.ley)
        col_dom = self._col(df_muestras, mapeo.dominio)
        col_lon = self._col(df_muestras, mapeo.longitud)
        col_hole = self._col(df_muestras, mapeo.sondaje)

        sub = df_muestras
        dominios = pas.dominios
        if col_dom and dominios:
            col = df_muestras[col_dom]
            numerica = pd.to_numeric(col, errors='coerce')
            texto = col.astype(str).str.strip()
            mascara = pd.Series(False, index=df_muestras.index)
            for dominio in dominios:   # un dominio numérico casa por valor; si no, por texto
                try:
                    mascara |= numerica == float(dominio)
                except ValueError:
                    mascara |= texto == dominio
            sub = df_muestras[mascara]
        if sub.empty:
            vacio = np.empty((0, 3))
            return vacio, np.array([]), np.array([]), np.array([], dtype=object), 0

        xyz = sub[[col_x, col_y, col_z]].to_numpy(dtype=float)
        g = pd.to_numeric(sub[col_ley], errors='coerce').to_numpy(dtype=float)
        if col_lon:
            l = pd.to_numeric(sub[col_lon], errors='coerce').to_numpy(dtype=float)
            l = np.where(np.isfinite(l) & (l > 0), l, 1.0)
        else:
            l = np.ones(len(sub))
        hole = (sub[col_hole].astype(str).to_numpy() if col_hole
                else np.array([''] * len(sub), dtype=object))

        ok = np.isfinite(g) & np.isfinite(xyz).all(axis=1)
        return xyz[ok], g[ok], l[ok], hole[ok], int(len(sub))

    @staticmethod
    def _margen_discretizacion(tam_obj, t_busq):
        """Cota superior de la distancia (en espacio de búsqueda) del centroide a la esquina del
        bloque, para expandir el radio de consulta del árbol. Usa |T| · (tamaño/2) como cota."""
        if tam_obj.size == 0:
            return 0.0
        semis = np.abs(tam_obj).max(axis=0) / 2.0
        return float(np.linalg.norm(np.abs(t_busq) @ semis))

    def _candidatos_knn(self, arbol, b_busq, s_hole, pas):
        """Candidatos por bloque vía k-vecinos más cercanos con escalado (k×4), equivalente al
        ball-query para pasadas nearest-first con tope de muestras y sin límite por octante.

        Se consulta el árbol por tandas de k vecinos; un bloque solo escala a la tanda siguiente
        si su selección greedy no se completó con los candidatos traídos Y todavía pueden existir
        más muestras dentro del elipsoide (la tanda entera cayó dentro y quedan muestras sin ver).
        La selección definitiva la hace la vía normal sobre el prefijo devuelto; aquí solo se
        garantiza que ese prefijo contiene todo lo seleccionable, por lo que el resultado es
        idéntico al del ball-query. Matiz de determinismo: un empate EXACTO de distancia partido
        justo por el corte de una tanda se resuelve por cercanía y no por índice de muestra; no
        altera las distancias reportadas (ambas muestras equidistan). El octante no participa
        (esta vía exige max_octante=0), por eso a _seleccionar se le pasan deltas nulos."""
        n_muestras = int(arbol.n)
        candidatos = [np.empty(0, dtype=np.int64)] * b_busq.shape[0]
        pendientes = np.arange(b_busq.shape[0])
        k = max(32, 4 * pas.max_muestras)
        while pendientes.size:
            k_ef = min(k, n_muestras)
            dist, idx = arbol.query(b_busq[pendientes], k=k_ef, workers=-1)
            if k_ef == 1:
                dist, idx = dist[:, None], idx[:, None]
            faltan = []
            for fila, j in enumerate(pendientes):
                dentro = dist[fila] <= 1.0
                cand = idx[fila][dentro]
                candidatos[j] = cand
                # Si la tanda ya salió del elipsoide o no quedan muestras por ver, el prefijo está
                # completo; si no, escalar solo cuando la selección greedy no se llenó con él.
                if k_ef == n_muestras or not bool(dentro.all()):
                    continue
                orden = np.lexsort((cand, dist[fila][dentro]))
                sel = self._seleccionar(cand[orden], np.zeros((cand.size, 3)), s_hole, pas)
                if len(sel) < pas.max_muestras:
                    faltan.append(j)
            pendientes = np.asarray(faltan, dtype=np.int64)
            k *= 4
        return candidatos

    @staticmethod
    def _chequear_duplicados(s_xyz, s_hole, pas):
        """Réplica del 'Distance to check duplicate samples' de Vulcan: con umbral >= 0, dos
        muestras a distancia <= umbral son duplicadas y se DETIENE la estimación; con umbral
        negativo (desactivado) sólo se AVISA por consola de las muestras en posición idéntica,
        como hace Vulcan."""
        activo = pas.dist_duplicados is not None and pas.dist_duplicados >= 0
        radio = float(pas.dist_duplicados) if activo else 0.0
        pares = sorted(cKDTree(s_xyz).query_pairs(r=radio))
        if not pares:
            return
        i, j = pares[0]
        detalle = (f'{len(pares)} par(es); ej.: ({s_xyz[i][0]:.3f}, {s_xyz[i][1]:.3f}, '
                   f'{s_xyz[i][2]:.3f}) sondajes {s_hole[i]} / {s_hole[j]}')
        if activo:
            raise ValueError(
                f"Pasada '{pas.id}': muestras duplicadas a distancia <= {pas.dist_duplicados:g} "
                f'({detalle}). Corrija el compósito o desactive el chequeo (-1).')
        print(f"  AVISO pasada '{pas.id}': muestras en posición idéntica (posibles duplicados): "
              f'{detalle}')

    @staticmethod
    def _seleccionar(cand, deltas_oct, s_hole, pas):
        """Selección greedy nearest-first respetando octante (en el marco de octantes de Vulcan),
        máx por sondaje, máx sondajes y máx muestras. Devuelve posiciones (en los arrays
        ordenados) elegidas. `deltas_oct` son los vectores muestra-centro en el marco de octantes."""
        sel = []
        por_octante, por_sondaje, sondajes = {}, {}, set()
        for k in range(cand.shape[0]):
            i = cand[k]
            u = deltas_oct[k]  # delta en el marco de octantes; los signos definen el octante
            octante = (int(u[0] >= 0) << 2) | (int(u[1] >= 0) << 1) | int(u[2] >= 0)
            if pas.max_octante > 0 and por_octante.get(octante, 0) >= pas.max_octante:
                continue
            hole = s_hole[i]
            if pas.max_por_sondaje > 0 and por_sondaje.get(hole, 0) >= pas.max_por_sondaje:
                continue
            if pas.max_sondajes > 0 and hole not in sondajes and len(sondajes) >= pas.max_sondajes:
                continue
            sel.append(k)
            por_octante[octante] = por_octante.get(octante, 0) + 1
            por_sondaje[hole] = por_sondaje.get(hole, 0) + 1
            sondajes.add(hole)
            if pas.max_muestras > 0 and len(sel) >= pas.max_muestras:
                break
        return sel

    @staticmethod
    def _idw_bloque(idx, centro, tam_b, disc_unit, rot_modelo, s_xyz, s_g, s_l, s_hole, t_pond,
                    potencia):
        """IDW del bloque con discretización, vectorizado con broadcasting NumPy (M muestras × N
        puntos). Devuelve estimación y campos de reporte.

        Fórmula de Vulcan (calibrada por ingeniería inversa de los pesos por muestra que imprime al
        extraer muestras seleccionadas; 6 bloques reproducidos con error < 1e-4):
          - los puntos de discretización se orientan según los EJES DEL MODELO (`rot_modelo`), no
            los del mundo;
          - el peso de la muestra i es UNO solo, agregado sobre los N puntos:
            ``w_i = length_i · Σ_j (1/d_ij^p)``, y la estimación es la única media ponderada
            ``Σ w_i·g_i / Σ w_i`` (NO el promedio de N estimaciones por punto, que es distinto).
            La columna 'Weight' que imprime Vulcan es este w_i SIN la longitud, normalizado.
        """
        pos = s_xyz[idx]                    # (M, 3) coordenadas de las muestras seleccionadas
        g = s_g[idx]                        # (M,)
        l = s_l[idx]                        # (M,)
        disc = centro + (disc_unit * tam_b) @ rot_modelo   # (N, 3) puntos absolutos (marco modelo)

        diff = pos[:, None, :] - disc[None, :, :]     # (M, N, 3)
        diff_t = diff @ t_pond.T                       # a espacio de ponderación
        dw = np.sqrt((diff_t ** 2).sum(axis=2))        # (M, N)
        dw = np.maximum(dw, _EPS_DIST)
        w = l * (1.0 / dw ** potencia).sum(axis=1)     # (M,) peso agregado por muestra
        est = float((w * g).sum() / w.sum())

        # Distancias de reporte desde el CENTROIDE: cartesiana y anisotrópica de PONDERACIÓN (la
        # misma métrica t_pond con que se ponderó, en unidades de terreno, como las reporta Vulcan).
        dist_c = np.sqrt(((pos - centro) ** 2).sum(axis=1))
        dist_a = np.sqrt((((pos - centro) @ t_pond.T) ** 2).sum(axis=1))
        i_cerca = int(np.argmin(dist_a))              # muestra anisotrópicamente más cercana
        return {
            'est': est,
            'nsamp': int(idx.size),
            'nholes': int(len(set(s_hole[idx].tolist()))),
            'dcc': float(dist_c.min()),
            'dca': float(dist_a.min()),
            'nn': float(g[i_cerca]),
            'acv': float(dist_c.mean()),
            'aav': float(dist_a.mean()),
        }

    @staticmethod
    def _escribir_resultados(df, mapeo, pas, idx, res):
        """Escribe (vectorizado) la estimación, el flag y los campos de reporte de los bloques
        estimados en esta pasada."""
        df.loc[idx, mapeo.col_estimacion] = np.asarray(res['est'], dtype=float)
        if mapeo.flag:
            df.loc[idx, mapeo.flag] = float(pas.valor_flag)
        reporte = {'nsamp': 'nsamp', 'nholes': 'nholes', 'distclose_cart': 'dcc',
                   'distclose_anisot': 'dca', 'nn': 'nn', 'avgdist_cart': 'acv',
                   'avgdist_anisot': 'aav'}
        for attr, clave in reporte.items():
            col = getattr(mapeo, attr)
            if col:
                df.loc[idx, col] = np.asarray(res[clave], dtype=float)


# ==================================================================================================
# Importador del Estimation Parameter File de Vulcan
# ==================================================================================================
# Encabezado del CSV de Vulcan (normalizado a minúsculas/espacios) -> destino en el motor.
def _norm(clave):
    return re.sub(r'\s+', ' ', str(clave).replace('﻿', '').strip().lower())


def cargar_parametros_vulcan(ruta_csv):
    """Lee un Estimation Parameter File de Vulcan (CSV) y devuelve (MapeoCampos, [PasadaIDW]).

    El mapeo de campos se toma de la primera fila (se asume común a todas las pasadas); cada fila
    genera una PasadaIDW. Tolera el BOM y variaciones de mayúsculas/espacios en los encabezados.
    """
    with open(ruta_csv, 'r', encoding='utf-8-sig', newline='') as f:
        filas = list(csv.reader(f))
    if len(filas) < 2:
        raise ValueError('El archivo de parámetros de Vulcan no tiene filas de datos.')

    encabezados = [_norm(c) for c in filas[0]]

    def celda(fila, *claves, defecto=''):
        for clave in claves:
            clave = _norm(clave)
            if clave in encabezados:
                valor = fila[encabezados.index(clave)] if encabezados.index(clave) < len(fila) else ''
                return valor.strip()
        return defecto

    def num(valor, defecto=0.0):
        try:
            return float(str(valor).strip())
        except (TypeError, ValueError):
            return defecto

    def ent(valor, defecto=0):
        try:
            return int(round(float(str(valor).strip())))
        except (TypeError, ValueError):
            return defecto

    primera = filas[1]
    mapeo = MapeoCampos(
        x=celda(primera, 'Location X Field', defecto='MIDX'),
        y=celda(primera, 'Location Y Field', defecto='MIDY'),
        z=celda(primera, 'Location Z Field', defecto='MIDZ'),
        ley=celda(primera, 'Assay Field'),
        longitud=celda(primera, 'Weighting Variable', defecto='length'),
        dominio=celda(primera, 'W Field'),
        col_estimacion=celda(primera, 'Grade Variable', defecto='au_idw'),
        valor_defecto=num(celda(primera, 'Grade Variable Default'), Const.VAL_DEFECTO),
        nsamp=celda(primera, 'Number of Samples Variable'),
        nholes=celda(primera, 'Number of Holes Variable'),
        flag=celda(primera, 'Estimation flag Variable'),
        distclose_cart=celda(primera, 'Distance to Closest Sample (Cartesian)'),
        distclose_anisot=celda(primera, 'Distance to Closest Sample (Anisotropic)'),
        nn=celda(primera, 'Grade of Closest Sample (Anisotropic)'),
        avgdist_cart=celda(primera, 'Cartesian Average Distance'),
        avgdist_anisot=celda(primera, 'Anisotropic (1) Average Distance'),
    )

    pasadas = []
    for fila in filas[1:]:
        if not any(c.strip() for c in fila):
            continue
        pasadas.append(PasadaIDW(
            id=celda(fila, 'Estimation ID', 'Estimation Description', defecto=f'pasada{len(pasadas) + 1}'),
            potencia=num(celda(fila, 'Power'), 2.0),
            mayor=num(celda(fila, 'Major Axis'), 100.0),
            semi=num(celda(fila, 'Semi-Major Axis'), 100.0),
            menor=num(celda(fila, 'Minor Axis'), 100.0),
            bearing=num(celda(fila, 'Bearing(Z)', 'Bearing (Z)')),
            plunge=num(celda(fila, 'Plunge(Y)', 'Plunge (Y)')),
            dip=num(celda(fila, 'Dip (X)', 'Dip(X)')),
            # Rotación del octante ('Select octant rotation type'). Suele NO venir en el CSV de
            # parámetros de Vulcan; por defecto 90/0/0 ('Cartesian axes'). Se ajusta en la grilla.
            oct_bearing=num(celda(fila, 'Octant Bearing(Z)', 'Octant Bearing'), 90.0),
            oct_plunge=num(celda(fila, 'Octant Plunge(Y)', 'Octant Plunge'), 0.0),
            oct_dip=num(celda(fila, 'Octant Dip (X)', 'Octant Dip'), 0.0),
            anis_x=num(celda(fila, 'Inverse Anisotropic X Radius'), 1.0),
            anis_y=num(celda(fila, 'Inverse Anisotropic Y Radius'), 1.0),
            anis_z=num(celda(fila, 'Inverse Anisotropic Z Radius'), 1.0),
            normalizar_radios=celda(fila, 'Normalise Radii to Search Radii').upper().startswith('Y'),
            min_muestras=ent(celda(fila, 'Min Samples per Est'), 1),
            max_muestras=ent(celda(fila, 'Max Samples per Est'), 0),
            max_octante=ent(celda(fila, 'Max Samples per Octant'), 0),
            max_por_sondaje=ent(celda(fila, 'Maximun Samples per Drillhole',
                                       'Maximum Samples per Drillhole'), 0),
            max_sondajes=ent(celda(fila, 'Maximun Drillholes per Estimate',
                                    'Maximum Drillholes per Estimate'), 0),
            disc_x=ent(celda(fila, 'Discretisation Steps in X Dir'), 1),
            disc_y=ent(celda(fila, 'Discretisation steps in Y Dir', 'Discretisation Steps in Y Dir'), 1),
            disc_z=ent(celda(fila, 'Discretisation Steps in Z Dir'), 1),
            dist_duplicados=num(celda(fila, 'Distance to Check Duplicate Samples'), -1.0),
            valor_flag=num(celda(fila, 'Estimation Flag Default'), len(pasadas) + 1),
            dominio_valor=(celda(fila, 'Specific W Values') or None),
            condicion=celda(fila, 'Condition'),
        ))
    if not pasadas:
        raise ValueError('El archivo de parámetros de Vulcan no contiene pasadas válidas.')
    return mapeo, pasadas


# ==================================================================================================
# Importador del Block Estimation File (.bef) de Vulcan
# ==================================================================================================
def _leer_bloques_bef(ruta_bef):
    """Dict {nombre_de_bloque: {clave: valor}} con los BEGIN$DEF/END$DEF de un BEF de Vulcan.

    Los valores se devuelven como texto sin comillas y sin relleno (los ' ' de Vulcan quedan '').
    Las tablas anidadas BEGIN$TAB/END$TAB y las líneas de comentario (*) se ignoran."""
    bloques = {}
    actual = None
    en_tab = False
    with open(ruta_bef, 'r', encoding='latin-1') as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith('*'):
                continue
            if linea.startswith('BEGIN$TAB'):
                en_tab = True
                continue
            if linea.startswith('END$TAB'):
                en_tab = False
                continue
            if en_tab:
                continue
            if linea.startswith('BEGIN$DEF '):
                actual = {}
                bloques[linea[len('BEGIN$DEF '):].strip()] = actual
                continue
            if linea.startswith('END$DEF') or linea.startswith('END$FILE'):
                actual = None
                continue
            if actual is None or '=' not in linea:
                continue
            clave, valor = linea.split('=', 1)
            valor = valor.strip()
            if len(valor) >= 2 and valor.startswith("'") and valor.endswith("'"):
                valor = valor[1:-1]
            actual[clave.strip()] = valor.strip()
    return bloques


def cargar_parametros_bef(ruta_bef):
    """Lee un Block Estimation File (.bef) de Vulcan y devuelve (MapeoCampos, [PasadaIDW]).

    El BEF es el archivo de texto donde Vulcan guarda las especificaciones de estimación
    (BEGIN$DEF <id>_<sección> ... END$DEF). Cada <id> con sección krige_parms es una pasada; solo
    se importan las de tipo KNIS (Inverso a la Distancia; p. ej. el kriging es KNOB y se omite).
    El mapeo de campos se toma de la primera pasada (secciones data_format y data_domain), igual
    que en el importador del Estimation Parameter File (CSV)."""
    bloques = _leer_bloques_bef(ruta_bef)

    sufijo = '_krige_parms'
    ids = [nombre[:-len(sufijo)] for nombre in bloques if nombre.endswith(sufijo)]
    if not ids:
        raise ValueError('El archivo BEF no contiene estimaciones (bloques *_krige_parms).')

    ids_idw, omitidas = [], []
    for id_est in ids:
        tipo = bloques.get(f'{id_est}_type', {}).get('type', 'KNIS')
        (ids_idw if tipo == 'KNIS' else omitidas).append((id_est, tipo))
    if not ids_idw:
        tipos = ', '.join(sorted({tipo for _, tipo in omitidas}))
        raise ValueError('El BEF no contiene estimaciones IDW (tipo KNIS); se encontró: '
                         f'{tipos}. Este módulo solo importa Inverso a la Distancia.')
    for id_est, tipo in omitidas:
        print(f"AVISO BEF: se omite la estimación '{id_est}' (tipo {tipo}; no es KNIS/IDW).")

    def num(valor, defecto=0.0):
        try:
            return float(str(valor).strip())
        except (TypeError, ValueError):
            return defecto

    def ent(valor, defecto=0):
        try:
            return int(round(float(str(valor).strip())))
        except (TypeError, ValueError):
            return defecto

    def si(valor):
        return str(valor).strip().upper().startswith('Y')

    fmt = bloques.get(f'{ids_idw[0][0]}_data_format', {})
    dom = bloques.get(f'{ids_idw[0][0]}_data_domain', {})
    mapeo = MapeoCampos(
        x=fmt.get('x_field') or 'MIDX',
        y=fmt.get('y_field') or 'MIDY',
        z=fmt.get('z_field') or 'MIDZ',
        ley=fmt.get('input_var_1', ''),
        longitud=fmt.get('sg_field') or 'length',
        dominio=dom.get('w_field', ''),
        sondaje=fmt.get('h_field', ''),
        col_estimacion=fmt.get('variable') or 'au_idw',
        valor_defecto=num(fmt.get('variable_default'), Const.VAL_DEFECTO),
        nsamp=fmt.get('num_samples', ''),
        nholes=fmt.get('num_holes', ''),
        flag=fmt.get('flag_variable', ''),
        distclose_cart=fmt.get('dist_c_ca', ''),
        distclose_anisot=fmt.get('dist_c_an', ''),
        nn=fmt.get('grade_c_an', ''),
        avgdist_cart=fmt.get('cart_distance', ''),
        avgdist_anisot=fmt.get('aniso_distance', ''),
    )

    pasadas = []
    for id_est, _tipo in ids_idw:
        kp = bloques.get(f'{id_est}_krige_parms', {})
        fmt = bloques.get(f'{id_est}_data_format', {})
        dom = bloques.get(f'{id_est}_data_domain', {})
        condicion = bloques.get(f'{id_est}_block_match', {}).get('condition', '')
        sw = dom.get('specific_w', '').strip()
        if sw:
            try:
                dominio_valor = '%g' % float(sw)
            except ValueError:
                dominio_valor = sw
        else:
            dominio_valor = None
        pasadas.append(PasadaIDW(
            id=fmt.get('description') or id_est,
            potencia=num(kp.get('power'), 2.0),
            mayor=num(kp.get('major_search'), 100.0),
            semi=num(kp.get('semi_search'), 100.0),
            menor=num(kp.get('minor_search'), 100.0),
            # Ángulos del elipsoide: alpha=Bearing(Z), zeta=Plunge(Y), beta=Dip(X). ¡NO es
            # alfabético! Verificado contra el Estimation Parameter File (CSV) exportado por
            # Vulcan de la misma estimación (40_AU_id: alpha 180/beta -65/zeta 90 = CSV
            # Bearing 180/Plunge 90/Dip -65).
            bearing=num(kp.get('rot_alpha')),
            plunge=num(kp.get('rot_zeta')),
            dip=num(kp.get('rot_beta')),
            anis_x=num(kp.get('aniso_x'), 1.0),
            anis_y=num(kp.get('aniso_y'), 1.0),
            anis_z=num(kp.get('aniso_z'), 1.0),
            normalizar_radios=si(kp.get('normalise_radii')),
            min_muestras=ent(kp.get('min_samples'), 1),
            max_muestras=ent(kp.get('max_samples'), 0),
            # i_search='O' activa la búsqueda por octantes; con otro valor el límite no aplica.
            max_octante=(ent(kp.get('max_sam_oct'), 0)
                         if str(kp.get('i_search', '')).strip().upper() == 'O' else 0),
            # A diferencia del CSV de parámetros (que exporta dh_limit/est_dh_max aunque el check
            # esté apagado), el BEF conserva los flags use_*: aquí se respetan.
            max_por_sondaje=ent(kp.get('dh_limit'), 0) if si(kp.get('use_dh_limit')) else 0,
            max_sondajes=ent(kp.get('est_dh_max'), 0) if si(kp.get('use_est_limit')) else 0,
            disc_x=ent(kp.get('x_disc_pnts'), 1),
            disc_y=ent(kp.get('y_disc_pnts'), 1),
            disc_z=ent(kp.get('z_disc_pnts'), 1),
            dist_duplicados=num(kp.get('tol_duplicates'), -1.0),
            valor_flag=num(fmt.get('flag_value'), len(pasadas) + 1),
            dominio_valor=dominio_valor,
            condicion=condicion,
        ))
    return mapeo, pasadas


# ==================================================================================================
# Resumen legible
# ==================================================================================================
def texto_resumen(resumen):
    """Texto multilínea con el resumen del proceso (para mostrar en la GUI)."""
    lineas = [f"Estimación IDW: {resumen['n_bloques']:,} bloques | {resumen['n_muestras']:,} "
              f"muestras | {resumen['n_pasadas']} pasada(s).", '']
    lineas.append('Bloques estimados por pasada:')
    for p in resumen['por_pasada']:
        lineas.append(f"    {p['id']} (flag {p['flag']:g}): {p['n_estimados']:,} bloques")
    lineas.append('')
    lineas.append(f"Total estimado: {resumen['n_estimados']:,} de {resumen['n_bloques']:,} bloques "
                  f"({resumen['n_sin_estimar']:,} sin estimar).")
    # Los avisos explican las pasadas que salieron mudas. Van al diálogo además de la consola
    # porque una pasada que estima 0 bloques no se distingue, en el conteo, de una que no tocaba.
    if resumen.get('avisos'):
        lineas.append('')
        lineas.append('Avisos:')
        for aviso in resumen['avisos']:
            lineas.append(f'    • {aviso}')
    if resumen.get('ruta_salida'):
        lineas.append('')
        lineas.append(f"Archivo CSV: {resumen['ruta_salida']}")
    return '\n'.join(lineas)
