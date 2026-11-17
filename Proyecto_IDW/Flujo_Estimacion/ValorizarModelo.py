"""
Nombre : ValorizarModelo.py
Propósito: Ejecuta sin interfaz gráfica la valorización del modelo de bloques de YupAI (FmValorizacion):
           lee el JSON de configuración, asigna la densidad, calcula la recuperación por metal, el NSR y
           el equivalente con Valorizador y escribe el CSV del modelo valorizado.
Uso      : python ValorizarModelo.py [ruta_config.json]
"""

import os
import sys
import json
import datetime

import pandas as pd

from Herramientas.Valorizacion import (
    Valorizador, ParametrosValorizacion, MetalValorizacion, texto_resumen)

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_valorizacion.json')
def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'Valorizacion':
        raise ValueError('El JSON no pertenece al módulo Valorizacion.')

    params = ParametrosValorizacion(
        densidad=cfg.get('densidad', 2.7),
        col_densidad=str(cfg.get('col_densidad', 'Densidad')).strip() or 'Densidad',
        asignar_densidad=bool(cfg.get('asignar_densidad', True)),
        metales=[MetalValorizacion(clave=m.get('clave', ''), columna=m.get('columna', ''),
                                   exp_recuperacion=m.get('exp_recuperacion', ''))
                 for m in cfg.get('metales', [])],
        exp_nsr=str(cfg.get('exp_nsr', '')).strip(),
        col_nsr=str(cfg.get('col_nsr', 'nsr')).strip() or 'nsr',
        exp_equiv=str(cfg.get('exp_equiv', '')).strip(),
        col_equiv=str(cfg.get('col_equiv', 'equiv')).strip() or 'equiv')

    df_bloques = pd.read_csv(cfg['modelo'])
    print('======> INICIANDO VALORIZACIÓN:', datetime.datetime.now())
    _, resumen = Valorizador().valorizar(df_bloques, params, ruta_salida=cfg['salida'])
    print('======> VALORIZACIÓN FINALIZADA:', datetime.datetime.now())
    print(texto_resumen(resumen))


if __name__ == '__main__':
    main(*sys.argv[1:2])
