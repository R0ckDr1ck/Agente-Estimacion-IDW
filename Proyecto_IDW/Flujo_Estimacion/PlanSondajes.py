"""
Nombre : PlanSondajes.py
Propósito: v7 — evalúa un plan de sondajes. A partir de los Collar y Survey del plan (sondajes nuevos, sin
           assay):
             1. genera un assay temporal con muestras de ancho fijo (0.50 m) a lo largo de cada sondaje;
             2. lo codifica con el seteo de la pestaña Codificación (conf_codificar.json);
             3. asigna a cada muestra una ley provisional (ver más abajo);
             4. lo compósita con el seteo de la pestaña Compositación (conf_comp.json);
             5. agrega los compósitos del plan a los compósitos ya existentes de la base (tabla assay_comp).
           Todo el trabajo intermedio ocurre en una base temporal (Plan_Temporal.db), así que los datos de
           entrada y los compósitos existentes de 01_Data no se tocan: solo se modifica la tabla de compósitos
           de la base del flujo (Database.db), y al correr de nuevo el paso se reemplazan los compósitos del
           plan anterior (no se duplican).

           Ley de las muestras del plan: los sondajes del plan no tienen análisis. Para que los compósitos
           participen de la estimación (el número de sondajes y la pasada que usa la categorización salen de la
           estimación de la ley) se les asigna una ley provisional, según 'ley.modo':
             'modelo'    la ley del bloque del modelo existente que contiene a la muestra (columna
                         'ley.columna_modelo', p. ej. cu_idw); si el bloque no está estimado o la muestra cae fuera del
                         modelo, la ley media del dominio de los compósitos existentes;
             'dominio'   la ley media del dominio (ponderada por longitud) de los compósitos existentes;
             'constante' el valor de 'ley.constante'.
           Las muestras sin ninguna ley posible (p. ej. un dominio sin compósitos existentes) reciben el flag de
           faltante de la compositación (conf_comp.json) y no generan compósitos. Así el plan aporta información de
           posición (distancia y número de sondajes) sin cambiar de forma apreciable las leyes estimadas. Las muestras
           fuera de todo sólido se tratan igual que las de los compósitos existentes (código de dominio por defecto).

           Salidas (carpeta 'salida'): Plan_Assay_Temporal.csv, Plan_Assay_Comp.csv, Plan_Resumen.json
           (longitud total, nº de sondajes, muestras y compósitos; lo lee el reporte) y la base temporal.
Uso      : python PlanSondajes.py [ruta_config.json]      (conf_plan.json; lee también conf_codificar.json y
           conf_comp.json de la misma carpeta)
"""

import os
import sys
import copy
import json
import datetime

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Herramientas.Desurvey import desurveyar_intervalos, METODO_TANGENTE_VULCAN
import CodificarSondajes
import CompositarSondajes

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_plan.json')
META_PLAN = 'plan_sondajes'            # nombres de los sondajes del plan ya agregados a la base del flujo
NOMBRE_TEMPORAL = 'Plan_Temporal.db'
NOMBRE_ASSAY_CSV = 'Plan_Assay_Temporal.csv'
NOMBRE_COMP_CSV = 'Plan_Assay_Comp.csv'
NOMBRE_RESUMEN = 'Plan_Resumen.json'
MODOS_LEY = ('modelo', 'dominio', 'constante')


# ==================================================================================================
# Lectura y validación del plan
# ==================================================================================================
def _leer_csv(ruta, descripcion):
    if not ruta or not os.path.exists(ruta):
        raise FileNotFoundError(f'No existe el CSV de {descripcion}: {ruta or "(sin indicar)"}')
    df = pd.read_csv(ruta, encoding='utf-8-sig')
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _tomar(df, columnas, descripcion):
    """DataFrame con las columnas pedidas {nombre_estandar: nombre_en_el_csv}, renombradas."""
    faltan = [f"{nom} ('{col}')" for nom, col in columnas.items() if col not in df.columns]
    if faltan:
        raise ValueError(f'El CSV de {descripcion} no tiene la(s) columna(s): {", ".join(faltan)}. '
                         f'Columnas del archivo: {", ".join(df.columns)}.')
    salida = df[list(columnas.values())].copy()
    salida.columns = list(columnas)
    return salida


