"""
 Nombre : Const.py
 Propósito: Clase que agrupa todas las constantes y variables globales definidas en el aplicativo
 Creacion : 19-Junio-2023
 Autor : Eloy Paredes Quispe
 Lugar : Buenaventura - Lima
 Modificado :
 Modificado :

"""
import os


class Const:
    DIR_BASE = os.getcwd()  # Directorio base donde se ejecuta el aplicativo
    DIR_ICON = DIR_BASE + '\\_res\\icon\\'  # Directorio de los iconos del sistema
    DIR_TMP = DIR_BASE + '\\_temp\\'  # directorio para archivos temporales
    VAL_DEFECTO = -99       # Valor por defecto para variables del modelo de bloques
    LIM_BLO_SUP = 100000    # Nro de bloques superpuestos máximo que se pueden eliminar
    VER = '0.1.69'          # Versión de Yupay
    MINA = 'BVN'      # Mina con la que se está trabajando
    LAST_DIR = '.'    # Directorio de la última carpeta abierta


    # Diccionario auxiliar para ordenamiento por meses.
    MESES = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
             'setiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}

