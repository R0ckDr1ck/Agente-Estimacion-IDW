"""
Nombre : ImportarBD.py
Propósito: Importador de la base de sondajes (Collar, Survey, Assay) sin interfaz gráfica. Replica el flujo
           de FmImportarProyecto (YupAI): mapeo de columnas a roles, validación con ValidadorSondajes,
           política estricta de exclusión de sondajes con observaciones y guardado con ProyectoSondajes
           en una base SQLite (.db). Los roles y tipos de cada campo se leen del JSON de configuración.
           Además exporta cada tabla a CSV (espejo de lo guardado en la .db) y escribe un log.
           v5: con "origen": "compositos" arma la base solo con un CSV de compósitos ya codificado y
           compositado (clave "compositos": archivo, nombre_tabla, roles, tipos, enteros), sin Collar ni Survey.
Uso      : python ImportarBD.py [ruta_config.json]
"""

import os
import sys
import json
import traceback

import pandas as pd

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Proyecto import ValidadorSondajes

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_importar_bd.json')
# Sinónimos -> rol, copiados de FmImportarProyecto (mapeo_collar / mapeo_survey / mapeo_intervalo).
# Solo se usan para las columnas que el JSON no menciona; las del JSON mandan.
MAPEO = {
    'Collar': {
        'Sondaje': ['SONDAJE', 'ID', 'HOLEID', 'COLLAR', 'HOLE_ID', 'HOLE'],
        'Este': ['X', 'ESTE', 'EAST', 'EASTING', 'XCOORD', 'X_COORD', 'X_COORDINATE', 'XCENTER', 'MIDX', 'MID_X', 'XC'],
        'Norte': ['Y', 'NORTE', 'NORTH', 'NORTHING', 'YCOORD', 'Y_COORD', 'Y_COORDINATE', 'YCENTER', 'MIDY', 'MID_Y', 'YC'],
        'Cota': ['Z', 'RL', 'COTA', 'ALTURA', 'ALTITUD', 'ZC', 'ZCENTER', 'MIDZ', 'MID_Z', 'ELEVATION', 'ELEVACION',
                 'ELEV', 'ZCOORD', 'Z_COORD', 'Z_COORDINATE'],
        'Profundidad': ['PROFUNDIDAD', 'DEPTH', 'LONGITUD', 'EOH'],
    },
    'Survey': {
        'Sondaje': ['SONDAJE', 'ID', 'HOLEID', 'HOLE_ID', 'BHID', 'DHID'],
        'Profundidad': ['PROFUNDIDAD', 'DEPTH', 'AT', 'DISTANCE', 'DISTANCIA', 'PROF'],
        'Azimut': ['AZIMUT', 'AZIMUTH', 'AZM', 'AZ', 'BRG', 'BEARING', 'RUMBO'],
        'Dip': ['DIP', 'INCLINACION', 'INCLINATION', 'INC', 'ANGLE', 'ANGULO'],
    },
    'Intervalo': {
        'Sondaje': ['SONDAJE', 'ID', 'HOLEID', 'HOLE_ID', 'BHID', 'DHID'],
        'Desde': ['DESDE', 'FROM', 'SAMPFROM', 'SAMP_FROM', 'GEOLFROM', 'GEOL_FROM',
                  'DEPTH_FROM', 'START', 'INICIO', 'INTERVAL_FROM', 'Z_FROM'],
        'Hasta': ['HASTA', 'TO', 'SAMPTO', 'SAMP_TO', 'GEOLTO', 'GEOL_TO',
                  'DEPTH_TO', 'END', 'FIN', 'INTERVAL_TO', 'Z_TO', 'BOTTOM'],
    },
}
REQUERIDOS = {
    'Collar': ['Sondaje', 'Este', 'Norte', 'Cota', 'Profundidad'],
    'Survey': ['Sondaje', 'Profundidad', 'Azimut', 'Dip'],
    'Intervalo': ['Sondaje', 'Desde', 'Hasta'],
}
NOMBRE_FIJO = {'Collar': ProyectoSondajes.NOMBRE_COLLAR, 'Survey': ProyectoSondajes.NOMBRE_SURVEY}

# v5: origen 'compositos' (conf_importar_bd.json): la base se arma desde un CSV de compósitos ya
# codificado y compositado (p. ej. assay_comp.csv), sin Collar ni Survey. Sus columnas se conservan tal
# cual (mayúsculas/minúsculas incluidas) y, salvo que el JSON diga lo contrario, todas se incluyen.
TIPO_COMPOSITO = 'Compósito'
ORIGEN_SONDAJES, ORIGEN_COMPOSITOS = 'sondajes', 'compositos'
MAPEO[TIPO_COMPOSITO] = MAPEO['Intervalo']
REQUERIDOS[TIPO_COMPOSITO] = REQUERIDOS['Intervalo']


