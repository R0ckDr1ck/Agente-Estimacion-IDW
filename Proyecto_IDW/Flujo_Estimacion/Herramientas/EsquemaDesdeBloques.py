"""
Nombre : EsquemaDesdeBloques.py
Propósito: Identifica el esquema de grilla de un modelo de bloques a partir de su CSV de bloques (centroides y
           dimensiones): origen, extensión, tamaño de bloque padre, tamaño de subbloque y bearing. Es la
           operación inversa de EsquemaGrilla (Herramientas/ModeloBloques.py) y devuelve los mismos campos que
           la pestaña «Modelo de bloques» pide: origen_x/y/z, extension_x/y/z, x/y/z_size, sub_x/y/z, bearing.

           Convenciones (las de EsquemaGrilla, réplica de Vulcan):
             - origen: esquina inferior del modelo en coordenadas de mundo (el punto local (0,0,0)).
             - extensión: LONGITUD en metros del modelo en cada eje local (n.º de bloques padre x tamaño).
             - bearing: azimut del eje X local (90 = modelo sin rotación, X local = Este). Plunge y Dip no
               existen. X = ox + u·sin(θ) − v·cos(θ);  Y = oy + u·cos(θ) + v·sin(θ).
             - tamaño de bloque padre = mayor DX/DY/DZ del modelo; subbloque = menor DX/DY/DZ (si todos los
               bloques miden lo mismo no hay subbloqueo y el subbloque se informa igual al padre).

           Cómo se identifica:
             - Tamaños: de los valores de DX, DY y DZ.
             - Bearing: la dirección de los bloques vecinos del mismo tamaño da el ángulo de la grilla módulo
               90°; se afina maximizando la coherencia de las esquinas de los bloques con la malla del
               subbloque, y esa misma coherencia decide si X y Y van intercambiados cuando sus tamaños
               difieren.
             - Origen: la esquina mínima del modelo llevada a la malla de bloques padre (la fase se toma de los
               bloques de tamaño padre). Si faltan bloques en la esquina mínima (modelo recortado) el origen es
               la esquina de malla más cercana por dentro, porque los bloques ausentes no dejan huella.
             - Extensión: lo necesario para cubrir el último bloque, en múltiplos del tamaño padre.

           Límites: con bloques cuadrados en planta el bearing solo queda definido módulo 90° (X e Y se
           pueden intercambiar); se informa el menor y se avisa. No detecta Plunge ni Dip.
           No depende de Qt.
Uso      : python EsquemaDesdeBloques.py modelo_bloques.csv [XC YC ZC DX DY DZ]
Creacion : 13-Noviembre-2026
"""

import sys
import math

import numpy as np
import pandas as pd

BEARING_DEFECTO = 90.0
COLUMNAS_DEFECTO = ('XC', 'YC', 'ZC', 'DX', 'DY', 'DZ')

# Sinónimos (en mayúsculas) de las columnas de geometría, para CSV exportados por otros programas.
SINONIMOS = {
    'XC': ['XC', 'XCENTRE', 'XCENTER', 'X_CENTRE', 'X_CENTER', 'XCENTRO', 'MIDX', 'X'],
    'YC': ['YC', 'YCENTRE', 'YCENTER', 'Y_CENTRE', 'Y_CENTER', 'YCENTRO', 'MIDY', 'Y'],
    'ZC': ['ZC', 'ZCENTRE', 'ZCENTER', 'Z_CENTRE', 'Z_CENTER', 'ZCENTRO', 'MIDZ', 'Z'],
    'DX': ['DX', 'XINC', 'XSIZE', 'X_SIZE', 'XLEN', 'XLENGTH', 'X_LENGTH', 'XDIM', 'DIMX'],
    'DY': ['DY', 'YINC', 'YSIZE', 'Y_SIZE', 'YLEN', 'YLENGTH', 'Y_LENGTH', 'YDIM', 'DIMY'],
    'DZ': ['DZ', 'ZINC', 'ZSIZE', 'Z_SIZE', 'ZLEN', 'ZLENGTH', 'Z_LENGTH', 'ZDIM', 'DIMZ'],
}
MAX_MUESTRA = 100_000          # bloques usados en las búsquedas de ángulo (la malla final usa todos)
TOL_REL = 1e-6                 # tolerancia relativa para redondeos de conteo de bloques