def leer_plan(cfg):
    """(collar, survey) del plan con columnas estándar: Sondaje, Este, Norte, Cota, Profundidad / Sondaje,
    Profundidad, Azimut, Dip. Valida nombres únicos, números completos y survey para cada sondaje."""
    c, s = cfg['collar'], cfg['survey']
    collar = _tomar(_leer_csv(c['archivo'], 'collar del plan'), {
        'Sondaje': c['col_sondaje'], 'Este': c['col_este'], 'Norte': c['col_norte'], 'Cota': c['col_cota'],
        'Profundidad': c['col_longitud']}, 'collar del plan')
    survey = _tomar(_leer_csv(s['archivo'], 'survey del plan'), {
        'Sondaje': s['col_sondaje'], 'Profundidad': s['col_profundidad'], 'Azimut': s['col_azimut'],
        'Dip': s['col_dip']}, 'survey del plan')
    for df in (collar, survey):
        df['Sondaje'] = df['Sondaje'].astype(str).str.strip()
    for col in ('Este', 'Norte', 'Cota', 'Profundidad'):
        collar[col] = pd.to_numeric(collar[col], errors='coerce')
    for col in ('Profundidad', 'Azimut', 'Dip'):
        survey[col] = pd.to_numeric(survey[col], errors='coerce')

    if collar.empty:
        raise ValueError('El collar del plan no tiene sondajes.')
    repetidos = sorted(collar.loc[collar['Sondaje'].duplicated(), 'Sondaje'].unique())
    if repetidos:
        raise ValueError(f'El collar del plan repite sondajes: {", ".join(repetidos[:10])}.')
    malos = collar[collar[['Este', 'Norte', 'Cota', 'Profundidad']].isna().any(axis=1)]['Sondaje'].tolist()
    if malos:
        raise ValueError(f'Collar del plan con coordenadas o longitud vacías o no numéricas: {", ".join(malos[:10])}.')
    cortos = collar[collar['Profundidad'] <= 0]['Sondaje'].tolist()
    if cortos:
        raise ValueError(f'Collar del plan con longitud <= 0: {", ".join(cortos[:10])}.')
    if survey[['Profundidad', 'Azimut', 'Dip']].isna().any().any():
        raise ValueError('El survey del plan tiene profundidad, azimut o dip vacíos o no numéricos.')
    sin_survey = sorted(set(collar['Sondaje']) - set(survey['Sondaje']))
    if sin_survey:
        raise ValueError(f'Sondajes del collar sin survey: {", ".join(sin_survey[:10])}.')
    extra = sorted(set(survey['Sondaje']) - set(collar['Sondaje']))
    if extra:
        print(f'  AVISO: el survey trae sondajes que no están en el collar (se ignoran): {", ".join(extra[:10])}.')
        survey = survey[survey['Sondaje'].isin(collar['Sondaje'])]
    return collar.reset_index(drop=True), survey.reset_index(drop=True)


def generar_assay(collar, ancho):
    """Assay temporal: una muestra de 'ancho' metros cada vez, de 0 a la longitud de cada sondaje. La última
    muestra se acorta para terminar exactamente en la longitud."""
    if not ancho > 0:
        raise ValueError('El ancho de muestra debe ser mayor que 0.')
    partes = []
    for sondaje, largo in zip(collar['Sondaje'], collar['Profundidad']):
        n = int(np.ceil(largo / ancho - 1e-9))
        desde = np.round(np.arange(n) * ancho, 6)
        hasta = np.minimum(np.round(desde + ancho, 6), largo)
        partes.append(pd.DataFrame({'Sondaje': sondaje, 'Desde': desde, 'Hasta': hasta}))
    return pd.concat(partes, ignore_index=True)


# ==================================================================================================
# Ley provisional de las muestras
# ==================================================================================================
def _clave_dominio(valor):
    """Clave canónica de un código de dominio: 21, 21.0 y '21' son la misma."""
    if valor is None or (isinstance(valor, float) and np.isnan(valor)):
        return ''
    try:
        f = float(valor)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(valor).strip()


def leer_modelo(cfg_modelo, columnas_extra):
    """Centros, tamaños y ley de los bloques del modelo existente (solo las columnas necesarias)."""
    ruta = cfg_modelo['archivo']
    if not ruta or not os.path.exists(ruta):
        raise FileNotFoundError(f'No existe el modelo de bloques: {ruta or "(sin indicar)"}')
    cols = [cfg_modelo[k] for k in ('xc', 'yc', 'zc', 'dx', 'dy', 'dz')] + list(columnas_extra)
    cabecera = list(pd.read_csv(ruta, nrows=0, encoding='utf-8-sig').columns)
    faltan = [c for c in cols if c not in cabecera]
    if faltan:
        raise ValueError(f"El modelo de bloques no tiene la(s) columna(s): {', '.join(faltan)}.")
    return pd.read_csv(ruta, usecols=cols, encoding='utf-8-sig')