def leer_csv_seguro(ruta):
    """Igual que Util.leer_csv_seguro de YupAI (UTF-8 con fallback a latin1)."""
    try:
        return pd.read_csv(ruta, encoding='utf-8', low_memory=True)
    except UnicodeDecodeError:
        return pd.read_csv(ruta, encoding='latin1', low_memory=True)


def rol_de(columna, tipo, roles_json):
    columna = columna.upper()
    if columna in roles_json:
        return roles_json[columna]
    for rol, sinonimos in MAPEO[tipo].items():
        if columna in sinonimos:
            return rol
    return 'Incluir' if tipo == TIPO_COMPOSITO else 'Omitir'


def estandarizar(cfg, log):
    """Lee el CSV y renombra a roles canónicos (réplica de _leer_estandarizado). Devuelve (df, rename)."""
    tipo = cfg['tipo']
    df = leer_csv_seguro(cfg['archivo'])
    df.columns = df.columns.str.strip()
    if tipo != TIPO_COMPOSITO:
        df.columns = df.columns.str.upper()
    roles_json = {k.strip().upper(): v for k, v in cfg.get('roles', {}).items()}
    rename = {}
    for col in df.columns:
        rol = rol_de(col, tipo, roles_json)
        if col.upper() not in roles_json and tipo != TIPO_COMPOSITO:
            log.append(f'{cfg["nombre_tabla"]}: columna {col} no está en el JSON -> rol {rol} (por sinónimo)')
        if rol != 'Omitir':
            rename[col] = col if rol == 'Incluir' else rol
    conteo = pd.Series(list(rename.values())).value_counts()
    for rol in REQUERIDOS[tipo]:
        if conteo.get(rol, 0) != 1:
            raise ValueError(f"{cfg['nombre_tabla']}: el rol obligatorio '{rol}' debe asignarse exactamente "
                             f"una vez (asignado {conteo.get(rol, 0)} veces).")
    return df.rename(columns=rename)[list(rename.values())], rename


def validar_estado(proy, df_in, tipo, nombre):
    """Réplica de FmImportarProyecto._validar_estado."""
    existe_collar = proy.existe_tabla(ProyectoSondajes.NOMBRE_COLLAR)
    existe_survey = proy.existe_tabla(ProyectoSondajes.NOMBRE_SURVEY)
    df_collar = df_in if tipo == 'Collar' else (proy.get_collar() if existe_collar else pd.DataFrame())
    df_survey = df_in if tipo == 'Survey' else (proy.get_survey() if existe_survey else pd.DataFrame())
    intervalos = {n: proy.get_intervalo(n) for n in proy.listar_intervalos()}
    if tipo == 'Intervalo':
        intervalos[nombre] = df_in
    errores = ValidadorSondajes.validar(df_collar, df_survey, intervalos)
    if not (tipo == 'Survey' or existe_survey):
        errores = [e for e in errores if 'registros en Survey' not in e['error']]
    if not (tipo == 'Collar' or existe_collar):
        errores = [e for e in errores if 'no existe en la tabla Collar' not in e['error']]
    return errores


def depurar(proy, df_std, tipo, nombre):
    """Réplica de FmImportarProyecto._depurar_tabla: retira filas sin sondaje y sondajes con
    observaciones propias de la tabla, revalidando hasta quedar limpia."""
    etiqueta = nombre if tipo == 'Intervalo' else tipo
    df_limpio, excluidos, sin_id, acumulados, errores = df_std, set(), 0, [], []
    for _ in range(10):
        errores = validar_estado(proy, df_limpio, tipo, nombre)
        excluyentes = [e for e in errores
                       if e['tabla'] == etiqueta and 'registros en Survey' not in e['error']]
        if not excluyentes or df_limpio.empty:
            break
        acumulados += excluyentes
        n_antes = len(df_limpio)
        sin = df_limpio['Sondaje'].isna()
        if sin.any():
            sin_id += int(sin.sum())
            df_limpio = df_limpio[~sin]
        con_error = {str(e['sondaje']) for e in excluyentes if str(e['sondaje']) != 'N/A'}
        if con_error:
            excluidos |= con_error
            df_limpio = df_limpio[~df_limpio['Sondaje'].astype(str).isin(con_error)]
        if len(df_limpio) == n_antes:
            break
    return df_limpio, excluidos, sin_id, acumulados + errores, errores


