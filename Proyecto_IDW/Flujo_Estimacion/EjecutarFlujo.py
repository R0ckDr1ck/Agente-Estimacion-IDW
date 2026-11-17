"""
Nombre : EjecutarFlujo.py
Propósito: Ejecuta de punta a punta el flujo de estimación del proyecto, siguiendo los JSON de la carpeta
           Configuraciones (los mismos que edita InterfazParametros.py). Los pasos, en orden:
             1 importar     ImportarBD.py          conf_importar_bd.json   (CSV -> Database.db + CSV)
             2 codificar    CodificarSondajes.py   conf_codificar.json     (ENVAU contra el sólido)
             3 acotar       AcotarSondajes.py      conf_acotamiento.json   (AUPPM_ACOT)
             4 compositar   CompositarSondajes.py  conf_comp.json          (assay_comp)
             5 plan         PlanSondajes.py        conf_plan.json          (v7: plan de sondajes -> muestras de 0.50 m,
                                                   codificación, compositación y unión con los compósitos)
             6 exportar     ExportarBD.py          conf_importar_bd.json   (CSV de todas las tablas de la base)
             7 modelo       CrearModeloBloques.py  conf_crear_mb.json      (modelo de bloques CSV; se omite
                                                   si conf_proyecto.json tiene crear_modelo_bloques = false)
             8 recortar     RecortarModelo.py      conf_recorte.json       (v8: deja solo los bloques dentro de un
                                                   sólido; se omite si conf_recorte.json no existe o activo = false)
             9 estimar      EstimarIDW.py          conf_estimacion.json    (IDW + distancia a 3 sondajes)
            10 valorizar    ValorizarModelo.py     conf_valorizacion.json  (densidad, recuperación, NSR, equiv; se
                                                   omite si conf_proyecto.json tiene valorizar_modelo = false)
            11 categorizar  CategorizarModelo.py   conf_categ.json         (Medido/Indicado/Inferido/Potencial)
            12 reporte      GenerarReporte.py      conf_reporte.json       (Excel de recursos + CSV)
           Se detiene en el primer error. Escribe un log con todo lo impreso en <raiz>\\02_Resultados.
           v5: con origen 'compositos' en conf_importar_bd.json el paso 1 arma la base desde un CSV de
           compósitos y los pasos 2 a 4 se omiten por defecto.
           v7: paso 'plan' (evaluación de un plan de sondajes) y 'valorizar' opcional (valorizar_modelo).
           v8: paso 'recortar' (el modelo se recorta al sólido de reporte antes de estimar).
Uso      : python EjecutarFlujo.py                      # todo el flujo
           python EjecutarFlujo.py --pasos estimar,valorizar,categorizar,reporte
           python EjecutarFlujo.py --desde estimar --hasta reporte
           python EjecutarFlujo.py --config-dir RUTA --listar
"""

import os
import sys
import json
import time
import argparse
import datetime
import importlib
import traceback

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)   # los ejecutores importan Proyecto/, Herramientas/ y Util/ desde esta carpeta

CONFIG_DIR_DEFECTO = os.path.normpath(os.path.join(AQUI, '..', 'Configuraciones'))

# (clave, descripción, módulo, archivo de configuración)
PASOS = [
    ('importar', 'Importar base de datos', 'ImportarBD', 'conf_importar_bd.json'),
    ('codificar', 'Codificar sondajes', 'CodificarSondajes', 'conf_codificar.json'),
    ('acotar', 'Acotar altas leyes', 'AcotarSondajes', 'conf_acotamiento.json'),
    ('compositar', 'Compositar', 'CompositarSondajes', 'conf_comp.json'),
    ('plan', 'Plan de sondajes', 'PlanSondajes', 'conf_plan.json'),
    ('exportar', 'Exportar base a CSV', 'ExportarBD', 'conf_importar_bd.json'),
    ('modelo', 'Crear modelo de bloques', 'CrearModeloBloques', 'conf_crear_mb.json'),
    ('recortar', 'Recortar modelo al sólido', 'RecortarModelo', 'conf_recorte.json'),
    ('estimar', 'Estimar (IDW)', 'EstimarIDW', 'conf_estimacion.json'),
    ('valorizar', 'Valorizar modelo', 'ValorizarModelo', 'conf_valorizacion.json'),
    ('categorizar', 'Categorizar', 'CategorizarModelo', 'conf_categ.json'),
    ('reporte', 'Generar reporte', 'GenerarReporte', 'conf_reporte.json'),
]
CLAVES = [p[0] for p in PASOS]