# ==================================================================================================
# Lectura
# ==================================================================================================
def resolver_columnas(cabecera, pedidas):
    """{rol: nombre real en el CSV} para los 6 roles XC, YC, ZC, DX, DY, DZ. `pedidas` son los nombres que el
    usuario indicó (en el mismo orden); se buscan sin distinguir mayúsculas. Los sinónimos (XCENTRE, XINC, ...) solo
    se prueban si el usuario dejó el nombre por defecto (o vacío): un nombre personalizado que no existe es un error,
    no se reemplaza en silencio por otra columna. Lanza ValueError listando lo que falta."""
    por_mayusculas = {}
    for c in cabecera:
        por_mayusculas.setdefault(str(c).strip().upper(), c)
    usadas, faltan = {}, []
    for rol, pedida in zip(COLUMNAS_DEFECTO, pedidas):
        pedida = str(pedida or '').strip()
        personalizada = bool(pedida) and pedida.upper() != rol
        candidatas = ([pedida.upper()] if pedida else []) + ([] if personalizada else SINONIMOS[rol])
        encontrada = next((por_mayusculas[c] for c in candidatas if c in por_mayusculas), None)
        if encontrada is None:
            faltan.append(f"{rol} ('{pedida or rol}')")
        else:
            usadas[rol] = encontrada
    if faltan:
        raise ValueError('El CSV de bloques no tiene la(s) columna(s): ' + ', '.join(faltan) + '.')
    return usadas


def leer_bloques(ruta, columnas=COLUMNAS_DEFECTO):
    """Lee solo las 6 columnas de geometría del CSV. Devuelve (DataFrame con columnas XC..DZ, {rol: columna real})."""
    try:
        cabecera = pd.read_csv(ruta, nrows=0, encoding='utf-8-sig').columns
        codificacion = 'utf-8-sig'
    except UnicodeDecodeError:
        cabecera = pd.read_csv(ruta, nrows=0, encoding='latin1').columns
        codificacion = 'latin1'
    usadas = resolver_columnas(cabecera, columnas)
    df = pd.read_csv(ruta, usecols=list(usadas.values()), encoding=codificacion, low_memory=False)
    return df.rename(columns={real: rol for rol, real in usadas.items()})[list(COLUMNAS_DEFECTO)], usadas


# ==================================================================================================
# Utilidades numéricas
# ==================================================================================================
def _valores_distintos(d):
    """Valores distintos de una dimensión (redondeados a 1e-6 para absorber ruido de exportación)."""
    return np.unique(np.round(d, 6))


def _fase(esquinas, paso):
    """Posición de la malla de paso `paso` respecto del 0 (en unidades de longitud, en [0, paso)): media circular
    de las esquinas módulo el paso."""
    ang = esquinas * (2.0 * math.pi / paso)
    fase = math.atan2(np.sin(ang).mean(), np.cos(ang).mean()) / (2.0 * math.pi)
    return (fase % 1.0) * paso


def _coherencia(esquinas, paso):
    """0..1: qué tan alineadas están las esquinas con una malla de paso `paso` (1 = todas en la misma fase)."""
    ang = esquinas * (2.0 * math.pi / paso)
    return float(math.hypot(np.cos(ang).mean(), np.sin(ang).mean()))


def _locales(xs, ys, theta):
    """Coordenadas locales (u, v) de puntos centrados respecto de un punto de referencia, para un bearing."""
    s, c = math.sin(math.radians(theta)), math.cos(math.radians(theta))
    return xs * s + ys * c, -xs * c + ys * s