def ley_de_bloques(modelo, cfg_modelo, puntos, col_ley):
    """Ley del bloque que contiene a cada punto (NaN si el punto cae fuera del modelo o el bloque no tiene una
    ley válida, es decir, vacía o negativa como el -99 de 'no estimado')."""
    centros = modelo[[cfg_modelo['xc'], cfg_modelo['yc'], cfg_modelo['zc']]].to_numpy(dtype=float)
    tam = modelo[[cfg_modelo['dx'], cfg_modelo['dy'], cfg_modelo['dz']]].to_numpy(dtype=float)
    ley = pd.to_numeric(modelo[col_ley], errors='coerce').to_numpy(dtype=float)
    salida = np.full(len(puntos), np.nan)
    buenos = np.isfinite(puntos).all(axis=1)
    if not buenos.any():
        return salida
    dist, idx = cKDTree(centros).query(puntos[buenos], k=1)
    dentro = dist <= 0.5 * np.linalg.norm(tam[idx], axis=1) + 1e-6
    # el bloque más cercano debe contener al punto en cada eje (modelo sin rotación)
    dentro &= (np.abs(puntos[buenos] - centros[idx]) <= tam[idx] / 2 + 1e-6).all(axis=1)
    valores = ley[idx]
    valores = np.where(dentro & np.isfinite(valores) & (valores >= 0), valores, np.nan)
    salida[buenos] = valores
    return salida


def leyes_medias_dominio(comp, campo_dominio, campos, peso='length'):
    """{campo: {dominio: ley media ponderada por longitud}} de los compósitos existentes (ignora flags < 0)."""
    medias = {}
    pesos = pd.to_numeric(comp[peso], errors='coerce') if peso in comp.columns else pd.Series(1.0, index=comp.index)
    claves = comp[campo_dominio].map(_clave_dominio)
    for campo in campos:
        if campo not in comp.columns:
            medias[campo] = {}
            continue
        valores = pd.to_numeric(comp[campo], errors='coerce')
        ok = valores.notna() & (valores >= 0) & pesos.notna() & (pesos > 0)
        df = pd.DataFrame({'d': claves[ok], 'v': valores[ok], 'w': pesos[ok]})
        df['vw'] = df['v'] * df['w']
        g = df.groupby('d')[['vw', 'w']].sum()
        medias[campo] = (g['vw'] / g['w']).to_dict()
    return medias


def asignar_leyes(df_assay, mids, campos, campo_dominio, flag, cfg_ley, cfg_plan, comp_existente):
    """Agrega a df_assay las columnas de ley provisional (una por campo de 'campos', con el mismo valor en cada
    muestra) y devuelve (df, origen, resumen). 'origen' es, por muestra, 'modelo' / 'constante' (la ley pedida),
    'dominio' (respaldo: ley media del dominio de los compósitos existentes; las muestras sin sólido usan la media
    del código de dominio por defecto, p. ej. -99) o 'sin ley' (no hay ni media: recibe el flag de faltante)."""
    modo = cfg_ley.get('modo', 'modelo')
    if modo not in MODOS_LEY:
        raise ValueError(f"Modo de ley no válido: «{modo}» (use {', '.join(MODOS_LEY)}).")
    n = len(df_assay)
    dominios = df_assay[campo_dominio].map(_clave_dominio)

    base = np.full(n, np.nan)          # ley pedida por el modo, antes del respaldo por dominio
    if modo == 'constante':
        base[:] = float(cfg_ley.get('constante', 0.0))
    elif modo == 'modelo':
        col_ley = cfg_ley.get('columna_modelo', '')
        if not col_ley:
            raise ValueError("Indique la columna de ley del modelo ('ley.columna_modelo') o elija otro modo de ley.")
        modelo = leer_modelo(cfg_plan['modelo'], [col_ley])
        print(f'  Modelo de bloques: {len(modelo):,} bloques; ley provisional de la columna {col_ley}.')
        base = ley_de_bloques(modelo, cfg_plan['modelo'], mids, col_ley)

    medias = leyes_medias_dominio(comp_existente, campo_dominio, campos)
    origen = np.where(np.isfinite(base), 'constante' if modo == 'constante' else 'modelo', 'sin ley').astype(object)
    for campo in campos:
        valores = base.copy()
        falta = ~np.isfinite(valores)
        if falta.any():
            valores[falta] = dominios[falta].map(medias.get(campo, {})).to_numpy(dtype=float)
        if campo == campos[0]:
            origen[falta & np.isfinite(valores)] = 'dominio'
        df_assay[campo] = np.where(np.isfinite(valores), valores, flag)
    resumen = {k: int((origen == k).sum()) for k in ('modelo', 'constante', 'dominio', 'sin ley')}
    return df_assay, origen, resumen


