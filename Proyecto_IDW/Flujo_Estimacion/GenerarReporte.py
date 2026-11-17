"""
Nombre : GenerarReporte.py
Propósito: Ejecuta sin interfaz gráfica el Reporte de Recursos de YupAI (FmReporteRecursos): lee el JSON de
           configuración, calcula el resumen por dominio y categoría con ReporteRecursos y escribe el Excel
           (hojas 'Resumen' y 'Resumen_Potencial') con el nombre estándar
           Recursos_<mina>_<periodo>_No_Env_<fecha-hora>.xlsx. Además exporta cada hoja a CSV.
           Con la clave 'solido' activa (v3) reporta solo los bloques cuyo centroide está dentro de un .obj
           y agrega _Sol_<nombre> al nombre del archivo.
           Con la clave 'minado' activa (v4) descarta lo ya minado según una variable del modelo,
           binaria (1 = minado) o porcentaje remanente (100 = no minado, 0 = minado).
           Con la clave 'comparacion' activa (v5) agrega la hoja 'Comparacion_Categ': tonelaje por
           categoría de una categoría anterior del modelo (p. ej. 'categ-anterior') vs la calculada.
           v7: 'valorizado' = false (el modelo no está valorizado: sin NSR/equivalente, cut-off sobre la ley del primer
           metal) y la clave 'ganancia' agrega la hoja 'Ganancia_Plan' (ganancia de tonelaje por categoría y ratio de
           conversión = ganancia / longitud total del plan de sondajes, leída del Plan_Resumen.json del paso «plan»).
Uso     : python GenerarReporte.py [ruta_config.json]
"""

import os
import sys
import json
import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from Herramientas.FiltroSolido import filtrar_en_solido as _filtrar_en_solido
from Herramientas.ReporteRecursos import (
    ReporteRecursos, ParametrosReporte, MetalReporte, nombre_archivo_salida, texto_resumen)

CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'Configuraciones', 'conf_reporte.json')

def filtrar_en_solido(df_bloques, solido):
    """Deja solo los bloques cuyo centroide cae dentro del sólido (.obj) de la clave 'solido' del JSON:
    {'activo', 'ruta', 'tolerancia_pared', 'campos': {'x', 'y', 'z'}}. Es la misma prueba que usa el recorte del
    modelo (Herramientas/FiltroSolido.py), así que ambos eligen los mismos bloques."""
    return _filtrar_en_solido(df_bloques, solido, 'Reporte')


def exportar_csv(ruta_xlsx):
    """Exporta cada hoja del Excel a CSV (<libro>_<hoja>.csv), tal cual se ve en la hoja."""
    base = os.path.splitext(ruta_xlsx)[0]
    salidas = []
    for hoja, df in pd.read_excel(ruta_xlsx, sheet_name=None, header=None).items():
        ruta = f'{base}_{hoja}.csv'
        df.to_csv(ruta, index=False, header=False, encoding='utf-8-sig')
        salidas.append(ruta)
    return salidas


def leer_plan_resumen(ganancia):
    """(longitud total del plan en m, resumen del plan) para la hoja de ganancia (v7). La longitud sale de
    'longitud_manual' si se indica; si no, del Plan_Resumen.json que escribe el paso «plan»."""
    info = {}
    ruta = str(ganancia.get('resumen_plan', '') or '').strip()
    if ruta and os.path.exists(ruta):
        with open(ruta, 'r', encoding='utf-8-sig') as f:
            info = json.load(f)
        print(f"Plan de sondajes: {ruta} ({info.get('fecha', 'sin fecha')}): {info.get('n_sondajes', '?')} sondajes, "
              f"{float(info.get('longitud_total', 0)):,.1f} m.")
    manual = str(ganancia.get('longitud_manual', '') or '').strip()
    if manual:
        try:
            return float(manual.replace(',', '.')), info
        except ValueError:
            raise ValueError(f"'ganancia.longitud_manual' no es un número: «{manual}».") from None
    if info.get('longitud_total'):
        return float(info['longitud_total']), info
    raise ValueError('La ganancia del plan necesita la longitud total de los sondajes: no existe el resumen del plan '
                     f"({ruta or 'sin indicar'}) y no se indicó una longitud manual. Ejecute el paso «plan» o "
                     'indique la longitud.')


def main(ruta_config=CONFIG_DEFECTO):
    with open(ruta_config, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    if cfg.get('__modulo__') != 'ReporteRecursos':
        raise ValueError('El JSON no pertenece al módulo ReporteRecursos.')

    c = cfg.get('columnas', {})
    minado = cfg.get('minado', {})
    comparacion = cfg.get('comparacion', {})
    ganancia = cfg.get('ganancia', {})
    longitud_plan, info_plan = leer_plan_resumen(ganancia) if ganancia.get('activo') else (0.0, {})
    params = ParametrosReporte(
        col_grupo=c.get('coboGrupo', ''), cutoff=float(cfg.get('cutoff', 0)),
        metales=[MetalReporte(columna=m.get('columna', ''), etiqueta=m.get('etiqueta', ''),
                              unidad=m.get('unidad', ''), factor=m.get('factor', '1') or '1')
                 for m in cfg.get('metales', [])],
        variable_corte=cfg.get('variable_corte', 'nsr'),
        col_categoria=c.get('coboCategoria', ''), col_densidad=c.get('coboDensidad', ''),
        col_volumen=c.get('coboVolumen', ''), col_x_len=c.get('coboXLen', ''),
        col_y_len=c.get('coboYLen', ''), col_z_len=c.get('coboZLen', ''),
        col_nsr=c.get('coboNsr', ''), col_equiv=c.get('coboEquiv', ''),
        metal_principal=cfg.get('metal_principal', '') or 'Au',
        unidad_principal=cfg.get('unidad_principal', ''),
        fecha_reporte=cfg.get('fecha_reporte', ''), etiqueta_precios=cfg.get('etiqueta_precios', ''),
        col_minado=minado.get('variable', '') if minado.get('activo') else '',
        tipo_minado=minado.get('tipo', 'binario'),
        col_categ_anterior=comparacion.get('categoria_anterior', '') if comparacion.get('activo') else '',
        valorizado=bool(cfg.get('valorizado', True)),
        ganancia=bool(ganancia.get('activo')), longitud_plan=longitud_plan, info_plan=info_plan)

    carpeta = cfg['salida']
    os.makedirs(carpeta, exist_ok=True)
    ruta_salida = nombre_archivo_salida(params, carpeta)
    df_bloques = pd.read_csv(cfg['modelo'])

    solido = cfg.get('solido', {})
    if solido.get('activo') and str(solido.get('ruta', '')).strip():
        df_bloques = filtrar_en_solido(df_bloques, solido)
        base, ext = os.path.splitext(ruta_salida)
        ruta_salida = f"{base}_Sol_{Path(solido['ruta']).stem}{ext}"

    print('======> INICIANDO REPORTE DE RECURSOS:', datetime.datetime.now())
    info = ReporteRecursos(params).generar(df_bloques, ruta_salida)
    print('======> REPORTE DE RECURSOS FINALIZADO:', datetime.datetime.now())
    print(texto_resumen(info))
    for ruta in exportar_csv(ruta_salida):
        print('CSV:', ruta)


if __name__ == '__main__':
    main(*sys.argv[1:2])
