"""
Nombre : ExportarBD.py
Propósito: Exporta a CSV todas las tablas de la base .db del proyecto (collar, survey, assay, assay_comp, ...)
           con su estado actual, de modo que los CSV reflejen lo que dejaron la codificación, el acotamiento
           y la compositación sobre la base. Usa la misma configuración de la importación (proyecto,
           carpeta_csv_salida y decimales de conf_importar_bd.json).
Uso      : python ExportarBD.py [ruta_config.json]
"""

import os
import sys
import json

from Proyecto.ProyectoSondajes import ProyectoSondajes
from ImportarBD import exportar_csv, archivos_de_entrada

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_importar_bd.json')


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    log = []
    with ProyectoSondajes.abrir(cfg['proyecto']) as proy:
        exportar_csv(proy, cfg['carpeta_csv_salida'], cfg.get('decimales', 3), log, archivos_de_entrada(cfg))
    print('\n'.join(log))


if __name__ == '__main__':
    main(*sys.argv[1:2])
