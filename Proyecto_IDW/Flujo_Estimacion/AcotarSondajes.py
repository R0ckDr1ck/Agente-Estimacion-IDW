"""
Nombre : AcotarSondajes.py
Propósito: Ejecuta sin interfaz gráfica el acotamiento (top-cut) de YupAI (FmAcotamiento): lee el JSON de
           configuración, aplica los topes por dominio con Acotamiento.aplicar_topes (genera la columna
           <variable>_ACOT), reescribe la tabla con ProyectoSondajes.actualizar_tabla y guarda los topes
           en la tabla _acotamiento de la base, igual que el botón «Aplicar» del formulario.
Uso      : python AcotarSondajes.py [ruta_config.json]
"""

import os
import sys
import json

from Proyecto.ProyectoSondajes import ProyectoSondajes
from Herramientas.Acotamiento import Acotamiento, METODO_MANUAL
from Herramientas.EstadisticaCodificacion import BDL_MITAD

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_acotamiento.json')
def nulos_desde_texto(texto):
    """Igual que FmAcotamiento._nulos: '-99, -999' -> (-99.0, -999.0)."""
    nulos = []
    for parte in str(texto).replace(';', ',').split(','):
        parte = parte.strip()
        if parte:
            try:
                nulos.append(float(parte))
            except ValueError:
                pass
    return tuple(nulos)


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'Acotamiento':
        raise ValueError('El JSON no pertenece al módulo Acotamiento.')

    tabla, variable, campo = cfg['tabla'], cfg['variable'], cfg['campo_dominio']
    nulos = nulos_desde_texto(cfg.get('nulos', '-99'))
    bdl = cfg.get('bdl') or BDL_MITAD
    campo_salida = f'{variable}_ACOT'
    topes_cfg = (cfg.get('topes') or {}).get(variable, {})
    topes = {dom: info.get('tope') for dom, info in topes_cfg.items() if info.get('tope') is not None}

    ac = Acotamiento()
    with ProyectoSondajes.abrir(cfg['proyecto']) as proy:
        if not proy.existe_tabla(tabla):
            raise ValueError(f"La base no contiene la tabla '{tabla}'.")
        df = proy.get_intervalo(tabla)
        for c in (variable, campo):
            if c not in df.columns:
                raise ValueError(f"La tabla '{tabla}' no tiene la columna '{c}'.")

        df_out, afect = ac.aplicar_topes(df, variable, campo, topes, campo_salida, nulos, bdl)
        proy.actualizar_tabla(tabla, df_out)

        # Filas de _acotamiento (réplica de FmAcotamiento._filas_topes_para_db).
        filas = []
        for dom, info in topes_cfg.items():
            tope = info.get('tope')
            ef = ac.resumen_efecto(*ac.preparar(df, variable, campo, dom, nulos, bdl), tope) if tope else None
            par = info.get('parametro')
            filas.append({
                'dominio': dom, 'tope': tope, 'metodo': info.get('metodo', METODO_MANUAL),
                'parametros': {'parametro': par} if par is not None else None,
                'campo_salida': campo_salida,
                'n_afectados': ef['n_afectados'] if ef else 0,
                'pct_metal': ef['pct_metal'] if ef else None,
            })
        proy.guardar_acotamiento(tabla, variable, filas)

    print(f"Columna '{campo_salida}' generada en '{tabla}'. Muestras recortadas por dominio: {afect}")
    for f in filas:
        print(f"  dominio {f['dominio']}: tope {f['tope']} ({f['metodo']}), "
              f"{f['n_afectados']:,} muestras afectadas, {f['pct_metal']:.3f} % del metal")


if __name__ == '__main__':
    main(*sys.argv[1:2])
