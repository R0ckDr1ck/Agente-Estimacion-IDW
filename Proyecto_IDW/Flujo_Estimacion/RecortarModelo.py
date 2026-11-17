"""
Nombre : RecortarModelo.py
Propósito: v8. Recorta el modelo de bloques a los bloques cuyo centroide cae dentro de un sólido (.obj), p. ej. el
           sólido del reporte (OXIDOS.obj), y escribe el CSV recortado. Es un paso del flujo entre «modelo» y «estimar»:
           la estimación, la valorización, la categorización y el reporte trabajan solo con esos bloques y corren más
           rápido. La prueba de punto en sólido es la misma del reporte (Herramientas/FiltroSolido.py), así que el
           reporte dentro del sólido da los mismos resultados que con el modelo completo (la estimación de cada bloque
           es independiente de los demás; los compósitos no se recortan).
           conf_recorte.json: {activo, modelo, salida, solido: {ruta, tolerancia_pared, campos: {x, y, z}}}
Uso      : python RecortarModelo.py [ruta_config.json]
"""

import os
import sys
import json
import time
import datetime

import pandas as pd

from Herramientas.FiltroSolido import mascara_en_solido

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_recorte.json')


def recortar(df_bloques, solido):
    """(df recortado, resumen). El recorte conserva el orden y todas las columnas del modelo."""
    dentro = mascara_en_solido(df_bloques, solido)
    n, n_dentro = len(df_bloques), int(dentro.sum())
    if n_dentro == 0:
        raise ValueError('Ningún bloque tiene el centroide dentro del sólido: revise que el sólido y el modelo estén en '
                         'el mismo sistema de coordenadas y las columnas de centroide (campos x, y, z).')
    return df_bloques[dentro].reset_index(drop=True), {'n_bloques': n, 'n_dentro': n_dentro}


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8-sig') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'RecorteModelo':
        raise ValueError('El JSON no pertenece al módulo RecorteModelo.')

    entrada, salida = str(cfg.get('modelo', '')).strip(), str(cfg.get('salida', '')).strip()
    solido = cfg.get('solido', {})
    if not entrada or not os.path.exists(entrada):
        raise FileNotFoundError(f'No existe el modelo de bloques a recortar: {entrada or "(sin indicar)"}')
    if not salida:
        raise ValueError('Indique el CSV del modelo recortado (salida).')
    if os.path.normcase(os.path.abspath(entrada)) == os.path.normcase(os.path.abspath(salida)):
        raise ValueError('El CSV recortado no puede ser el mismo archivo que el modelo de entrada: se perdería el '
                         'modelo completo.')
    if not str(solido.get('ruta', '')).strip():
        raise ValueError('Indique el sólido (.obj) con el que se recorta el modelo.')

    print('======> INICIANDO RECORTE DEL MODELO:', datetime.datetime.now())
    t0 = time.perf_counter()
    df = pd.read_csv(entrada)
    print(f'Modelo: {entrada} ({len(df):,} bloques, {df.shape[1]} columnas)')
    recortado, resumen = recortar(df, solido)
    os.makedirs(os.path.dirname(os.path.abspath(salida)), exist_ok=True)
    recortado.to_csv(salida, index=False, encoding='utf-8-sig')
    pct = 100.0 * resumen['n_dentro'] / resumen['n_bloques']
    print('=' * 70)
    print(f"Recorte con '{os.path.basename(str(solido['ruta']))}' (tolerancia de pared "
          f"{float(solido.get('tolerancia_pared', 0) or 0):g} m): {resumen['n_dentro']:,} de {resumen['n_bloques']:,} "
          f'bloques ({pct:.1f} %) en {time.perf_counter() - t0:.1f} s.')
    print(f'Modelo recortado: {salida}')
    print('======> RECORTE DEL MODELO FINALIZADO:', datetime.datetime.now())


if __name__ == '__main__':
    main(*sys.argv[1:2])
