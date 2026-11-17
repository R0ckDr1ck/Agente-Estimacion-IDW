"""
Nombre : CompositarSondajes.py
Propósito: Ejecuta sin interfaz gráfica la compositación de YupAI (FmCompositacion): lee el JSON de
           configuración, compósita la tabla de assay con Compositacion (solo sondajes estimables),
           georreferencia los compósitos con el desurvey Tangente Vulcan y guarda el resultado como tabla
           de compósito en la base con ProyectoSondajes.guardar_composito.
Uso      : python CompositarSondajes.py [ruta_config.json]
"""

import os
import sys
import json
import datetime

import pandas as pd

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Herramientas.Compositacion import Compositacion
from Herramientas.Desurvey import crear_desurvey, METODO_TANGENTE_VULCAN

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_comp.json')
def parsear_flags(texto):
    """Igual que FmCompositacion._parsear_flags: '-99; -1' -> [-99.0, -1.0]."""
    flags = []
    for parte in str(texto).replace(';', ',').split(','):
        parte = parte.strip()
        if parte:
            try:
                flags.append(float(parte))
            except ValueError:
                pass
    return flags


def consolidar_salida(df_comp, comps_xyz):
    """Réplica de FmCompositacion._consolidar_salida: agrega MID/TOP/BOT XYZ y ordena columnas."""
    df = df_comp.copy()
    renombrar = {'X_MID': 'MIDX', 'Y_MID': 'MIDY', 'Z_MID': 'MIDZ',
                 'X_START': 'TOPX', 'Y_START': 'TOPY', 'Z_START': 'TOPZ',
                 'X_END': 'BOTX', 'Y_END': 'BOTY', 'Z_END': 'BOTZ'}
    partes = [v for v in (comps_xyz or {}).values() if v is not None and not v.empty]
    if partes:
        df_xyz = pd.concat(partes, ignore_index=True)
        cols_key = ['Sondaje', 'Desde', 'Hasta']
        cols_coord = [c for c in renombrar if c in df_xyz.columns]
        if cols_coord:
            df_xyz = df_xyz[cols_key + cols_coord].drop_duplicates(subset=cols_key)
            df = df.merge(df_xyz, on=cols_key, how='left').rename(columns=renombrar)
    base = [c for c in ['Sondaje', 'Desde', 'Hasta', 'length', 'MIDX', 'MIDY', 'MIDZ',
                        'TOPX', 'TOPY', 'TOPZ', 'BOTX', 'BOTY', 'BOTZ'] if c in df.columns]
    return df[base + [c for c in df.columns if c not in base]]


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'Compositacion':
        raise ValueError('El JSON no pertenece al módulo Compositacion.')
    ejecutar(cfg)


def ejecutar(cfg):
    """Compósita según el dict de configuración (el contenido de conf_comp.json). v7: PlanSondajes.py lo
    reutiliza con otra base (cfg['proyecto']) para compositar los sondajes del plan con el mismo seteo."""
    tabla, nombre_comp = cfg['tabla_assay'], cfg['tabla_composito'].strip().lower()
    cortar = bool(cfg.get('cortar_geologia', True))
    campo_roca = (cfg.get('codigo_roca') or '').strip() or None
    if nombre_comp == tabla.lower():
        raise ValueError('La tabla de compósito no puede llamarse igual que la de muestras.')

    with ProyectoSondajes.abrir(cfg['proyecto']) as proy:
        tipo_existente = proy.tipo_tabla(nombre_comp)
        if tipo_existente is not None and tipo_existente != ProyectoSondajes.TIPO_COMPOSITO:
            raise ValueError(f"Ya existe una tabla '{nombre_comp}' de tipo '{tipo_existente}'.")
        df_col, df_sur, df_ass = proy.get_collar(), proy.get_survey(), proy.get_intervalo(tabla)
        if df_ass.empty:
            raise ValueError(f"La tabla '{tabla}' no tiene intervalos.")

        # Solo sondajes estimables (coordenadas de Collar + Survey); la tabla fuente no se toca.
        estimables = proy.calcular_estimables()
        n_excluidos = 0
        if estimables:
            antes = set(df_ass['Sondaje'].astype(str).unique())
            df_ass = df_ass[df_ass['Sondaje'].astype(str).isin(estimables)]
            df_col = df_col[df_col['Sondaje'].astype(str).isin(estimables)]
            df_sur = df_sur[df_sur['Sondaje'].astype(str).isin(estimables)]
            n_excluidos = len(antes - set(df_ass['Sondaje'].astype(str).unique()))

        print('======> INICIANDO COMPOSITACIÓN:', datetime.datetime.now())
        df_comp = Compositacion().compositar(
            df_ass, metodo=cfg['metodo'], longitud=float(cfg['longitud']),
            tolerancia=float(cfg['tolerancia']), campos_num=cfg['campos_numericos'],
            campos_cat=cfg['campos_categoricos'], campo_roca=campo_roca if cortar else None,
            cortar_geologia=cortar, faltante_flags=parsear_flags(cfg.get('faltantes_flag', '')),
            faltante_accion=cfg['faltantes_accion'], faltante_valor=float(cfg.get('faltantes_valor', 0)),
            noreg_accion=cfg['noregistrado_accion'], noreg_valor=float(cfg.get('noregistrado_valor', 0)))
        if df_comp is None or df_comp.empty:
            raise RuntimeError('La compositación no generó resultados.')

        _, comps_xyz, _ = crear_desurvey(METODO_TANGENTE_VULCAN).calcular_sondajes(
            df_col, df_sur, df_comp.copy(), pd.DataFrame())
        df_final = consolidar_salida(df_comp, comps_xyz)
        proy.guardar_composito(nombre_comp, df_final, archivo_origen=f'Compositación de {tabla}')
        print('======> COMPOSITACIÓN FINALIZADA:', datetime.datetime.now())

    print(f"Tabla '{nombre_comp}' guardada: {len(df_final):,} compósitos de {df_final['Sondaje'].nunique()} "
          f"sondajes; {n_excluidos} sondaje(s) no estimable(s) excluido(s).")


if __name__ == '__main__':
    main(*sys.argv[1:2])
