"""
Nombre : FiltroSolido.py
Propósito: Filtra un modelo de bloques (DataFrame) dejando solo los bloques cuyo centroide cae dentro de un sólido
           (.obj). Es la prueba de punto en sólido que usa la codificación (Fast Winding Number con filtro AABB,
           re-evaluación exacta de la banda de frontera y rescate por tolerancia de pared). La comparten el reporte
           de recursos (GenerarReporte.py, 'solido') y el recorte del modelo (RecortarModelo.py, v8), de modo que ambos
           eligen exactamente los mismos bloques.
Creacion : 14-Noviembre-2026
"""

import os
from pathlib import Path

import numpy as np

from Herramientas.CodificadorEspacialV2 import CodificadorEspacialV2
from Herramientas.CodificadorSondajes import CodificadorSondajes

EPS_WINDING = 0.01   # misma banda de re-evaluación del winding number que usa el modelo de bloques


def columnas_centroide(solido, df_bloques):
    """Nombres (x, y, z) de las columnas de centroide del bloque según solido['campos'] (XC, YC, ZC por defecto);
    ValueError si el modelo no las trae."""
    campos = solido.get('campos', {})
    columnas = [campos.get('x') or 'XC', campos.get('y') or 'YC', campos.get('z') or 'ZC']
    faltan = [c for c in columnas if c not in df_bloques.columns]
    if faltan:
        raise ValueError(f"El modelo no tiene las columnas de centroide del bloque: {', '.join(faltan)}.")
    return columnas


def mascara_en_solido(df_bloques, solido):
    """Máscara booleana (una por bloque) de los centroides dentro del sólido de la clave {'ruta',
    'tolerancia_pared', 'campos': {'x', 'y', 'z'}}."""
    ruta = str(solido['ruta']).strip()
    if not os.path.exists(ruta):
        raise FileNotFoundError(f'No existe el sólido: {ruta}')
    columnas = columnas_centroide(solido, df_bloques)
    mesh = CodificadorEspacialV2.load_solid(Path(ruta))
    puntos = df_bloques[columnas].to_numpy(dtype=float)
    validos = np.isfinite(puntos).all(axis=1)
    return CodificadorSondajes._puntos_en_solido(
        puntos, validos, mesh, float(solido.get('tolerancia_pared', 0) or 0), EPS_WINDING)


def filtrar_en_solido(df_bloques, solido, etiqueta='Reporte'):
    """Deja solo los bloques cuyo centroide cae dentro del sólido (.obj). La tolerancia de pared rescata
    centroides que quedan sobre la superficie. `etiqueta` solo cambia el mensaje impreso."""
    ruta = str(solido['ruta']).strip()
    if not os.path.exists(ruta):
        raise FileNotFoundError(f'No existe el sólido del {etiqueta.lower()}: {ruta}')
    dentro = mascara_en_solido(df_bloques, solido)
    print(f"{etiqueta} dentro del sólido '{Path(ruta).stem}': {int(dentro.sum()):,} de {len(df_bloques):,} "
          'bloques con el centroide dentro.')
    return df_bloques[dentro].reset_index(drop=True)
