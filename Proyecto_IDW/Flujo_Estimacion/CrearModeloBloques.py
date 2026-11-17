"""
Nombre : CrearModeloBloques.py
Propósito: Ejecuta sin interfaz gráfica la creación del modelo de bloques de YupAI (FmModeloBloques): lee el
           JSON de configuración, genera la grilla con subbloqueo y bearing, codifica las variables contra
           los sólidos (.obj), aplica las excepciones y escribe el CSV con GeneradorModeloBloques.
Uso      : python CrearModeloBloques.py [ruta_config.json]
"""

import os
import sys
import json
import datetime

from Herramientas.ModeloBloques import (
    EsquemaGrilla, VariableBloque, LimiteSolido, ExcepcionExpresion, GeneradorModeloBloques,
    texto_resumen)

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_crear_mb.json')
def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'ModeloBloques':
        raise ValueError('El JSON no pertenece al módulo ModeloBloques.')

    esquema = EsquemaGrilla(**cfg['esquema'])
    variables = [VariableBloque(v['nombre'], v.get('tipo', 'Float'), v.get('valor_defecto', -99),
                                v.get('descripcion', '')) for v in cfg['variables']]
    solidos = []
    for i, s in enumerate(cfg.get('solidos', [])):
        if not os.path.exists(s['ruta']):
            raise FileNotFoundError(f"No existe el sólido: {s['ruta']}")
        solidos.append(LimiteSolido(s['ruta'], s['variable'], s['valor'],
                                    int(s.get('prioridad', 1)), orden=i))
    excepciones = [ExcepcionExpresion(e['expresion'], e.get('descripcion', ''))
                   for e in cfg.get('excepciones', []) if str(e.get('expresion', '')).strip()]
    salida = cfg['salida']
    os.makedirs(os.path.dirname(salida), exist_ok=True)

    print('======> INICIANDO CREACIÓN DE MODELO DE BLOQUES:', datetime.datetime.now())
    _, resumen = GeneradorModeloBloques().generar(
        esquema, variables, solidos, excepciones, ruta_salida=salida,
        tolerancia_pared=float(cfg.get('tolerancia_pared', 0.0)))
    print('======> MODELO DE BLOQUES FINALIZADO:', datetime.datetime.now())
    print(texto_resumen(resumen))


if __name__ == '__main__':
    main(*sys.argv[1:2])