def _angulo_por_vecinos(x, y, z, dx, dy, dz, padre, pasos_xy):
    """Ángulo de la grilla módulo 90° (en grados, [0, 90)) medido en el plano XY con los vecinos inmediatos de los
    bloques de tamaño padre, o None si no hay vecinos suficientes. Es el ángulo del eje X local respecto del Este
    (antihorario) módulo 90°."""
    from scipy.spatial import cKDTree
    es_padre = (np.abs(dx - padre[0]) < 1e-6) & (np.abs(dy - padre[1]) < 1e-6) & (np.abs(dz - padre[2]) < 1e-6)
    idx = np.nonzero(es_padre)[0]
    if idx.size < 20:
        return None
    if idx.size > MAX_MUESTRA:
        idx = np.random.default_rng(0).choice(idx, MAX_MUESTRA, replace=False)
    pts = np.column_stack([x[idx], y[idx], z[idx]])
    arbol = cKDTree(pts)
    # Con muestra completa los vecinos están a un paso exacto; con submuestreo se buscan más para no quedarse sin ellos.
    k = 7 if idx.size == int(es_padre.sum()) else 25
    dist, vec = arbol.query(pts, k=min(k, len(pts)))
    angulos, pesos = [], []
    for j in range(1, dist.shape[1]):
        d = dist[:, j]
        vx, vy, vz = (pts[vec[:, j], 0] - pts[:, 0], pts[vec[:, j], 1] - pts[:, 1], pts[vec[:, j], 2] - pts[:, 2])
        en_plano = np.abs(vz) < 1e-6
        a_paso = (np.abs(d - pasos_xy[0]) < 1e-3 * pasos_xy[0]) | (np.abs(d - pasos_xy[1]) < 1e-3 * pasos_xy[1])
        ok = en_plano & a_paso & (d > 0)
        angulos.append(np.degrees(np.arctan2(vy[ok], vx[ok])))
    angulos = np.concatenate(angulos) if angulos else np.array([])
    if angulos.size < 20:
        return None
    a4 = np.radians(angulos) * 4.0       # periodicidad de 90°
    medio = math.degrees(math.atan2(np.sin(a4).mean(), np.cos(a4).mean())) / 4.0
    return medio % 90.0