class Tee:
    """Duplica lo impreso en consola hacia el archivo de log."""

    def __init__(self, *flujos):
        self.flujos = flujos

    def write(self, texto):
        for f in self.flujos:
            f.write(texto)
            f.flush()

    def flush(self):
        for f in self.flujos:
            f.flush()


def leer_proyecto(config_dir):
    """conf_proyecto.json como dict ({} si no existe)."""
    try:
        with open(os.path.join(config_dir, 'conf_proyecto.json'), 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leer_importacion(config_dir):
    """conf_importar_bd.json como dict ({} si no existe)."""
    try:
        with open(os.path.join(config_dir, 'conf_importar_bd.json'), 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leer_plan(config_dir):
    """conf_plan.json como dict ({} si no existe)."""
    try:
        with open(os.path.join(config_dir, 'conf_plan.json'), 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leer_recorte(config_dir):
    """conf_recorte.json como dict ({} si no existe)."""
    try:
        with open(os.path.join(config_dir, 'conf_recorte.json'), 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leer_estimacion_modelo(config_dir):
    """Modelo de entrada de conf_estimacion.json ('' si no se puede leer)."""
    try:
        with open(os.path.join(config_dir, 'conf_estimacion.json'), 'r', encoding='utf-8-sig') as f:
            return str(json.load(f).get('modelo', ''))
    except (OSError, ValueError):
        return ''


def pasos_omitidos(config_dir):
    """Pasos que la selección por defecto / --desde / --hasta no incluye:
       - 'modelo' si conf_proyecto.json tiene crear_modelo_bloques = false (se usa un modelo existente);
       - codificar, acotar y compositar si conf_importar_bd.json tiene origen = 'compositos' (la base se
         arma con compósitos ya codificados y compositados, así que esos pasos ya vienen hechos);
       - 'valorizar' si conf_proyecto.json tiene valorizar_modelo = false (v7);
       - 'plan' si conf_plan.json no existe o tiene activo = false (v7);
       - 'recortar' si conf_recorte.json no existe o tiene activo = false (v8)."""
    omitidos = set()
    if not leer_recorte(config_dir).get('activo', False):
        omitidos.add('recortar')
    proyecto = leer_proyecto(config_dir)
    if not proyecto.get('crear_modelo_bloques', True):
        omitidos.add('modelo')
    if not proyecto.get('valorizar_modelo', True):
        omitidos.add('valorizar')
    if not leer_plan(config_dir).get('activo', False):
        omitidos.add('plan')
    if leer_importacion(config_dir).get('origen', 'sondajes') == 'compositos':
        omitidos.update(('codificar', 'acotar', 'compositar'))
    return omitidos


def seleccionar(args, config_dir):
    """Claves de los pasos a ejecutar, en el orden del flujo. Con 'crear_modelo_bloques' en false
    (conf_proyecto.json) el paso 'modelo' no entra en la selección por defecto / --desde / --hasta:
    la estimación usa un modelo de bloques existente. Con origen 'compositos' (conf_importar_bd.json) tampoco
    entran codificar, acotar ni compositar. Con --pasos X se ejecuta X igualmente."""
    if args.pasos:
        elegidos = [p.strip().lower() for p in args.pasos.split(',') if p.strip()]
        desconocidos = [p for p in elegidos if p not in CLAVES]
        if desconocidos:
            raise SystemExit(f'Pasos desconocidos: {desconocidos}. Disponibles: {CLAVES}')
        return [c for c in CLAVES if c in elegidos]
    desde = CLAVES.index(args.desde) if args.desde else 0
    hasta = CLAVES.index(args.hasta) if args.hasta else len(CLAVES) - 1
    if desde > hasta:
        raise SystemExit('--desde debe ir antes que --hasta en el flujo.')
    omitidos = pasos_omitidos(config_dir)
    return [c for c in CLAVES[desde:hasta + 1] if c not in omitidos]


def carpeta_log(config_dir):
    """<raiz>\\02_Resultados según conf_proyecto.json; si no existe, la carpeta del proyecto."""
    ruta = os.path.join(config_dir, 'conf_proyecto.json')
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            raiz = json.load(f)['raiz']
    except (OSError, KeyError, ValueError):
        raiz = os.path.normpath(os.path.join(config_dir, '..'))
    carpeta = os.path.join(raiz, '02_Resultados')
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def ejecutar(claves, config_dir):
    """Corre los pasos en orden; devuelve [(clave, descripción, estado, segundos)]."""
    por_clave = {p[0]: p for p in PASOS}
    resultados = []
    for clave in claves:
        _, descripcion, modulo, archivo = por_clave[clave]
        ruta_config = os.path.join(config_dir, archivo)
        n = PASOS.index(por_clave[clave]) + 1
        print('\n' + '#' * 78)
        print(f'# PASO {n}/{len(PASOS)}: {descripcion}  [{modulo}]  {archivo}')
        print('#' * 78, flush=True)
        t0 = time.perf_counter()
        try:
            if not os.path.exists(ruta_config):
                raise FileNotFoundError(f'No existe la configuración: {ruta_config}')
            importlib.import_module(modulo).main(ruta_config)
            resultados.append((clave, descripcion, 'OK', time.perf_counter() - t0))
        except BaseException:
            traceback.print_exc(file=sys.stdout)
            resultados.append((clave, descripcion, 'ERROR', time.perf_counter() - t0))
            break
    return resultados


def main(argv=None):
    ap = argparse.ArgumentParser(description='Ejecuta el flujo de estimación completo.')
    ap.add_argument('--config-dir', default=CONFIG_DIR_DEFECTO, help='carpeta con los conf_*.json')
    ap.add_argument('--pasos', help='lista separada por comas de pasos a ejecutar')
    ap.add_argument('--desde', choices=CLAVES, help='primer paso a ejecutar')
    ap.add_argument('--hasta', choices=CLAVES, help='último paso a ejecutar')
    ap.add_argument('--listar', action='store_true', help='lista los pasos y termina')
    args = ap.parse_args(argv)

    if args.listar:
        for i, (clave, descripcion, modulo, archivo) in enumerate(PASOS, 1):
            print(f'{i}  {clave:12s} {descripcion:28s} {modulo:22s} {archivo}')
        return 0

    config_dir = os.path.abspath(args.config_dir)
    claves = seleccionar(args, config_dir)
    proyecto = leer_proyecto(config_dir)
    log_ruta = os.path.join(carpeta_log(config_dir),
                            f'Flujo_Log_{datetime.datetime.now():%Y%m%d-%H%M%S}.txt')
    stdout_original = sys.stdout
    with open(log_ruta, 'w', encoding='utf-8') as log:
        sys.stdout = Tee(stdout_original, log)
        try:
            print(f'FLUJO DE ESTIMACIÓN  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
            print(f'Configuraciones: {config_dir}')
            print(f'Pasos: {", ".join(claves)}')
            if not proyecto.get('crear_modelo_bloques', True):
                print('Modelo de bloques: se usa uno existente (paso "modelo" omitido): '
                      f'{proyecto.get("modelo_bloques_existente", "ver conf_estimacion.json")}')
            if not proyecto.get('valorizar_modelo', True):
                print('Valorización: omitida (valorizar_modelo = false); la categorización toma el modelo estimado.')
            plan = leer_plan(config_dir)
            if plan.get('activo', False):
                print('Plan de sondajes: los compósitos del plan se agregan a los existentes antes de estimar '
                      f'({plan.get("collar", {}).get("archivo", "")}).')
            recorte = leer_recorte(config_dir)
            if recorte.get('activo', False):
                print('Recorte: el modelo se recorta al sólido '
                      f'{os.path.basename(str(recorte.get("solido", {}).get("ruta", "")))} antes de estimar.')
                est_modelo = leer_estimacion_modelo(config_dir)
                if 'recortar' in claves and est_modelo and \
                        os.path.normcase(os.path.normpath(est_modelo)) != \
                        os.path.normcase(os.path.normpath(str(recorte.get('salida', '')))):
                    print('AVISO: la estimación (conf_estimacion.json) no lee el modelo recortado '
                          f'({recorte.get("salida", "")}) sino {est_modelo}.')
            importacion = leer_importacion(config_dir)
            if importacion.get('origen', 'sondajes') == 'compositos':
                print('Base de datos: se arma con compósitos ya codificados y compositados (pasos codificar, '
                      f'acotar y compositar omitidos): {importacion.get("compositos", {}).get("archivo", "")}')
            t0 = time.perf_counter()
            resultados = ejecutar(claves, config_dir)
            print('\n' + '=' * 78)
            print('RESUMEN DEL FLUJO')
            for clave, descripcion, estado, seg in resultados:
                print(f'  {estado:5s} {clave:12s} {descripcion:28s} {seg:8.1f} s')
            ejecutados = [r[0] for r in resultados]
            for clave in claves:
                if clave not in ejecutados:
                    print(f'  OMIT  {clave:12s} (no se ejecutó por el error anterior)')
            ok = len(resultados) == len(claves) and all(r[2] == 'OK' for r in resultados)
            print(f'Total: {time.perf_counter() - t0:.1f} s | {"FLUJO COMPLETO" if ok else "FLUJO INTERRUMPIDO"}')
            print(f'Log: {log_ruta}')
        finally:
            sys.stdout = stdout_original
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
