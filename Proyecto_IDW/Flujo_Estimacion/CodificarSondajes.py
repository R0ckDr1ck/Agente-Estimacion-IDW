"""
Nombre : CodificarSondajes.py
Propósito: Ejecuta sin interfaz gráfica la codificación de sondajes de YupAI (FmCodificacionSondajes):
           lee el JSON de configuración, codifica el campo indicado de la tabla intervalar contra los
           sólidos (.obj) con CodificadorSondajes (desurvey Tangente Vulcan) y reescribe la tabla en la
           base .db con ProyectoSondajes.actualizar_tabla.
Uso      : python CodificarSondajes.py [ruta_config.json]
"""

import os
import sys
import json
import datetime

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Herramientas.CodificadorSondajes import CodificadorSondajes, MODO_DEFECTO, MODO_MANTENER

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_codificar.json')
def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'CodificacionSondajes':
        raise ValueError('El JSON no pertenece al módulo CodificacionSondajes.')
    ejecutar(cfg)


def ejecutar(cfg):
    """Codifica según el dict de configuración (el contenido de conf_codificar.json). v7: PlanSondajes.py lo
    reutiliza con otra base (cfg['proyecto']) para codificar los sondajes del plan con el mismo seteo."""
    tabla, campo = cfg['tabla'], cfg['campo']
    solidos = [{'ruta': s['ruta'], 'valor': str(s['valor']).strip(), 'prioridad': int(s['prioridad']),
                'orden': i} for i, s in enumerate(cfg['solidos'])]
    for s in solidos:
        if not os.path.exists(s['ruta']):
            raise FileNotFoundError(f"No existe el sólido: {s['ruta']}")
    modo = MODO_DEFECTO if cfg.get('no_codificados', 'defecto') == 'defecto' else MODO_MANTENER

    with ProyectoSondajes.abrir(cfg['proyecto']) as proy:
        for t in (ProyectoSondajes.NOMBRE_COLLAR, ProyectoSondajes.NOMBRE_SURVEY, tabla):
            if not proy.existe_tabla(t):
                raise ValueError(f"La base no contiene la tabla '{t}'.")
        if 'Profundidad' not in proy.columnas(ProyectoSondajes.NOMBRE_COLLAR):
            raise ValueError("Collar no tiene la columna 'Profundidad', requerida por el desurvey.")
        if campo in ('Sondaje', 'Desde', 'Hasta'):
            raise ValueError(f"'{campo}' es una columna estructural y no puede codificarse.")

        print('======> INICIANDO CODIFICACIÓN:', datetime.datetime.now())
        df_out, resumen = CodificadorSondajes().codificar(
            proy.get_collar(), proy.get_survey(), proy.get_intervalo(tabla), solidos, campo,
            modo_no_codificado=modo, valor_defecto=str(cfg.get('valor_defecto', '-99')),
            tolerancia_pared=float(cfg.get('tolerancia_pared', 0.0)))
        proy.actualizar_tabla(tabla, df_out)
        print('======> CODIFICACIÓN FINALIZADA:', datetime.datetime.now())

    print(f"\nTabla '{tabla}', campo '{campo}':")
    for s in resumen['por_solido']:
        print(f"  {s['nombre']} (valor '{s['valor']}', prioridad {s['prioridad']}): {s['n']:,} tramos")
    print(f"  codificados {resumen['n_codificados']:,} de {resumen['n_filas']:,}; "
          f"sin codificar {resumen['n_sin_codificar']:,}; sin XYZ {resumen['n_sin_xyz']:,}")
    if resumen['sondajes_sin_xyz']:
        print(f"  sondajes sin XYZ: {resumen['sondajes_sin_xyz']}")


if __name__ == '__main__':
    main(*sys.argv[1:2])