# ==================================================================================================
# Esquema
# ==================================================================================================
def inferir_esquema(df, nombres=COLUMNAS_DEFECTO):
    """Esquema de grilla de un modelo de bloques.

    `df` trae las columnas de centroide y dimensión (XC, YC, ZC, DX, DY, DZ, o los `nombres` indicados en ese
    orden). Devuelve un dict:
      - 'esquema': origen_x/y/z, extension_x/y/z, x_size/y_size/z_size, sub_x/sub_y/sub_z y bearing,
      - 'n_bloques', 'n_padres' (bloques padre del esquema), 'tiene_subbloques',
      - 'tamanos': {'DX': [valores distintos], 'DY': [...], 'DZ': [...]},
      - 'consistencia': fracción (0..1) de bloques cuyas esquinas caen en la malla del esquema,
      - 'avisos': lista de textos."""
    if list(nombres) != list(COLUMNAS_DEFECTO):
        df = df.rename(columns={n: r for r, n in zip(COLUMNAS_DEFECTO, nombres)})
    faltan = [c for c in COLUMNAS_DEFECTO if c not in df.columns]
    if faltan:
        raise ValueError('Faltan las columnas: ' + ', '.join(faltan) + '.')
    avisos = []
    datos = df[list(COLUMNAS_DEFECTO)].apply(pd.to_numeric, errors='coerce')
    validos = np.isfinite(datos.to_numpy()).all(axis=1) & (datos[['DX', 'DY', 'DZ']].to_numpy() > 0).all(axis=1)
    if not validos.all():
        avisos.append(f'{int((~validos).sum()):,} bloque(s) con valores vacíos o dimensiones <= 0 se ignoraron.')
        datos = datos[validos]
    if datos.empty:
        raise ValueError('El CSV no tiene bloques válidos (centroides y dimensiones numéricos, dimensiones > 0).')
    x, y, z, dx, dy, dz = (datos[c].to_numpy(dtype=float) for c in COLUMNAS_DEFECTO)
    n = len(datos)

    # --- Tamaños: padre = mayor, subbloque = menor --------------------------------------------------------------
    distintos = [_valores_distintos(d) for d in (dx, dy, dz)]
    padre = [float(v[-1]) for v in distintos]
    sub = [float(v[0]) for v in distintos]
    for eje, v, p, s in zip('XYZ', distintos, padre, sub):
        razon = v / s
        if np.abs(razon - np.round(razon)).max() > 1e-4:
            avisos.append(f'Las dimensiones D{eje} ({", ".join(f"{t:g}" for t in v[:6])}) no son múltiplos del '
                          f'menor ({s:g}): no parece un subbloqueo regular.')
        elif abs(p / s - round(p / s)) > 1e-4:
            avisos.append(f'El tamaño padre {p:g} no es múltiplo del subbloque {s:g} en {eje}.')

    # --- Bearing -------------------------------------------------------------------------------------------------
    x0, y0 = float(x.mean()), float(y.mean())
    xs, ys = x - x0, y - y0
    rng = np.random.default_rng(1)
    muestra = rng.choice(n, MAX_MUESTRA, replace=False) if n > MAX_MUESTRA else np.arange(n)

    def coherencia(theta, idx):
        u, v = _locales(xs[idx], ys[idx], theta)
        return _coherencia(u - dx[idx] / 2.0, sub[0]) * _coherencia(v - dy[idx] / 2.0, sub[1])

    theta0 = _angulo_por_vecinos(x, y, z, dx, dy, dz, padre, (padre[0], padre[1]))
    if theta0 is not None:
        # El ángulo medido es el del eje X local respecto del Este (antihorario); el bearing es su azimut: 90 - ángulo.
        theta_g = (90.0 - theta0) % 90.0
    else:
        # Sin vecinos de tamaño padre (todo subdividido o muy recortado): barrido por coherencia de esquinas en
        # 0..180° con un paso fino respecto del ancho del pico (~ subbloque / extensión).
        avisos.append('No hay bloques padre vecinos para medir la orientación; se buscó por coherencia de esquinas '
                      '(más lento y menos preciso).')
        chica = muestra[:5_000]
        largo = max(float(np.ptp(xs)), float(np.ptp(ys)), 1.0)
        paso = float(np.clip(math.degrees(0.25 * min(sub[0], sub[1]) / (2.0 * math.pi * largo)), 0.005, 0.05))
        pasos = np.arange(0.0, 180.0, paso)
        theta_g = float(pasos[int(np.argmax([coherencia(t, chica) for t in pasos]))]) % 90.0
    # Los dos candidatos módulo 90° (X e Y intercambiados) se afinan por separado por coherencia de esquinas
    # (el centro de la ventana es la estimación por vecinos) y gana el de mayor coherencia con los tamaños X e Y.
    from scipy.optimize import minimize_scalar

    def afinar(t0):
        ventana = np.arange(t0 - 0.3, t0 + 0.3001, 0.005)
        mejor = float(ventana[int(np.argmax([coherencia(t, muestra) for t in ventana]))])
        res = minimize_scalar(lambda t: -coherencia(t, muestra), bounds=(mejor - 0.01, mejor + 0.01),
                              method='bounded', options={'xatol': 1e-7})
        return float(res.x), coherencia(float(res.x), muestra)

    (t_a, p_a), (t_b, p_b) = afinar(theta_g), afinar(theta_g + 90.0)
    # Si empatan (bloques cuadrados en planta) el bearing es ambiguo módulo 90°: se toma el menor ángulo, o 90 en
    # vez de 0 cuando la grilla no está rotada (el bearing estándar).
    ambiguo = abs(p_a - p_b) < 1e-3 * max(p_a, p_b)
    if not ambiguo:
        theta = t_a if p_a > p_b else t_b
    elif min(theta_g, 90.0 - theta_g) < 1e-2:
        theta = 90.0
    else:
        theta = t_a
    theta = theta % 180.0
    bearing = round(theta, 3)
    if ambiguo and padre[0] == padre[1] and abs(bearing - 90.0) > 1e-3:
        avisos.append(f'Los bloques son cuadrados en planta: el bearing solo queda definido módulo 90° '
                      f'({bearing:g}° o {(bearing + 90.0) % 180.0:g}° describen la misma grilla con X e Y '
                      f'intercambiados). Se informa {bearing:g}°.')

    # --- Origen y extensión -------------------------------------------------------------------------------------
    u, v = _locales(xs, ys, bearing)
    ejes = [(u, dx, padre[0], sub[0]), (v, dy, padre[1], sub[1]), (z - z.min(), dz, padre[2], sub[2])]
    origen_local, extension, esquinas_ok = [], [], np.ones(n, dtype=bool)
    z_ref = float(z.min())
    for centro, d, p, s in ejes:
        esquina_min, esquina_max = centro - d / 2.0, centro + d / 2.0
        de_padre = np.abs(d - p) < 1e-6
        # La fase de la malla padre sale de los bloques de tamaño padre; si no hay, de la malla del subbloque.
        fase = _fase(esquina_min[de_padre], p) if de_padre.any() else _fase(esquina_min, s)
        k = math.floor((float(esquina_min.min()) - fase) / p + TOL_REL)
        origen = fase + k * p
        n_bloques = max(1, math.ceil((float(esquina_max.max()) - origen) / p - TOL_REL))
        origen_local.append(origen)
        extension.append(n_bloques * p)
        resto = (esquina_min - origen) / s
        tol = max(2e-3, 0.01 * s) / s
        esquinas_ok &= np.abs(resto - np.round(resto)) <= tol
    consistencia = float(esquinas_ok.mean())
    if consistencia < 0.999:
        avisos.append(f'Solo el {consistencia * 100:.2f} % de los bloques cae en la malla detectada: el CSV puede '
                      'tener rotación fuera de plano (Plunge/Dip), otra convención de ejes o bloques irregulares.')

    s_b, c_b = math.sin(math.radians(bearing)), math.cos(math.radians(bearing))
    ou, ov, ow = origen_local
    origen_x = x0 + ou * s_b - ov * c_b
    origen_y = y0 + ou * c_b + ov * s_b
    origen_z = z_ref + ow

    def limpio(valor, dec=4):
        r = round(float(valor), dec)
        return 0.0 if r == 0 else r

    esquema = {
        'origen_x': limpio(origen_x), 'origen_y': limpio(origen_y), 'origen_z': limpio(origen_z),
        'extension_x': limpio(extension[0]), 'extension_y': limpio(extension[1]), 'extension_z': limpio(extension[2]),
        'x_size': limpio(padre[0]), 'y_size': limpio(padre[1]), 'z_size': limpio(padre[2]),
        'sub_x': limpio(sub[0]), 'sub_y': limpio(sub[1]), 'sub_z': limpio(sub[2]),
        'bearing': limpio(bearing, 3),
    }
    n_padres = int(round(extension[0] / padre[0])) * int(round(extension[1] / padre[1])) \
        * int(round(extension[2] / padre[2]))
    return {
        'esquema': esquema,
        'n_bloques': n,
        'n_padres': n_padres,
        'tiene_subbloques': any(abs(p - s) > 1e-9 for p, s in zip(padre, sub)),
        'tamanos': {f'D{e}': [float(t) for t in v] for e, v in zip('XYZ', distintos)},
        'consistencia': consistencia,
        'avisos': avisos,
    }