def importar_tabla(proy, cfg, decimales, excluir, log):
    tipo, nombre = cfg['tipo'], cfg['nombre_tabla']
    destino = NOMBRE_FIJO.get(tipo, nombre)
    df_std, rename = estandarizar(cfg, log)
    if tipo != 'Collar' and not proy.existe_tabla(ProyectoSondajes.NOMBRE_COLLAR):
        raise RuntimeError('Primero debe importarse Collar (integridad referencial).')

    df_limpio, excluidos, sin_id, errores, finales = depurar(proy, df_std, tipo, nombre)
    log.append(f'--- {destino}: {len(df_std)} filas leídas')
    for e in errores:
        log.append(f"  [{e['tabla']}] sondaje={e['sondaje']} fila={e['fila']}: {e['error']}")
    if tipo == 'Collar':
        huerfanos = {str(e['sondaje']) for e in finales if 'no existe en la tabla Collar' in e['error']}
        if huerfanos:
            raise RuntimeError(f'El nuevo Collar dejaría {len(huerfanos)} sondaje(s) huérfano(s).')
    if (excluidos or sin_id) and not excluir:
        raise RuntimeError(f'{destino}: hay observaciones y "excluir_sondajes_con_observaciones" es false.')
    if df_limpio.empty:
        raise RuntimeError(f'{destino}: no queda nada por importar tras excluir sondajes con observaciones.')
    if excluidos or sin_id:
        log.append(f'{destino}: se excluyen {len(excluidos)} sondaje(s) ({len(df_std) - len(df_limpio)} fila(s))'
                   f' y {sin_id} fila(s) sin sondaje: {sorted(excluidos)}')

    # Enteros declarados en el JSON (p. ej. columnas vacías, que pandas lee como float) y redondeo.
    df_limpio = df_limpio.copy()
    for col in (c.upper() for c in cfg.get('enteros', [])):
        if col in df_limpio.columns:
            df_limpio[col] = pd.to_numeric(df_limpio[col], errors='coerce').astype('Int64')
    for col in df_limpio.columns:
        if pd.api.types.is_float_dtype(df_limpio[col]):
            df_limpio[col] = df_limpio[col].round(decimales)

    columnas_originales = {rol: orig for orig, rol in rename.items()}
    roles_meta = {rol: orig for orig, rol in rename.items() if rol in ('Sondaje', 'Desde', 'Hasta')}
    tipos = {k.strip().upper(): v for k, v in cfg.get('tipos', {}).items()}
    kw = dict(archivo_origen=cfg['archivo'], roles=roles_meta,
              columnas_originales=columnas_originales, tipos=tipos)
    if tipo == 'Collar':
        proy.guardar_collar(df_limpio, **kw)
    elif tipo == 'Survey':
        proy.guardar_survey(df_limpio, **kw)
    else:
        proy.guardar_intervalo(nombre, df_limpio, **kw)
    log.append(f"{destino}: importada ({len(df_limpio)} filas)")


