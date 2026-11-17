##########################################################################################
# Nombre : Util.py
# Propósito: Implementación de algunos métodos utilitarios de uso general
# Creacion : 09-Mayo-2018
# Autor : Eloy Paredes Quispe
# Lugar : Buenaventura - Lima
# Modificado :
# Modificado :
#
##########################################################################################
import math  # NO ELIMINAR ES IMPORTANTE PARA QUE FUNCIONEN LAS FÓRMULAS DINÁMICAS!!!
import datetime
import os
import pathlib
import re
import numpy as np
import shutil
import pandas as pd
import subprocess
import time
import cv2


from Util.Const import Const

from PySide6 import QtWidgets
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QFileDialog, QMessageBox
from PySide6.QtGui import QKeySequence

class Util:
    # ==============================================================================================================
    # Utilitarios IU
    # ==============================================================================================================
    # mover elementos seleccionados del primer QListWidget al segundo QListWidget
    @staticmethod
    def izquierda_a_derecha(izquierda, derecha):
        seleccionados = izquierda.selectedItems()
        for item in seleccionados:
            derecha.addItem(item.text())

        i = len(seleccionados) - 1
        while i >= 0:
            izquierda.takeItem(izquierda.row(seleccionados[i]))
            i -= 1

    # mover todos los elementos del primer QListWidget al segundo QListWidget
    @staticmethod
    def izquierda_a_derecha_todo(izquierda, derecha):
        total = izquierda.count()
        i = 0
        while i < total:
            derecha.addItem(izquierda.item(i).text())
            i += 1
        izquierda.clear()

    # Filtrar los elementos del wxListBox
    @staticmethod
    def aplicar_filtro(control, filtro, opcion):
        # convertir filtro a minusculas y eliminar espacios en blanco
        filtro = filtro.lower().strip()
        elementos = control.GetItems()
        control.Clear()
        for elemento in elementos:
            # convertir elemento en minuscula para comparar
            elemento_m = elemento.lower()
            if opcion == 'Termina Con':
                if elemento_m.endswith(filtro):
                    control.Append(elemento)
            else:
                if not elemento_m.endswith(filtro):
                    control.Append(elemento)

    # Guardar la selección de los elementos del wxGrid
    @staticmethod
    def guardar_seleccion(grid, archivo):
        seleccion = []
        for i in range(grid.GetNumberRows()):
            if grid.GetCellValue(i, 1):
                seleccion.append(grid.GetCellValue(i, 0))

        # guardar la selección en archivo
        with open(archivo, 'w') as ar:
            ar.write('\n'.join(seleccion))

    # Abrir la selección de los elementos del wxGrid
    @staticmethod
    def abrir_seleccion(grid, archivo):
        # recupear la selección den archivo
        with open(archivo, 'r') as ar:
            seleccion = ar.read().splitlines()

        # actualizar el grid con la selección
        for sel in seleccion:
            for i in range(grid.GetNumberRows()):
                if sel == grid.GetCellValue(i, 0):
                    grid.SetCellValue(i, 1, '1')
                    break

    # Resetear variables seleccionadas
    @staticmethod
    def resetear_seleccion(grid):
        for i in range(grid.GetNumberRows()):
            grid.SetCellValue(i, 1, '')

    @staticmethod
    def obtener_items_seleccionados(list_ctrl):
        """    Gets the selected items for the list control.Selection is returned as a list of selected indices,
        low to high.
        """
        if list_ctrl.GetSelectedItemCount() == 0:
            return None

        selection = []
        index = list_ctrl.GetFirstSelected()
        selection.append(index)
        while len(selection) != list_ctrl.GetSelectedItemCount():
            index = list_ctrl.GetNextSelected(index)
            selection.append(index)

        return selection

    # mover elementos seleccionados del primer wxListCtrl al segundo wxListCtrl
    @staticmethod
    def izquierda_a_derecha_lc(izquierda, derecha):
        if izquierda.GetSelectedItemCount() > 0:
            seleccionados = Util.obtener_items_seleccionados(izquierda)
            for i in seleccionados:
                indice = derecha.InsertItem(derecha.GetItemCount(), izquierda.GetItem(i, 0).GetText())
                derecha.SetItemData(indice, izquierda.GetItemData(i))  # datos para ordenar el listctrl
                derecha.SetItem(indice, 0, izquierda.GetItem(i, 0).GetText())
                derecha.SetItem(indice, 1, izquierda.GetItem(i, 1).GetText())
                derecha.SetItem(indice, 2, izquierda.GetItem(i, 2).GetText())

            i = len(seleccionados) - 1
            while i >= 0:
                izquierda.DeleteItem(seleccionados[i])
                i -= 1

    # mover todos los elementos del primer wxListCtrl al segundo wxListCtrl
    @staticmethod
    def izquierda_a_derecha_todo_lc(izquierda, derecha):
        total = izquierda.GetItemCount()
        i = 0
        while i < total:
            indice = derecha.InsertItem(derecha.GetItemCount(), izquierda.GetItem(i, 0).GetText())
            derecha.SetItemData(indice, izquierda.GetItemData(i))  # datos para ordenar el listctrl
            derecha.SetItem(indice, 0, izquierda.GetItem(i, 0).GetText())
            derecha.SetItem(indice, 1, izquierda.GetItem(i, 1).GetText())
            derecha.SetItem(indice, 2, izquierda.GetItem(i, 2).GetText())
            i += 1
        izquierda.DeleteAllItems()

    # ======================== Funcionaliad de Copiar y pegar en QTableWidget
    @staticmethod
    def copy_selection(table_widget):
        """Copia las celdas seleccionadas en formato tabular."""
        selected_ranges = table_widget.selectedRanges()
        if not selected_ranges:
            return

        copied_text = ""
        for row in range(selected_ranges[0].topRow(), selected_ranges[0].bottomRow() + 1):
            row_data = []
            for col in range(selected_ranges[0].leftColumn(), selected_ranges[0].rightColumn() + 1):
                item = table_widget.item(row, col)
                row_data.append(item.text() if item else "")
            copied_text += "\t".join(row_data) + "\n"

        QApplication.clipboard().setText(copied_text.strip())

    @staticmethod
    def paste_selection(table_widget):
        """Pega los datos copiados en la selección actual."""
        clipboard_text = QApplication.clipboard().text()
        if not clipboard_text:
            return

        selected_ranges = table_widget.selectedRanges()
        if not selected_ranges:
            return

        start_row = selected_ranges[0].topRow()
        start_col = selected_ranges[0].leftColumn()

        rows = clipboard_text.split("\n")
        for i, row_text in enumerate(rows):
            cols = row_text.split("\t")
            for j, cell_text in enumerate(cols):
                target_row = start_row + i
                target_col = start_col + j
                if target_row < table_widget.rowCount() and target_col < table_widget.columnCount():
                    table_widget.setItem(target_row, target_col, QTableWidgetItem(cell_text))

    @staticmethod
    def handle_copiar_pegar(event, table_widget):
        """Maneja los eventos de teclado para copiar y pegar."""
        if event.matches(QKeySequence.Copy):  # Ctrl+C
            Util.copy_selection(table_widget)
        elif event.matches(QKeySequence.Paste):  # Ctrl+V
            Util.paste_selection(table_widget)

    def handle_copiar(event, table_widget):
        """Maneja los eventos de teclado sólo para copiar."""
        if event.matches(QKeySequence.Copy):  # Ctrl+C
            Util.copy_selection(table_widget)

    # ==============================================================================================================
    # Utilitarios de uso general
    # ==============================================================================================================
    # determinar si una cadena es un numero
    @staticmethod
    def es_numero(cadena):
        try:
            float(cadena)
            return True
        except ValueError:
            pass
        return False

    # determinar si una cadena es un numero entero
    @staticmethod
    def es_entero(cadena):
        try:
            int(cadena)
            return True
        except ValueError:
            pass
        return False

    # poner comillas en caso de que se trate de una cadena, caso contrario dar formato
    @staticmethod
    def comillas(cadena):
        if cadena is None or cadena == '':
            return "' '"
        if Util.es_numero(cadena):
            return '{:10.12f}'.format(float(cadena))
        return "'" + cadena + "'"

    # poner dobles comillas en caso de que se trate de una cadena
    @staticmethod
    def comillas_d(cadena):
        if cadena is None or cadena == '':
            return '" "'
        return '"' + cadena + '"'

    # filtrar lista de tuplas por una columna indicada por indice
    @staticmethod
    def filtrar_por_valor(seq, col, valor):
        for reg in seq:
            if reg[col] == valor:
                yield reg

    @staticmethod
    def vacio(s: str) -> bool:
        """
        Retorna verdadero si la cadena es vacía o Nula
        """
        return not (s and s.strip())

    # Verdadero si la cadena no es vacía o Nula
    @staticmethod
    def no_vacio(s: str) -> bool:
        return bool(s and s.strip())

    @staticmethod
    def item_vacio(item):
        """
        Metodo aplicable a item de pyQT
        :param item:item de pyQT
        :return:verdadero si el item es nulo o contiene texto vacio, caso contrario falso
        """
        if item is None:
            return True
        if item.text().strip() == '':
            return True
        return False

    @staticmethod
    def crear_carpeta(carpeta):
        if not (os.path.exists(carpeta)):
            os.mkdir(carpeta)

    @staticmethod
    def eliminar_archivos_carpeta(carpeta):
        for archivo in os.listdir(carpeta):
            file_path = os.path.join(carpeta, archivo)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(e)

    @staticmethod
    def listar_archivos(carpeta, extension):
        """
        Devolver lista con todos los archivos de la carpeta incluidos subdirectorios que cumplan con la extension
        :param carpeta: Carpeta raiz de búsqueda
        :param extension: Extensión para filtrar los achivos
        :return: lista de achivos con path completo
        """
        raiz = pathlib.Path(carpeta)
        lista_arch = [str(f) for f in raiz.resolve().glob('**/*') if f.is_file() and f.name.endswith(extension)]
        return lista_arch

    @staticmethod
    def hora():
        """
        Retorna la fecha y hora actual en formato cadena YYYYMMDD-HHMMSS
        """
        actual = datetime.datetime.now()
        return datetime.datetime.strftime(actual, '%Y%m%d-%H%M%S')

    # rotar x,y con respecto al eje z, punto base(0,0,0)
    @staticmethod
    def rot_z(x, y, z, a):
        x_rot = x * math.cos(math.radians(a)) - y * math.sin(math.radians(a))
        y_rot = x * math.sin(math.radians(a)) + y * math.cos(math.radians(a))
        z_rot = z
        return [x_rot, y_rot, z_rot]

    # rotar y,z con respecto al eje x, punto base(0,0,0)
    @staticmethod
    def rot_x(x, y, z, a):
        x_rot = x
        y_rot = y * math.cos(math.radians(a)) - z * math.sin(math.radians(a))
        z_rot = y * math.sin(math.radians(a)) + z * math.cos(math.radians(a))
        return [x_rot, y_rot, z_rot]

    # rotar x,z con respecto al eje y, punto base(0,0,0)
    @staticmethod
    def rot_y(x, y, z, a):
        x_rot = z * math.sin(math.radians(a)) + x * math.cos(math.radians(a))
        y_rot = y
        z_rot = z * math.cos(math.radians(a)) - x * math.sin(math.radians(a))
        return [x_rot, y_rot, z_rot]

    @staticmethod
    def rot_z_orig(punto: list, origen: list, a: float) -> list:
        """
        Rotar plano x, y con respecto al eje z, con un punto base arbitrario (origen)
        :param punto: Punto a rotar en formato [x, y, z]
        :param origen: Punto base para la rotación en formato [x, y, z]
        :param a: ángulo de rotacion en notación decimal
        :return: punto rotado en formato [x, y, z]
        """
        x, y, z = punto
        ox, oy, oz = origen
        x_rot = ox + (x - ox) * math.cos(math.radians(a)) - (y - oy) * math.sin(math.radians(a))
        y_rot = oy + (x - ox) * math.sin(math.radians(a)) + (y - oy) * math.cos(math.radians(a))
        z_rot = z
        return [x_rot, y_rot, z_rot]

    # metodo para crear una expresión a partir de dos listas una de coeficientes y la segunda de nombre de variables
    @staticmethod
    def exp_formula(coeficientes, campos):
        partes = []
        i = 0
        for coef in coeficientes:
            partes.append('(')
            partes.append(str(coef))
            partes.append(" * ")
            partes.append(campos[i])
            partes.append(")")
            if i < (len(coeficientes) - 1):
                partes.append(' + ')
            i += 1
        return ''.join(partes)

    # metodo para crear una expresión para cálculo de promedio ponderado en dataframe
    @staticmethod
    def exp_prom(campo, encabezado=None, peso='tonelaje'):
        if encabezado is None:
            partes = ["g.apply(lambda x: np.ma.average(x.", campo, ", weights=x.", peso, "))"]
        else:
            partes = ["g.apply(lambda x: np.ma.average(x.", campo, ", weights=x.", peso, ")).reset_index(name='",
                      encabezado, "')"]
        return ''.join(partes)

    @staticmethod
    def generar_etiqueta_precio(metales):
        # metal: 0:NombreMetal,1:Unidad,2:TipoMetal,3:NombreCampo,4:CampoAcotado,5:CampoEnvolvente,6:CoeficienteNSR,
        #        7:CoeficienteEQ,8:UnidadReporte,9:FactConversion,10:Precio
        salida = ''
        for i, metal in enumerate(metales):
            if metal[10] is not None:  # si el metal tiene precio
                if metal[0].lower() == 'au' or metal[0].lower() == 'ag':
                    unidad = 'onza'
                else:
                    unidad = 'TM'
                parte = 'US$ ' + '{:,.2f}'.format(metal[10]) + ' por ' + unidad + ' ' + metal[0].title()
                if 0 < i < len(metales) - 1:
                    parte = ', ' + parte
                elif i == len(metales) - 1:
                    parte = ' y ' + parte + '.'
                salida += parte
        salida = 'Los precios utilizados son ' + salida
        return salida

    @staticmethod
    def nombre_archivo(ruta):
        """
        Devuelve unicamente el nombre de archivo eliminando la extensión
        :param ruta: ruta completa del archivo incluida la extensión
        :return: nombre de archivo sin extensión
        """
        return os.path.splitext(os.path.split(ruta)[1])[0]

    @staticmethod
    def insert_row(row_number, df, row_value):
        """
        Insertar una fila en el dataframe df
        :param row_number: indice en el que se desea insertar
        :param df: dataframe en el que se insertará
        :param row_value: fila que se insertará
        :return: dataframe con la fila insertada.
        """
        # Starting value of upper half
        start_upper = 0
        # End value of upper half
        end_upper = row_number
        # Start value of lower half
        start_lower = row_number
        # End value of lower half
        end_lower = df.shape[0]
        # Create a list of upper_half index
        upper_half = [*range(start_upper, end_upper, 1)]
        # Create a list of lower_half index
        lower_half = [*range(start_lower, end_lower, 1)]
        # Increment the value of lower half by 1
        lower_half = [x.__add__(1) for x in lower_half]
        # Combine the two lists
        index_ = upper_half + lower_half
        # Update the index of the dataframe
        df.index = index_
        # Insert a row at the end
        df.loc[row_number] = row_value
        # Sort the index labels
        df = df.sort_index()
        # return the dataframe
        return df

    @staticmethod
    def preparar_expresion(expresion, campos):
        """
        método para reemplazar los nombres de los campos correspondientes a metales por su nombre completo incluito el
        del DataFrame para cálculo de expresiones dinámicas, se utiliza un nombre genérico para el dataframe: f
        :param expresion: Expresión original
        :param campos: Lista de campos de donde se extraerán los nombres originales de los campos
        :return: Expresión con los nombres de campos cambiados, se agrega el prefijo 'f.'
        """
        for campo in campos:
            expresion = re.sub(r'\b' + campo.lower() + r'\b', 'f.' + campo.lower(), expresion)
        return expresion

    @staticmethod
    def transponer(lista):
        """
        transponer una lista de listas, de filas a columnas y viceversa
        :param lista: lista de listas
        :return: lista de listas transpuerta
        """
        # si solo se requiere una lista de tuplas como salida se puede usar: list(zip(*lista))
        return list(map(list, zip(*lista)))

    @staticmethod
    def red_corporativa():
        intentos = 1

        servidor = "L-SRVEXPLOBD"
        for i in range(intentos):
            try:
                resultado = subprocess.run(["ping", "-n", "1", servidor], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                if resultado.returncode == 0:
                    return True
            except Exception as e:
                pass
            time.sleep(1)  # Espera 1 segundo antes de reintentar

        servidor = "CT-SRVBD1"
        for i in range(intentos):
            try:
                resultado = subprocess.run(["ping", "-n", "1", servidor], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                if resultado.returncode == 0:
                    return True
            except Exception as e:
                pass
            time.sleep(1)  # Espera 1 segundo antes de reintentar

        servidor = "EBAPDBPR01"
        for i in range(intentos):
            try:
                resultado = subprocess.run(["ping", "-n", "1", servidor], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
                if resultado.returncode == 0:
                    return True
            except Exception as e:
                pass
            time.sleep(1)  # Espera 1 segundo antes de reintentar

        return False

    @staticmethod
    def red_corporativa_v2():
        intentos = 1
        servidores = ["L-SRVEXPLOBD", "CT-SRVBD1", "EBAPDBPR01", "U-SRVBD1", "O-SRVBD1", "TM-SRVBD1", "J-SRVBD1",
                      "L-SRVBD"]
        for i in range(intentos):
            for servidor in servidores:
                try:
                    resultado = subprocess.run(["ping", "-n", "1", servidor], stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)
                    if resultado.returncode == 0:
                        return True
                except Exception as e:
                    pass
            # Si no se puede conectar a ninguno de los servidores, espera 1 segundo antes de reintentar
            time.sleep(1)  # Espera 1 segundo antes de reintentar
        return False

    @staticmethod
    def determinar_alta_baja(restriccion, indicadores):
        """
        Determina si una expresión de restricción corresponde a alta(hg) o baja(lg) ley
        :param restriccion: expresión de restricción
        :param indicadores: lista de las variables de indicador utilizadas
        :return: 'lg' para baja ley, 'hg' para alta ley
        """
        if Util.vacio(restriccion):
            return 'lg'  # si no existe restriccion asumimos que es de baja

        indicadores = [i for i in indicadores if i]  # eliminar todos los valores None de la lista de indicadores

        restriccion = restriccion.upper()  # convertimos a mayusculas para evitar problemas de busquedas
        partes = []
        if ',' in restriccion:
            partes = restriccion.split(',')
        else:
            partes.append(restriccion)

        for parte in partes:  # para cada una de las partes de la expresion de restriccion
            for ind in indicadores:  # para cada variable de indicador
                if ind.upper() in parte and ' ' in parte:  # si la variable de indicador existe  y existe un espacio
                    datos_ind = parte.split(' ')
                    if datos_ind[1] == '0':
                        return 'lg'
                    elif datos_ind[1] == '1':
                        return 'hg'
        # por defecto retornamos 'lg'
        return 'lg'

    @staticmethod
    def calcular_nsr(df, metales, exp_metales, exp_nsr, cutoff):
        """
        Calcular Nsr mediante expresiones dinamicas
        :param df: DataFrame de entrada donde se realizará el cálculo
        :param metales: Lista de metales de la unidad
        :param exp_metales: lista de expresiones de recuperación de cada metal
        :param exp_nsr: expresión dinámica para cálculo de nsr
        :param cutoff: cutoff al que se calculará el reporte
        :return: Dataframe con Tonelaje nsr, equiv y onzas equivalentes calculados.
        """
        # metales: 0:NombreMetal, 1:TipoMetal, 2:NombreCampo, 3:CampoAcotado, 4:CampoEnvolvente, 5:ExpRecuperable
        df['tonelaje'] = df['volume'] * df['densidad']  # calcular tonelaje
        if df.shape[0] > 0:  # Calcular solo si tiene datos
            # calcular los porcentajes de recuperación de cada metal y agregarlos al df
            for i, campo in enumerate(metales):
                if Util.no_vacio(exp_metales[i]):
                    df['pct_rec_' + campo[0].lower()] = df[campo[2].lower()]
                    exp = "df['pct_rec_" + campo[0].lower() + "'].apply(lambda " + campo[2].lower() + ": " + \
                          exp_metales[i] + ")"
                    df['pct_rec_' + campo[0].lower()] = eval(exp)
                else:  # si no tienes datos solo agregar los encabezados
                    df['pct_rec_' + campo[0].lower()] = 0

            # calcular NSR según las expresiones ingresadas en el archivo de parámetros.
            df['nsr'] = df.eval(exp_nsr)
        else:
            df['nsr'] = 0
        df = df.replace([np.inf, -np.inf], np.nan)  # para tratar valores infinitos
        df = df.fillna(0)  # reemplazar con 0 los valores NaN generados por división por cero df detallado

        # Filtra por Cutoff
        df = df.drop(df[df['nsr'] < cutoff].index)  # eliminar todos los bloques que no superen el cutoff

        return df

    @staticmethod
    def crear_boundary(nro, prioridad, triangulacion, valor, variable):
        variable = variable.lower()  # los campos del modelo de bloques estan en minusculas
        b = ['*']
        b.append('BEGIN$DEF boundary_' + str(nro))
        b.append(" inversion='None'")
        b.append(' priority=' + str(prioridad))
        b.append(" projection='Z'")
        b.append(" selection_file=' '")
        b.append(' triangulation=' + Util.comillas(triangulacion))
        b.append(' value=' + "'" + str(valor) + "'")
        b.append(' variable=' + Util.comillas(variable))
        b.append('END$DEF boundary_' + str(nro))
        return b

    @staticmethod
    def limpiar_carpeta(ruta_carpeta):
        # Si la carpeta existe, elimínala con su contenido
        if os.path.exists(ruta_carpeta):
            shutil.rmtree(ruta_carpeta)
        # Crea la carpeta nuevamente
        os.makedirs(ruta_carpeta)

    @staticmethod
    def obtener_encabezado_csv(archivo, separador=','):
        # leer una fila del archivo csv en un dataframe de pandas
        df = pd.read_csv(archivo, encoding='utf-8', sep=separador, nrows=1)
        # convertir el encabezado en una lista
        encabezado = df.columns.tolist()
        return encabezado

    @staticmethod
    def eliminar_tildes(texto):
        """Elimina las tildes de un texto usando str.translate()."""
        replacements = {
            'á': 'a', 'à': 'a', 'â': 'a', 'ä': 'a',
            'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
            'í': 'i', 'ì': 'i', 'î': 'i', 'ï': 'i',
            'ó': 'o', 'ò': 'o', 'ô': 'o', 'ö': 'o',
            'ú': 'u', 'ù': 'u', 'û': 'u',
            'Á': 'A', 'À': 'A', 'Â': 'A', 'Ä': 'A',
            'É': 'E', 'È': 'E', 'Ê': 'E', 'Ë': 'E',
            'Í': 'I', 'Ì': 'I', 'Î': 'I', 'Ï': 'I',
            'Ó': 'O', 'Ò': 'O', 'Ô': 'O', 'Ö': 'O',
            'Ú': 'U', 'Ù': 'U', 'Û': 'U'
        }

        # Usar traducción de caracteres para reemplazo masivo
        # Más eficiente que múltiples .replace() para textos grandes
        translation_table = str.maketrans(replacements)
        return texto.translate(translation_table)

    @staticmethod
    def ordenar_dic_valor_clave(diccionario):
        """
        Ordenar un diccionario por el valor y luego la clave
        :param diccionario: diccionario a ordenar
        :return: diccionario ordenado por valor y clave
        """
        return dict(sorted(diccionario.items(), key=lambda item: (item[1], item[0])))

    @staticmethod
    def extensiones_a_minusculas(directorio, incluir_subcarpetas=False):
        for raiz, _, archivos in os.walk(directorio):
            for archivo in archivos:
                nombre, extension = os.path.splitext(archivo)
                if extension != extension.lower():
                    origen = os.path.join(raiz, archivo)
                    destino = os.path.join(raiz, nombre + extension.lower())
                    os.rename(origen, destino)
            if not incluir_subcarpetas:
                break  # No desciende a subcarpetas si no se requiere

    @staticmethod
    def dibujar_contorno(image_rgb, mask, colores, grosor=10):
        """
        Dibuja los contornos de la máscara en la imagen original.
        :param image_rgb: Imagen original en la que se dibujarán los contornos en formato RGB
        :param mask: Máscara de segmentación generada por el modelo
        :param colores: Lista de colores por cada categoría en el formato [(categoria, color), ...], color en RGB
        :param grosor: Grosor del contorno a dibujar
        :return: Imagen con los contornos dibujados
        """
        # Recorrer lista de colores
        for categoria, color in colores:
            # Extraer los bordes de la categoría
            contornos, _ = cv2.findContours((mask == categoria).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            # Dibujar los contornos sobre la imagen
            cv2.drawContours(image_rgb, contornos, -1, color, grosor)

        return image_rgb

    @staticmethod
    def resize_and_pad(image_path, output_path, target_size):
        """
        Redimensiona una imagen manteniendo su proporción y aplica padding.
        Args:
            image_path: Ruta a la imagen.
            output_path: Ruta donde se guardará la imagen redimensionada y con padding.
            target_size: Tupla (ancho, alto) del tamaño objetivo.
        """

        img = cv2.imread(image_path)
        height, width, _ = img.shape

        # Calcular la nueva altura y ancho manteniendo la proporción
        scale = min(target_size[0] / width, target_size[1] / height)
        new_width = int(width * scale)
        new_height = int(height * scale)

        # Redimensionar la imagen
        resized_img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)

        # Calcular el padding
        top, bottom, left, right = 0, target_size[1] - new_height, 0, target_size[0] - new_width

        # Aplicar padding
        padded_img = cv2.copyMakeBorder(resized_img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=[0, 0, 0])

        cv2.imwrite(output_path, padded_img)

    @staticmethod
    def remove_padding_and_resize(mask_path, output_path, original_size, target_size):
        """
        Elimina el padding de una máscara predicha y la redimensiona a su tamaño original.

        Args:
            mask_path: Ruta a la máscara predicha con padding.
            output_path: Ruta donde se guardará la máscara redimensionada y sin padding.
            original_size: Tupla (alto, ancho) del tamaño original de la imagen antes de redimensionarla.
            target_size: Tupla (alto, ancho) del tamaño con padding (por ejemplo, 512x512).
        """
        # Cargar la máscara con padding
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        height, width = mask.shape

        # Calcular la nueva altura y ancho manteniendo la proporción
        scale = min(target_size[0] / original_size[0], target_size[1] / original_size[1])
        new_height = int(original_size[0] * scale)
        new_width = int(original_size[1] * scale)

        # Calcular el padding aplicado previamente
        top, bottom = 0, target_size[1] - new_height
        left, right = 0, target_size[0] - new_width

        # Eliminar el padding: recorta la máscara
        cropped_mask = mask[top:height - bottom, left:width - right]

        # Redimensionar la máscara al tamaño original de la imagen
        resized_mask = cv2.resize(cropped_mask, (original_size[1], original_size[0]), interpolation=cv2.INTER_NEAREST)

        # Guardar la máscara redimensionada
        cv2.imwrite(output_path, resized_mask)

    @staticmethod
    def remove_padding_and_resize_center(mask_path, output_path, original_size, target_size):
        """
        Elimina el padding CENTRADO (estilo Albumentations) y redimensiona a tamaño original.

        Args:
            mask_path (str): Ruta a la máscara predicha (ej. 512x512).
            output_path (str): Ruta de guardado.
            original_size (tuple): Tupla (alto, ancho) de la imagen original (ej. 3000, 5000).
            target_size (tuple): Tupla (alto, ancho) del tamaño de entrada del modelo (ej. 512, 512).
        """
        # Cargar la máscara con padding (ej. 512x512)
        mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)

        orig_h, orig_w = original_size
        target_h, target_w = target_size

        # 1. Calcular las dimensiones de la imagen redimensionada ANTES del padding
        # (Este es el mismo cálculo que hace A.LongestMaxSize)
        scale = min(target_h / orig_h, target_w / orig_w)
        new_h = int(orig_h * scale)
        new_w = int(orig_w * scale)

        # 2. Calcular el padding que Albumentations AÑADIÓ
        # A.PadIfNeeded centra el contenido, por lo que el padding se reparte.
        pad_h = target_h - new_h
        pad_w = target_w - new_w

        # // 2 da la parte entera (ej. 5 // 2 = 2)
        top = pad_h // 2
        bottom = pad_h - top  # (Cubre el caso de padding impar, ej. 5 - 2 = 3)

        left = pad_w // 2
        right = pad_w - left

        # 3. Recortar la máscara para eliminar el padding
        # (Índices: [fila_inicio : fila_fin, col_inicio : col_fin])
        cropped_mask = mask[top: target_h - bottom, left: target_w - right]

        # 4. Redimensionar la máscara recortada (que ahora tiene el aspect ratio correcto)
        # al tamaño original exacto.
        # cv2.resize espera (ancho, alto)
        resized_mask = cv2.resize(
            cropped_mask,
            (orig_w, orig_h),
            interpolation=cv2.INTER_NEAREST
        )

        # 5. Guardar la máscara final
        cv2.imwrite(output_path, resized_mask)

    @staticmethod
    def eliminar_regiones_pequenas_multiclase(mask, area_minima, num_clases=4):
        """
        Elimina las regiones pequeñas para cada clase en una máscara segmentada multiclasificada.
        :param mask: máscara segmentada (imagen con múltiples clases).
        :param area_minima: área mínima que debe tener una región para ser conservada.
        :param num_clases: número de clases en la segmentación (incluyendo el fondo).
        :return: máscara con regiones pequeñas eliminadas.
        """
        # Crear una máscara vacía para el resultado
        mask_limpia = np.zeros_like(mask)

        # Iterar sobre cada clase, ignorando la clase 0 (Fondo)
        for clase in range(1, num_clases):
            # Crear una máscara binaria para la clase actual
            mask_clase = (mask == clase).astype(np.uint8)

            # Encontrar todos los componentes conectados en la clase actual
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask_clase, connectivity=8)

            # Iterar sobre cada componente conectado de esta clase
            for label in range(1, num_labels):  # Ignoramos el fondo (label 0)
                area = stats[label, cv2.CC_STAT_AREA]
                if area >= area_minima:
                    # Conservar solo las regiones con área mayor o igual al umbral
                    mask_limpia[labels == label] = clase

        return mask_limpia

    @staticmethod
    def operaciones_morfologicas_multiclase(mask, kernel_size=3, num_clases=4, operacion='apertura'):
        """
        Aplica operaciones morfológicas (apertura o cierre) a cada clase en una máscara segmentada multiclasificada.
        :param mask: máscara segmentada (imagen con múltiples clases).
        :param kernel_size: tamaño del kernel estructurante para las operaciones morfológicas.
        :param num_clases: número de clases en la segmentación (incluyendo el fondo).
        :param operacion: tipo de operación morfológica ('apertura' o 'cierre').
        :return: máscara con las operaciones morfológicas aplicadas.
        """
        # Crear una máscara vacía para el resultado
        mask_morfologica = np.zeros_like(mask)

        # Definir el kernel estructurante
        kernel = np.ones((kernel_size, kernel_size), np.uint8)

        # Iterar sobre cada clase, ignorando la clase 0 (Fondo)
        for clase in range(1, num_clases):
            # Crear una máscara binaria para la clase actual
            mask_clase = (mask == clase).astype(np.uint8)

            # Aplicar la operación morfológica correspondiente
            if operacion == 'apertura':
                resultado = cv2.morphologyEx(mask_clase, cv2.MORPH_OPEN, kernel)
            elif operacion == 'cierre':
                resultado = cv2.morphologyEx(mask_clase, cv2.MORPH_CLOSE, kernel)
            else:
                raise ValueError("Operación morfológica no reconocida. Usa 'apertura' o 'cierre'.")

            # Añadir el resultado a la máscara final, conservando la clase original
            mask_morfologica[resultado == 1] = clase

        return mask_morfologica

    @staticmethod
    def post_eliminar_areas_pequenias(df, dir_mascaras, area_minima, num_clases):
        print(f"Limpiando áreas pequeñas (< {area_minima} px)...")
        for i, row in df.iterrows():
            path_msk = os.path.join(dir_mascaras, row['msks'])
            if not os.path.exists(path_msk): continue

            msk = cv2.imread(path_msk, cv2.IMREAD_GRAYSCALE)
            # Asumimos que Util maneja 3 clases correctamente
            msk = Util.eliminar_regiones_pequenas_multiclase(msk, area_minima=area_minima, num_clases=num_clases)
            cv2.imwrite(path_msk, msk)

    @staticmethod
    def post_apertura_cierre(df, dir_mascaras, kernel_size, operacion, num_clases):
        print(f"Aplicando morfología ({operacion}, k={kernel_size})...")
        for i, row in df.iterrows():
            path_msk = os.path.join(dir_mascaras, row['msks'])
            if not os.path.exists(path_msk): continue

            msk = cv2.imread(path_msk, cv2.IMREAD_GRAYSCALE)
            msk = Util.operaciones_morfologicas_multiclase(msk, kernel_size=kernel_size, num_clases=num_clases,
                                                           operacion=operacion)
            cv2.imwrite(path_msk, msk)

    @staticmethod
    def guardar_excel(padre, grilla):
        # Abrir diálogo para guardar archivo
        nombre_archivo, _ = QFileDialog.getSaveFileName(padre, 'Guardar escenarios en Excel',
                                                        filter='Archivos Excel (*.xlsx)', dir=Const.LAST_DIR)

        if nombre_archivo:
            # Asegurarse de que el nombre del archivo termine con .xlsx
            if not nombre_archivo.endswith('.xlsx'):
                nombre_archivo += '.xlsx'

            # Obtener los encabezados del QTableWidget
            column_count = grilla.columnCount()
            encabezados = [grilla.horizontalHeaderItem(i).text() for i in range(column_count)]

            # Recorrer el QTableWidget y extraer los datos
            datos = []
            for row in range(grilla.rowCount()):
                fila_datos = []
                for col in range(column_count):
                    item = grilla.item(row, col)
                    # Añadir el texto del item si existe, sino una cadena vacía
                    fila_datos.append(item.text() if item else '')
                # Guardar la fila solo si no está completamente vacía
                if any(cell.strip() for cell in fila_datos):
                    datos.append(fila_datos)

            # Crear un DataFrame de pandas y guardarlo en Excel
            df = pd.DataFrame(datos, columns=encabezados)

            df.to_excel(nombre_archivo, index=False, engine='openpyxl')

            Const.LAST_DIR = os.path.dirname(nombre_archivo)
            QMessageBox.information(padre, 'Éxito', f'Los escenarios se han guardado en:\n{nombre_archivo}')

    @staticmethod
    def cargar_excel(padre, grilla):
        # Abrir diálogo para seleccionar archivo
        nombre_archivo, _ = QFileDialog.getOpenFileName(padre, 'Cargar escenarios desde Excel',
                                                        filter='Archivos Excel (*.xlsx *.xls)', dir=Const.LAST_DIR)

        if nombre_archivo:
            # Leer el archivo Excel
            df = pd.read_excel(nombre_archivo, engine='openpyxl')

            # Validar encabezados
            encabezados_tabla = [grilla.horizontalHeaderItem(i).text() for i in range(grilla.columnCount())]
            encabezados_excel = list(df.columns)

            if encabezados_tabla != encabezados_excel:
                QMessageBox.critical(padre, 'Error de formato',
                                     'Los encabezados del archivo Excel no coinciden con los de la tabla de escenarios.')
                return

            # Reemplazar NaN por cadenas vacías para una comparación consistente
            df_sin_nan = df.fillna('')
            # Filtrar filas que no estén completamente vacías
            df_filtrado = df_sin_nan[df_sin_nan.astype(str).apply(lambda x: x.str.strip()).any(axis=1)].reset_index(drop=True)

            # limitar el número de filas del DataFrame al número de filas de la grilla
            if df_filtrado.shape[0] > grilla.rowCount():
                df_filtrado = df_filtrado.head(grilla.rowCount())

            # Limpiar la tabla y establecer el número de filas
            filas = grilla.rowCount()
            grilla.setRowCount(0)
            grilla.setRowCount(filas)

            # Poblar la tabla con los datos del DataFrame
            for row_idx, row_data in df_filtrado.iterrows():
                for col_idx, cell_data in enumerate(row_data):
                    # Convertir a string para evitar problemas de tipo
                    item_text = str(cell_data) if not pd.isna(cell_data) else ''
                    grilla.setItem(row_idx, col_idx, QtWidgets.QTableWidgetItem(item_text))

            Const.LAST_DIR = os.path.dirname(nombre_archivo)
            QMessageBox.information(padre, 'Éxito', 'Los escenarios se han cargado correctamente.')

    @staticmethod
    def leer_csv_seguro(ruta_archivo, nrows=None, low_memory=True):
        """
        Intenta leer un CSV con UTF-8 por defecto.
        Si encuentra un error de codificación, hace fallback a latin1.
        """
        try:
            # Primer intento: El estándar global (UTF-8)
            return pd.read_csv(ruta_archivo, nrows=nrows, encoding='utf-8', low_memory=low_memory)
        except UnicodeDecodeError:
            # Segundo intento: Codificación típica de Windows/Excel en español
            return pd.read_csv(ruta_archivo, nrows=nrows, encoding='latin1', low_memory=low_memory)