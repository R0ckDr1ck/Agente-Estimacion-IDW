"""
Nombre : EstimarIDW.py
Propósito: Ejecuta sin interfaz gráfica la estimación IDW de YupAI (FmEstimacionIDW): lee el JSON de
           configuración, estima el modelo de bloques por elemento con EstimadorIDW (multipasada, en bloque
           padre si 'est_parent' está activo), agrega la distancia promedio a 3 sondajes (d3h) y escribe el
           CSV del modelo estimado. Réplica de FmEstimacionIDW._leer_elementos / _leer_pasada /
           _leer_mapeo / _preparar_d3h / procesar_clic.
Uso      : python EstimarIDW.py [ruta_config.json]
"""

import os
import re
import sys
import json
import datetime
from dataclasses import replace

import pandas as pd

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Herramientas.EstimacionIDW import EstimadorIDW, MapeoCampos, PasadaIDW, GrillaModelo, texto_resumen

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_estimacion.json')
# Constantes de FmEstimacionIDW
OCT_ROTACION = (45.0, 0.0, 35.0)
VAR_D3H = 'd3h'
COL_DIST_3H = 'd3h_avgdist_anisot'
RADIO_D3H = 500.0


def _num(texto, defecto=0.0):
    try:
        return float(str(texto).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return defecto


def _int(texto, defecto=0):
    try:
        return int(round(float(str(texto).strip().replace(',', '.'))))
    except (TypeError, ValueError):
        return defecto


def _angulo(texto):
    """(valor fijo, variable): un ángulo es un número o el nombre de una columna del modelo de bloques
    (p. ej. 'lva_b') que da el ángulo de cada bloque."""
    texto = str(texto or '').strip()
    if not texto:
        return 0.0, ''
    try:
        return float(texto.replace(',', '.')), ''
    except ValueError:
        return 0.0, texto


def leer_pasada(reg, flag_deducido):
    """PasadaIDW de un registro de 'pasadas' (réplica de FmEstimacionIDW._leer_pasada). Bearing, Plunge y
    Dip aceptan además el nombre de una variable del modelo de bloques."""
    def t(col):
        return str(reg.get(col, '') or '').strip()
    texto_pasada = t('Pasada')
    flag = _num(texto_pasada, flag_deducido) if texto_pasada else flag_deducido
    mayor, semi, menor = _num(t('Mayor'), 100.0), _num(t('Semi'), 100.0), _num(t('Menor'), 100.0)
    anis = (1.0, semi / mayor, menor / mayor) if mayor > 0 else (1.0, 1.0, 1.0)
    (bearing, v_bearing), (plunge, v_plunge), (dip, v_dip) = (
        _angulo(t('Bearing')), _angulo(t('Plunge')), _angulo(t('Dip')))
    return PasadaIDW(
        id=t('ID') or 'pasada', potencia=_num(t('Potencia'), 2.0),
        mayor=mayor, semi=semi, menor=menor,
        bearing=bearing, plunge=plunge, dip=dip,
        var_bearing=v_bearing, var_plunge=v_plunge, var_dip=v_dip,
        oct_bearing=OCT_ROTACION[0], oct_plunge=OCT_ROTACION[1], oct_dip=OCT_ROTACION[2],
        anis_x=anis[0], anis_y=anis[1], anis_z=anis[2],
        min_muestras=_int(t('Mín Samp'), 1), max_muestras=_int(t('Máx Samp'), 0),
        max_octante=_int(t('Máx/oct'), 0), max_por_sondaje=_int(t('Máx samp/sond'), 0),
        disc_x=_int(t('Disc X'), 1), disc_y=_int(t('Disc Y'), 1), disc_z=_int(t('Disc Z'), 1),
        valor_flag=flag, dominio_valor=(t('Valor dominio') or None), condicion=t('Condición'))


def leer_mapeo(cfg, variable, reg, derivar_reporte):
    """MapeoCampos de un elemento (réplica de FmEstimacionIDW._leer_mapeo)."""
    campos, rep_cfg = cfg.get('campos', {}), cfg.get('reporte', {})
    base = MapeoCampos(
        nsamp=rep_cfg.get('nsamp', ''), nholes=rep_cfg.get('nholes', ''), flag=rep_cfg.get('flag', ''),
        distclose_cart=rep_cfg.get('distclose_cart', ''),
        distclose_anisot=rep_cfg.get('distclose_anisot', ''), nn=rep_cfg.get('nn', ''),
        avgdist_cart=rep_cfg.get('avgdist_cart', ''), avgdist_anisot=rep_cfg.get('avgdist_anisot', ''),
        sondaje=rep_cfg.get('sondaje', 'Sondaje'), valor_defecto=rep_cfg.get('valor_defecto', -99.0))
    var = str(variable).strip() or 'au_idw'

    def rep(atributo, sufijo):
        if derivar_reporte:
            return f'{var}_{sufijo}'
        return getattr(base, atributo, '') or f'{var}_{sufijo}'

    def t(col):
        return str(reg.get(col, '') or '').strip()

    return MapeoCampos(
        x=campos.get('x') or base.x or 'MIDX', y=campos.get('y') or base.y or 'MIDY',
        z=campos.get('z') or base.z or 'MIDZ',
        ley=t('Campo Ley'), longitud=t('Peso'), dominio=t('Campo dominio'),
        sondaje=campos.get('sondaje') or base.sondaje or 'Sondaje',
        bloque_xc=campos.get('bloque_xc', ''), bloque_yc=campos.get('bloque_yc', ''),
        bloque_zc=campos.get('bloque_zc', ''), bloque_dx=campos.get('bloque_dx', ''),
        bloque_dy=campos.get('bloque_dy', ''), bloque_dz=campos.get('bloque_dz', ''),
        col_estimacion=var, valor_defecto=base.valor_defecto,
        nsamp=rep('nsamp', 'nsamp'), nholes=rep('nholes', 'nholes'), flag=rep('flag', 'pass'),
        distclose_cart=rep('distclose_cart', 'distcart'),
        distclose_anisot=rep('distclose_anisot', 'distanis'), nn=rep('nn', 'nn'),
        avgdist_cart=rep('avgdist_cart', 'avgcart'), avgdist_anisot=rep('avgdist_anisot', 'avganis'))


def leer_elementos(cfg):
    """Agrupa 'pasadas' por Variable (un elemento = una corrida del motor multipasada)."""
    grupos, orden = {}, []
    for reg in cfg['pasadas']:
        var = str(reg.get('Variable', '')).strip()
        if var not in grupos:
            grupos[var] = []
            orden.append(var)
        grupos[var].append(reg)
    unico = len(orden) == 1
    return [{'variable': v, 'composito': str(grupos[v][0].get('Compósito', '')).strip(),
             'mapeo': leer_mapeo(cfg, v, grupos[v][0], derivar_reporte=not unico),
             'pasadas': [leer_pasada(r, i + 1) for i, r in enumerate(grupos[v])]} for v in orden]


def preparar_d3h(cfg, elemento):
    """Mapeo y pasada única de la distancia a 3 sondajes (réplica de FmEstimacionIDW._preparar_d3h)."""
    mapeo, pasadas = elemento['mapeo'], elemento['pasadas']
    mapeo_d3h = MapeoCampos(
        x=mapeo.x, y=mapeo.y, z=mapeo.z, ley=mapeo.ley, longitud=mapeo.longitud, dominio=mapeo.dominio,
        sondaje=mapeo.sondaje, bloque_xc=mapeo.bloque_xc, bloque_yc=mapeo.bloque_yc,
        bloque_zc=mapeo.bloque_zc, bloque_dx=mapeo.bloque_dx, bloque_dy=mapeo.bloque_dy,
        bloque_dz=mapeo.bloque_dz, col_estimacion=VAR_D3H, valor_defecto=mapeo.valor_defecto,
        avgdist_anisot=COL_DIST_3H)
    base = pasadas[0]
    # La distancia a 3 sondajes se calcula en todo el modelo con todo el compósito: no hereda el filtro
    # de dominio ni la condición de la primera pasada (p. ej. 'cu_idw eq -99 and domcu eq 21' dejaba d3h
    # solo en el dominio 21). La columna d3h parte vacía, así que no hace falta condición.
    cond = ''
    d3h = cfg.get('d3h', {})
    mayor = _num(d3h.get('mayor'), RADIO_D3H)
    semi = _num(d3h.get('semi'), RADIO_D3H)
    menor = _num(d3h.get('menor'), RADIO_D3H)
    # Orientación (bearing/plunge/dip, fijos o variables del modelo): se hereda de la primera pasada. Si el
    # elipsoide es una esfera (radios iguales) girarla no cambia nada, y con variables por bloque el motor
    # armaría un árbol de búsqueda por cada orientación distinta del modelo (horas), así que en ese caso se
    # deja sin orientación; el resultado es idéntico.
    if mayor == semi == menor:
        orientacion = dict(bearing=0.0, plunge=0.0, dip=0.0, var_bearing='', var_plunge='', var_dip='')
    else:
        orientacion = dict(bearing=base.bearing, plunge=base.plunge, dip=base.dip,
                           var_bearing=base.var_bearing, var_plunge=base.var_plunge, var_dip=base.var_dip)
    pasada = replace(
        base, id=VAR_D3H, mayor=mayor, semi=semi, menor=menor, **orientacion,
        anis_x=1.0, anis_y=semi / mayor, anis_z=menor / mayor, min_muestras=1, max_muestras=3,
        max_por_sondaje=1, max_octante=0, max_sondajes=0, disc_x=1, disc_y=1, disc_z=1, condicion=cond,
        dominio_valor=None)
    return mapeo_d3h, [pasada]


def reiniciar_variables(df, mapeos, conservar_anterior=True):
    """v7: el modelo existente puede traer ya estimadas las variables que se van a estimar (p. ej. cu_idw de una
    corrida anterior) y las pasadas solo estiman los bloques con la variable en su valor por defecto (-99), de
    modo que no estimarían nada. Deja esas columnas (la variable y sus columnas de reporte) como 'no estimado':
    valor por defecto en ley y distancias, 0 en conteos y pasada. Con 'conservar_anterior' guarda antes una copia
    de la variable como <variable>_anterior (el reporte la usa para comparar las leyes). Devuelve las columnas
    reiniciadas."""
    reiniciadas = []
    for mapeo in mapeos:
        variable = mapeo.col_estimacion
        if variable in df.columns:
            if conservar_anterior and variable != VAR_D3H:
                df[f'{variable}_anterior'] = df[variable]
            df[variable] = float(mapeo.valor_defecto)
            reiniciadas.append(variable)
        cero = {'nsamp', 'nholes', 'flag'}
        for atributo, columna in mapeo.columnas_reporte().items():
            if columna in df.columns:
                df[columna] = 0.0 if atributo in cero else float(mapeo.valor_defecto)
                reiniciadas.append(columna)
    return reiniciadas


def correr(estimador, df, muestras, mapeo, pasadas, grilla, etiqueta):
    print(f'======> INICIANDO ESTIMACIÓN [{etiqueta}]:', datetime.datetime.now())
    if grilla is not None:
        res = estimador.estimar_por_padre(df, muestras, mapeo, pasadas, grilla)
    else:
        res = estimador.estimar(df, muestras, mapeo, pasadas)
    print(f'======> ESTIMACIÓN FINALIZADA [{etiqueta}]:', datetime.datetime.now())
    return res


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'EstimacionIDW':
        raise ValueError('El JSON no pertenece al módulo EstimacionIDW.')

    elementos = leer_elementos(cfg)
    ep = cfg.get('est_parent', {})
    grilla = None
    if ep.get('activo', True):
        grilla = GrillaModelo(
            origen=(_num(ep.get('origen_x')), _num(ep.get('origen_y')), _num(ep.get('origen_z'))),
            tam_padre=(_num(ep.get('tam_x'), 5.0), _num(ep.get('tam_y'), 5.0), _num(ep.get('tam_z'), 5.0)),
            bearing=_num(ep.get('bearing'), 90.0))

    df_est = pd.read_csv(cfg['modelo'])
    estimador, cache, resumenes = EstimadorIDW(), {}, []

    reinicio = cfg.get('reiniciar', {})
    if reinicio.get('activo', True):
        principal_sel = str(cfg.get('d3h', {}).get('elemento_principal', '')).strip()
        principal = next((e for e in elementos if e['variable'] == principal_sel), elementos[0])
        mapeos = [e['mapeo'] for e in elementos] + [preparar_d3h(cfg, principal)[0]]
        reiniciadas = reiniciar_variables(df_est, mapeos, bool(reinicio.get('conservar_anterior', True)))
        if reiniciadas:
            print('Variables ya presentes en el modelo, reiniciadas a "no estimado" antes de estimar: '
                  + ', '.join(reiniciadas)
                  + (' (copia de la ley en <variable>_anterior)' if reinicio.get('conservar_anterior', True) else ''))

    with ProyectoSondajes.abrir(cfg['proyecto']) as proy:
        estimables = proy.calcular_estimables()

        def muestras(nombre):
            # Réplica de FmEstimacionIDW._muestras: compósito del proyecto sin sondajes no estimables.
            if nombre in cache:
                return cache[nombre]
            df = proy.get_intervalo(nombre)
            if estimables and 'Sondaje' in df.columns:
                df = df[df['Sondaje'].astype(str).isin(estimables)]
            if df.empty:
                raise ValueError(f"El compósito '{nombre}' no tiene muestras utilizables.")
            cache[nombre] = df
            return df

        for el in elementos:
            df_est, resumen = correr(estimador, df_est, muestras(el['composito']), el['mapeo'],
                                     el['pasadas'], grilla, el['variable'])
            resumenes.append((el['variable'], resumen))

        sel = str(cfg.get('d3h', {}).get('elemento_principal', '')).strip()
        principal = next((e for e in elementos if e['variable'] == sel), elementos[0])
        mapeo_d3h, pasadas_d3h = preparar_d3h(cfg, principal)
        df_d3h, _ = correr(estimador, df_est, muestras(principal['composito']), mapeo_d3h,
                           pasadas_d3h, grilla, 'distancia a 3 sondajes')

    df_est[COL_DIST_3H] = df_d3h[COL_DIST_3H].to_numpy()
    df_est.to_csv(cfg['salida'], index=False, encoding='utf-8-sig')

    for variable, resumen in resumenes:
        resumen['ruta_salida'] = cfg['salida']
        print(f'=== {variable} ===')
        print(texto_resumen(resumen))
    n_d3h = int((pd.to_numeric(df_est[COL_DIST_3H], errors='coerce') > 0).sum())
    print(f'\nDistancia a 3 sondajes ({COL_DIST_3H}) calculada en {n_d3h:,} bloques.')


if __name__ == '__main__':
    main(*sys.argv[1:2])