# ==================================================================================================
# Unión con los compósitos de la base del flujo
# ==================================================================================================
def _alinear(df_nuevo, df_existente):
    """Columnas y tipos de los compósitos nuevos iguales a los de la tabla existente. Devuelve (df, avisos)."""
    avisos = []
    faltan = [c for c in df_existente.columns if c not in df_nuevo.columns]
    sobran = [c for c in df_nuevo.columns if c not in df_existente.columns]
    if faltan:
        avisos.append(f"los compósitos del plan no traen: {', '.join(faltan)} (quedan vacías)")
    if sobran:
        avisos.append(f"la tabla de compósitos no tiene: {', '.join(sobran)} (no se agregan)")
    df = df_nuevo.reindex(columns=list(df_existente.columns)).copy()
    for col in df_existente.columns:
        tipo = df_existente[col].dtype
        try:
            if pd.api.types.is_numeric_dtype(tipo):
                numerico = pd.to_numeric(df[col], errors='coerce')
                if pd.api.types.is_integer_dtype(tipo) and numerico.notna().all():
                    numerico = numerico.round().astype(tipo)
                df[col] = numerico
            elif pd.api.types.is_object_dtype(tipo) or pd.api.types.is_string_dtype(tipo):
                df[col] = df[col].map(lambda v: None if pd.isna(v) else
                                      (str(int(v)) if isinstance(v, float) and float(v).is_integer() else str(v)))
        except (TypeError, ValueError):
            pass
    return df, avisos


def agregar_a_base(cfg, tabla_comp, df_comp_plan, collar, survey, nombres_plan):
    """Agrega (o reemplaza) los compósitos del plan en la tabla de compósitos de la base del flujo. Si la base
    tiene Collar y Survey (origen 'sondajes') agrega también los del plan, para que los sondajes del plan cuenten
    como estimables. Devuelve el resumen (dict)."""
    ruta = cfg['proyecto']
    with ProyectoSondajes.abrir(ruta) as proy:
        if not proy.existe_tabla(tabla_comp):
            raise ValueError(f"La base '{ruta}' no tiene la tabla de compósitos '{tabla_comp}'. Ejecute antes el paso "
                             "«importar» (y, si importa sondajes, codificar/acotar/compositar).")
        previos = set()
        try:
            previos = set(json.loads(proy.get_meta(META_PLAN, '[]') or '[]'))
        except ValueError:
            pass
        existente = proy.get_intervalo(tabla_comp)
        nombres_exist = set(existente['Sondaje'].astype(str))
        choque = sorted((set(nombres_plan) & nombres_exist) - previos)
        if choque:
            raise ValueError('Estos sondajes del plan ya existen en los compósitos de la base con el mismo nombre: '
                             f'{", ".join(choque[:10])}. Cambie los nombres en el plan.')
        quitar = previos | set(nombres_plan)
        base = existente[~existente['Sondaje'].astype(str).isin(quitar)]
        n_reemplazados = len(existente) - len(base)
        nuevos, avisos = _alinear(df_comp_plan, existente)
        total = pd.concat([base, nuevos], ignore_index=True)
        proy.actualizar_tabla(tabla_comp, total)

        agrego_collar = False
        if proy.existe_tabla(ProyectoSondajes.NOMBRE_COLLAR) and proy.existe_tabla(ProyectoSondajes.NOMBRE_SURVEY):
            col_ex, sur_ex = proy.get_collar(), proy.get_survey()
            col_ex = col_ex[~col_ex['Sondaje'].astype(str).isin(quitar)]
            sur_ex = sur_ex[~sur_ex['Sondaje'].astype(str).isin(quitar)]
            proy.guardar_collar(pd.concat([col_ex, collar], ignore_index=True), archivo_origen='Collar + plan')
            proy.guardar_survey(pd.concat([sur_ex, survey], ignore_index=True), archivo_origen='Survey + plan')
            agrego_collar = True
        proy._set_meta(META_PLAN, json.dumps(sorted(nombres_plan), ensure_ascii=False))
    return {'tabla': tabla_comp, 'n_existentes': int(len(base)), 'n_plan': int(len(nuevos)),
            'n_total': int(len(total)), 'n_reemplazados': int(n_reemplazados), 'avisos': avisos,
            'collar_survey': agrego_collar}