def inferir_esquema_csv(ruta, columnas=COLUMNAS_DEFECTO):
    """Lee el CSV de bloques y devuelve inferir_esquema(...) más 'archivo' y 'columnas' (las usadas)."""
    df, usadas = leer_bloques(ruta, columnas)
    info = inferir_esquema(df)
    info['archivo'] = ruta
    info['columnas'] = usadas
    return info


def _num(v):
    """Número sin notación científica ni ceros sobrantes (8807800, 0.25, 37.5)."""
    return f'{v:.4f}'.rstrip('0').rstrip('.')


def texto_resumen(info):
    """Texto multilínea con lo identificado (consola y mensaje de la interfaz)."""
    e = info['esquema']
    sub = 'con subbloques' if info['tiene_subbloques'] else 'sin subbloques'
    lineas = [
        f"Esquema identificado en {info['n_bloques']:,} bloques ({sub}; {info['n_padres']:,} bloques padre):",
        f"    Origen      : X {_num(e['origen_x'])}   Y {_num(e['origen_y'])}   Z {_num(e['origen_z'])}",
        f"    Extensión   : X {_num(e['extension_x'])}   Y {_num(e['extension_y'])}   Z {_num(e['extension_z'])}"
        '  (metros)',
        f"    Bloque padre: X {_num(e['x_size'])}   Y {_num(e['y_size'])}   Z {_num(e['z_size'])}",
        f"    Subbloque   : X {_num(e['sub_x'])}   Y {_num(e['sub_y'])}   Z {_num(e['sub_z'])}",
        f"    Bearing     : {_num(e['bearing'])}°   (90 = sin rotación)",
        f"    Bloques en la malla detectada: {info['consistencia'] * 100:.2f} %",
    ]
    if info.get('avisos'):
        lineas += ['', 'Avisos:'] + [f'    - {a}' for a in info['avisos']]
    return '\n'.join(lineas)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    nombres = sys.argv[2:8] if len(sys.argv) >= 8 else COLUMNAS_DEFECTO
    print(texto_resumen(inferir_esquema_csv(sys.argv[1], nombres)))