def importar_compositos(proy, cfg, log):
    """Guarda en la base un CSV de compósitos ya codificado y compositado (v5), sin Collar ni Survey.
    Roles obligatorios: Sondaje, Desde y Hasta (por el JSON o por sinónimo: Sondaje/Desde/Hasta, ID/FROM/TO,
    ...). El resto de las columnas se incluye tal cual, con su nombre original y sin redondear. Los tramos
    sin sondaje o sin Desde/Hasta numéricos no se pueden usar y se descartan avisando en el log."""
    nombre = cfg['nombre_tabla']
    cfg = dict(cfg, tipo=TIPO_COMPOSITO)
    if not os.path.exists(cfg['archivo']):
        raise FileNotFoundError(f"No existe el CSV de compósitos: {cfg['archivo']}")
    df, rename = estandarizar(cfg, log)
    n_leidas = len(df)
    log.append(f'--- {nombre}: {n_leidas} filas leídas de {cfg["archivo"]}')

    sondaje = df['Sondaje'].astype(str).str.strip()
    desde = pd.to_numeric(df['Desde'], errors='coerce')
    hasta = pd.to_numeric(df['Hasta'], errors='coerce')
    validas = df['Sondaje'].notna() & (sondaje != '') & desde.notna() & hasta.notna()
    if not validas.all():
        log.append(f'{nombre}: se descartan {int((~validas).sum())} fila(s) sin sondaje o sin Desde/Hasta numéricos.')
        df = df[validas].copy()
    if df.empty:
        raise RuntimeError(f'{nombre}: no queda ningún tramo utilizable en el CSV de compósitos.')
    n_invertidos = int((pd.to_numeric(df['Hasta']) <= pd.to_numeric(df['Desde'])).sum())
    if n_invertidos:
        log.append(f'AVISO {nombre}: {n_invertidos} tramo(s) con Hasta <= Desde (se importan igualmente).')

    enteros = {str(c).upper() for c in cfg.get('enteros', [])}
    for col in df.columns:
        if col.upper() in enteros:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('Int64')
    tipos = {k.strip().upper(): v for k, v in cfg.get('tipos', {}).items()}
    tipos = {col: tipos[col.upper()] for col in df.columns if col.upper() in tipos}

    columnas_originales = {rol: orig for orig, rol in rename.items()}
    roles_meta = {rol: orig for orig, rol in rename.items() if rol in ('Sondaje', 'Desde', 'Hasta')}
    proy.guardar_composito(nombre, df, archivo_origen=cfg['archivo'], roles=roles_meta,
                           columnas_originales=columnas_originales, tipos=tipos)
    log.append(f"{nombre}: compósito importado ({len(df)} filas, {df['Sondaje'].nunique()} sondajes, "
               f"{len(df.columns)} columnas: {', '.join(map(str, df.columns))})")


def archivos_de_entrada(config):
    """Rutas (normalizadas) de los CSV de entrada de la importación: el exportador no debe pisarlas."""
    if config.get('origen', ORIGEN_SONDAJES) == ORIGEN_COMPOSITOS:
        archivos = [config.get('compositos', {}).get('archivo', '')]
    else:
        archivos = [t.get('archivo', '') for t in config.get('tablas', [])]
    return {os.path.normcase(os.path.abspath(a)) for a in archivos if a}


def exportar_csv(proy, carpeta, decimales, log, protegidos=()):
    os.makedirs(carpeta, exist_ok=True)
    for t in proy.listar_tablas():
        n = t['nombre']
        ruta = os.path.join(carpeta, f'{n}.csv')
        if os.path.normcase(os.path.abspath(ruta)) in protegidos:
            log.append(f'CSV: {ruta} no se escribe (es el archivo de entrada de la importación)')
            continue
        df = {ProyectoSondajes.NOMBRE_COLLAR: proy.get_collar,
              ProyectoSondajes.NOMBRE_SURVEY: proy.get_survey}.get(n, lambda: proy.get_intervalo(n))()
        df.to_csv(ruta, index=False, float_format=f'%.{decimales}f', encoding='utf-8')
        log.append(f'CSV: {ruta} ({len(df)} filas)')


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    if config.get('__modulo__') != 'ImportarProyecto':
        raise ValueError('El JSON no pertenece al módulo ImportarProyecto.')
    carpeta = config['carpeta_csv_salida']
    os.makedirs(os.path.dirname(config['proyecto']), exist_ok=True)
    log = []
    origen = config.get('origen', ORIGEN_SONDAJES)
    if origen not in (ORIGEN_SONDAJES, ORIGEN_COMPOSITOS):
        raise ValueError(f"'origen' debe ser '{ORIGEN_SONDAJES}' o '{ORIGEN_COMPOSITOS}', no '{origen}'.")
    if origen == ORIGEN_COMPOSITOS and not config.get('compositos', {}).get('archivo'):
        raise ValueError("Con origen 'compositos' falta la clave 'compositos' con el CSV de compósitos.")
    proy = ProyectoSondajes.crear(config['proyecto'], sobrescribir=True)
    try:
        if origen == ORIGEN_COMPOSITOS:
            log.append('Origen: compósitos ya codificados y compositados (sin Collar ni Survey).')
            importar_compositos(proy, config['compositos'], log)
        else:
            for cfg in config['tablas']:
                importar_tabla(proy, cfg, config.get('decimales', 3),
                               config.get('excluir_sondajes_con_observaciones', True), log)
        exportar_csv(proy, carpeta, config.get('decimales', 3), log, archivos_de_entrada(config))
    except Exception:
        log.append(traceback.format_exc())
        raise
    finally:
        proy.cerrar()
        with open(os.path.join(carpeta, 'Importacion_Log.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(log) + '\n')
        print('\n'.join(log))


if __name__ == '__main__':
    main(*sys.argv[1:2])