# ==================================================================================================
def _num(cfg, clave, defecto):
    try:
        return float(str(cfg.get(clave, defecto)).replace(',', '.'))
    except ValueError:
        raise ValueError(f"'{clave}' debe ser un número.") from None


def _leer_json(ruta, modulo):
    if not os.path.exists(ruta):
        raise FileNotFoundError(f'No existe la configuración: {ruta}')
    with open(ruta, 'r', encoding='utf-8-sig') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != modulo:
        raise ValueError(f'{os.path.basename(ruta)} no pertenece al módulo {modulo}.')
    return cfg


def main(ruta_config=CONFIG_DEFECTO):
    ruta_config = os.path.abspath(ruta_config)
    carpeta_conf = os.path.dirname(ruta_config)
    cfg = _leer_json(ruta_config, 'PlanSondajes')
    cod = _leer_json(os.path.join(carpeta_conf, 'conf_codificar.json'), 'CodificacionSondajes')
    comp = _leer_json(os.path.join(carpeta_conf, 'conf_comp.json'), 'Compositacion')

    ancho = _num(cfg.get('muestreo', {}), 'ancho', 0.5)
    carpeta = cfg['salida']
    os.makedirs(carpeta, exist_ok=True)
    ruta_temporal = os.path.join(carpeta, NOMBRE_TEMPORAL)
    tabla_assay, campo_dom = cod['tabla'], cod['campo']
    tabla_comp = (cfg.get('tabla_composito') or comp['tabla_composito']).strip().lower()
    campos_ley = list(comp['campos_numericos'])
    if not campos_ley:
        raise ValueError("conf_comp.json no tiene campos numéricos que compositar (campos_numericos).")
    flags = CompositarSondajes.parsear_flags(comp.get('faltantes_flag', ''))
    flag = flags[0] if flags else np.nan

    print('======> INICIANDO PLAN DE SONDAJES:', datetime.datetime.now())
    # 1. Plan y assay temporal --------------------------------------------------------------------
    collar, survey = leer_plan(cfg)
    assay = generar_assay(collar, ancho)
    longitud_total = float(collar['Profundidad'].sum())
    print(f'  Plan: {len(collar)} sondajes, {longitud_total:,.1f} m en total, '
          f'{len(assay):,} muestras de {ancho:g} m.')

    # 2. Base temporal y codificación (seteo de la pestaña Codificación) ------------------------------
    proy = ProyectoSondajes.crear(ruta_temporal, nombre='Plan de sondajes', sobrescribir=True)
    try:
        proy.guardar_collar(collar, archivo_origen=cfg['collar']['archivo'])
        proy.guardar_survey(survey, archivo_origen=cfg['survey']['archivo'])
        proy.guardar_intervalo(tabla_assay, assay, archivo_origen='Plan: muestras sintéticas')
    finally:
        proy.cerrar()
    cfg_cod = copy.deepcopy(cod)
    cfg_cod['proyecto'] = ruta_temporal
    print('\n--- Codificación (conf_codificar.json) ---')
    CodificarSondajes.ejecutar(cfg_cod)

    # 3. Ley provisional --------------------------------------------------------------------------
    print('\n--- Ley provisional de las muestras ---')
    with ProyectoSondajes.abrir(ruta_temporal) as proy:
        assay = proy.get_intervalo(tabla_assay)
        df_col, df_sur = proy.get_collar(), proy.get_survey()
    con_xyz = desurveyar_intervalos(df_col, df_sur, assay, metodo=METODO_TANGENTE_VULCAN)
    mids = con_xyz[['X_MID', 'Y_MID', 'Z_MID']].to_numpy(dtype=float)
    with ProyectoSondajes.abrir(cfg['proyecto']) as principal:
        if not principal.existe_tabla(tabla_comp):
            raise ValueError(f"La base '{cfg['proyecto']}' no tiene la tabla de compósitos '{tabla_comp}'. "
                             'Ejecute antes el paso «importar».')
        comp_existente = principal.get_intervalo(tabla_comp)
    assay, origen, res_ley = asignar_leyes(
        assay, mids, campos_ley, campo_dom, flag, cfg.get('ley', {}), cfg, comp_existente)
    print(f"  Muestras por origen de la ley: modelo {res_ley['modelo']:,} | media del dominio {res_ley['dominio']:,} | "
          f"constante {res_ley['constante']:,} | sin ley (ignoradas) {res_ley['sin ley']:,}")
    with ProyectoSondajes.abrir(ruta_temporal) as proy:
        proy.actualizar_tabla(tabla_assay, assay)
    reporte_assay = con_xyz[['Sondaje', 'Desde', 'Hasta', 'X_MID', 'Y_MID', 'Z_MID']].copy()
    for col in [campo_dom] + campos_ley:
        reporte_assay[col] = assay[col].to_numpy()
    reporte_assay['ORIGEN_LEY'] = origen
    reporte_assay.to_csv(os.path.join(carpeta, NOMBRE_ASSAY_CSV), index=False, encoding='utf-8-sig')

    # 4. Compositación (seteo de la pestaña Compositación) -----------------------------------------
    cfg_comp = copy.deepcopy(comp)
    cfg_comp['proyecto'] = ruta_temporal
    cfg_comp['tabla_assay'] = tabla_assay
    cfg_comp['tabla_composito'] = tabla_comp
    print('\n--- Compositación (conf_comp.json) ---')
    CompositarSondajes.ejecutar(cfg_comp)
    with ProyectoSondajes.abrir(ruta_temporal) as proy:
        comp_plan = proy.get_intervalo(tabla_comp)
    comp_plan.to_csv(os.path.join(carpeta, NOMBRE_COMP_CSV), index=False, encoding='utf-8-sig')

    # 5. Unión con los compósitos de la base del flujo ---------------------------------------------
    print('\n--- Unión con los compósitos existentes ---')
    union = agregar_a_base(cfg, tabla_comp, comp_plan, collar, survey, collar['Sondaje'].tolist())
    for aviso in union['avisos']:
        print('  AVISO:', aviso)
    print(f"  Tabla '{union['tabla']}' de {cfg['proyecto']}: {union['n_existentes']:,} compósitos existentes + "
          f"{union['n_plan']:,} del plan = {union['n_total']:,}"
          + (f" ({union['n_reemplazados']:,} de una corrida anterior del plan reemplazados)"
             if union['n_reemplazados'] else '') + '.')
    if union['collar_survey']:
        print('  La base tiene Collar y Survey: se agregaron también los del plan.')

    por_sondaje = comp_plan.groupby('Sondaje').agg(n_compositos=('Desde', 'size'),
                                                   longitud_compositada=('length', 'sum'))
    resumen = {
        'fecha': datetime.datetime.now().isoformat(timespec='seconds'),
        'n_sondajes': int(len(collar)),
        'longitud_total': longitud_total,
        'ancho_muestra': ancho,
        'n_muestras': int(len(assay)),
        'n_compositos': int(len(comp_plan)),
        'longitud_compositada': float(comp_plan['length'].sum()),
        'longitud_en_dominios': float(comp_plan.loc[comp_plan[campo_dom].map(_clave_dominio)
                                                    != _clave_dominio(cod.get('valor_defecto', '-99')), 'length'].sum()),
        'modo_ley': cfg.get('ley', {}).get('modo', 'modelo'),
        'origen_ley': res_ley,
        'sondajes': [{'sondaje': s, 'longitud': float(l),
                      'n_compositos': int(por_sondaje['n_compositos'].get(s, 0)),
                      'longitud_compositada': float(por_sondaje['longitud_compositada'].get(s, 0.0))}
                     for s, l in zip(collar['Sondaje'], collar['Profundidad'])],
    }
    with open(os.path.join(carpeta, NOMBRE_RESUMEN), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(resumen, f, indent=4, ensure_ascii=False)
        f.write('\n')
    if not cfg.get('conservar_temporal', True):
        try:
            os.remove(ruta_temporal)
        except OSError:
            pass
    print('======> PLAN DE SONDAJES FINALIZADO:', datetime.datetime.now())
    print(f"\nPlan: {resumen['n_sondajes']} sondajes | {longitud_total:,.1f} m | {resumen['n_muestras']:,} muestras de "
          f"{ancho:g} m | {resumen['n_compositos']:,} compósitos ({resumen['longitud_compositada']:,.1f} m, "
          f"{resumen['longitud_en_dominios']:,.1f} m dentro de dominios).")
    print(f'Archivos en {carpeta}: {NOMBRE_ASSAY_CSV}, {NOMBRE_COMP_CSV}, {NOMBRE_RESUMEN}.')


if __name__ == '__main__':
    main(*sys.argv[1:2])
