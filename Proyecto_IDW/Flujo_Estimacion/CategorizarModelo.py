"""
Nombre : CategorizarModelo.py
Propósito: Ejecuta sin interfaz gráfica la categorización de recursos de YupAI (FmCategorizacion): lee el
           JSON de configuración, asigna la categoría (1 Medido, 2 Indicado, 3 Inferido, 4 Potencial) a
           cada bloque con Categorizador según la distancia a 3 sondajes, el nº de sondajes y la pasada de
           la estimación, y escribe el CSV del modelo categorizado.
Uso      : python CategorizarModelo.py [ruta_config.json]
"""

import os
import sys
import json
import datetime

import pandas as pd

from Herramientas.Categorizacion import Categorizador, ParametrosCategorizacion, texto_resumen

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_categ.json')
def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'Categorizacion':
        raise ValueError('El JSON no pertenece al módulo Categorizacion.')

    campos, dist = cfg.get('campos', {}), cfg.get('distancias', {})
    piso = cfg.get('piso', {})   # sin la clave 'piso' no se aplica (modelos sin categoría previa)
    params = ParametrosCategorizacion(
        col_distancia=campos.get('distancia', ''), col_nholes=campos.get('nholes', ''),
        col_pass=campos.get('pass', ''), col_categoria=campos.get('col_categoria', 'categ') or 'categ',
        dist_medido=float(dist.get('medido', 15)), dist_indicado=float(dist.get('indicado', 30)),
        dist_inferido=float(dist.get('inferido', 50)), dist_potencial=float(dist.get('potencial', 100)),
        col_anterior=(piso.get('columna_anterior', 'categ') if piso.get('activo', False) else ''))

    df_bloques = pd.read_csv(cfg['modelo'])
    print('======> INICIANDO CATEGORIZACIÓN:', datetime.datetime.now())
    _, resumen = Categorizador().categorizar(df_bloques, params, ruta_salida=cfg['salida'])
    print('======> CATEGORIZACIÓN FINALIZADA:', datetime.datetime.now())
    print(texto_resumen(resumen))


if __name__ == '__main__':
    main(*sys.argv[1:2])
