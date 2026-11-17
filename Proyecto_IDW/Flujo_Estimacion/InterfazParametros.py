"""
Nombre : InterfazParametros.py
Propósito: Interfaz (PySide6) para ingresar todos los parámetros del flujo de estimación y guardarlos en los
           JSON de la carpeta Configuraciones, que luego usa EjecutarFlujo.py. Una pestaña por configuración:
             Rutas del flujo (conf_proyecto.json) · Importar BD · Codificación · Acotamiento · Compositación ·
             Modelo de bloques · Estimación IDW · Valorización · Categorización · Reporte,
           más una pestaña para ejecutar el flujo (todo o pasos sueltos) viendo el avance.
           Al guardar se conservan las claves del JSON que la interfaz no edita (p. ej. __version__).
           v3: las tablas de Codificación, Acotamiento, Modelo de bloques, Estimación IDW, Valorización y
           Reporte se importan/exportan a CSV (carpeta 01_Data\\04_Parametros_Entrada); Bearing/Plunge/Dip de
           las pasadas aceptan un número o una variable del modelo de bloques; el modelo de bloques puede ser
           uno existente (no se crea) y el reporte puede limitarse a los bloques dentro de un sólido.
Uso      : python InterfazParametros.py [carpeta_configuraciones]
"""

import io
import os
import re
import sys
import csv
import json
import copy
import unicodedata

from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QGroupBox, QLineEdit, QPushButton, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem, QFileDialog,
    QMessageBox, QPlainTextEdit, QLabel, QScrollArea, QHeaderView, QAbstractItemView, QRadioButton, QSizePolicy)

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
CONFIG_DIR_DEFECTO = os.path.normpath(os.path.join(AQUI, '..', 'Configuraciones'))

import EjecutarFlujo  # noqa: E402  (solo la lista de pasos; no ejecuta nada al importarse)

ROLES = ['Incluir', 'Omitir', 'Sondaje', 'Este', 'Norte', 'Cota', 'Profundidad', 'Azimut', 'Dip', 'Desde', 'Hasta']
ROLES_COMPOSITO = ['Incluir', 'Omitir', 'Sondaje', 'Desde', 'Hasta']
TIPO_COMPOSITO = 'Compósito'
TIPOS_DATO = ['', 'Texto', 'Numérico']
FILTRO_CSV = 'CSV (*.csv);;Todos los archivos (*)'
FILTRO_DB = 'Base de datos (*.db *.ydb);;Todos los archivos (*)'
FILTRO_OBJ = 'Triangulación (*.obj);;Todos los archivos (*)'


# ==================================================================================================
# Utilitarios de JSON
# ==================================================================================================
def get_path(cfg, ruta, defecto=None):
    """Valor anidado por ruta con puntos ('esquema.origen_x')."""
    actual = cfg
    for clave in ruta.split('.'):
        if not isinstance(actual, dict) or clave not in actual:
            return defecto
        actual = actual[clave]
    return actual


def set_path(cfg, ruta, valor):
    partes = ruta.split('.')
    actual = cfg
    for clave in partes[:-1]:
        if not isinstance(actual.get(clave), dict):
            actual[clave] = {}
        actual = actual[clave]
    actual[partes[-1]] = valor


def a_texto_numero(valor):
    """Número -> texto sin ceros superfluos (2.7 -> '2.7', 5.0 -> '5')."""
    try:
        return '%.15g' % float(valor)
    except (TypeError, ValueError):
        return '' if valor is None else str(valor)


def es_numero(texto):
    try:
        float(str(texto).strip().replace(',', '.'))
        return True
    except ValueError:
        return False


# ==================================================================================================
# CSV de las tablas
# ==================================================================================================
VERDADEROS = {'1', 'true', 'verdadero', 'si', 'sí', 'yes', 'x'}


def _norm_titulo(texto):
    """Título de columna comparable: sin tildes, en minúsculas y con espacios simples."""
    sin_tildes = unicodedata.normalize('NFKD', str(texto)).encode('ascii', 'ignore').decode()
    return ' '.join(sin_tildes.lower().split())


def leer_csv(ruta):
    """(cabecera, filas) de un CSV. Detecta la codificación (UTF-8 con/sin BOM o ANSI) y el separador
    (coma, punto y coma o tabulación, el que más aparezca en la primera línea); ignora filas vacías."""
    for codificacion in ('utf-8-sig', 'latin1'):
        try:
            with open(ruta, 'r', encoding=codificacion, newline='') as f:
                texto = f.read()
            break
        except UnicodeDecodeError:
            continue
    lineas = texto.splitlines()
    if not lineas:
        return [], []
    separador = max((',', ';', '\t'), key=lineas[0].count)
    filas = [r for r in csv.reader(io.StringIO(texto), delimiter=separador) if any(c.strip() for c in r)]
    return ([c.strip() for c in filas[0]], filas[1:]) if filas else ([], [])


# ==================================================================================================
# Widgets básicos
# ==================================================================================================
class RutaEdit(QWidget):
    """Campo de texto con botón «…» para elegir archivo (abrir/guardar) o carpeta."""

    def __init__(self, modo, filtro=''):
        super().__init__()
        self.modo, self.filtro = modo, filtro
        self.linea = QLineEdit()
        boton = QPushButton('…')
        boton.setFixedWidth(30)
        boton.clicked.connect(self._elegir)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.linea)
        lay.addWidget(boton)

    def _elegir(self):
        actual = self.linea.text().strip()
        base = actual if os.path.exists(actual) else (os.path.dirname(actual) if actual else '')
        if self.modo == 'carpeta':
            ruta = QFileDialog.getExistingDirectory(self, 'Seleccionar carpeta', base)
        elif self.modo == 'archivo':
            ruta, _ = QFileDialog.getOpenFileName(self, 'Seleccionar archivo', base, self.filtro)
        else:
            ruta, _ = QFileDialog.getSaveFileName(self, 'Guardar como', actual or base, self.filtro)
        if ruta:
            self.linea.setText(os.path.normpath(ruta))

    def text(self):
        return self.linea.text()

    def setText(self, texto):
        self.linea.setText(texto)


class Campo:
    """Un campo de la interfaz ligado a un valor de JSON. Tipos: txt, float, int, lista (separada por
    comas), bool, combo (editable), archivo / guardar / carpeta (con botón de exploración)."""

    def __init__(self, tipo, opciones=None, filtro='', ayuda='', defecto=None):
        self.tipo = tipo
        self.defecto = defecto   # valor que se muestra si el JSON no trae la clave
        if tipo in ('archivo', 'guardar', 'carpeta'):
            self.widget = RutaEdit(tipo, filtro)
        elif tipo == 'bool':
            self.widget = QCheckBox()
        elif tipo == 'combo':
            self.widget = QComboBox()
            self.widget.setEditable(True)
            self.widget.addItems(opciones or [])
        else:
            self.widget = QLineEdit()
        if ayuda:
            self.widget.setToolTip(ayuda)

    def leer(self, nombre=''):
        t = self.tipo
        if t == 'bool':
            return self.widget.isChecked()
        if t == 'combo':
            return self.widget.currentText().strip()
        texto = self.widget.text().strip()
        if t in ('float', 'int'):
            try:
                numero = float(texto.replace(',', '.'))
            except ValueError:
                raise ValueError(f"'{nombre}': «{texto}» no es un número.") from None
            return int(round(numero)) if t == 'int' else numero
        if t == 'lista':
            return [p.strip() for p in texto.split(',') if p.strip()]
        return texto

    def escribir(self, valor):
        t = self.tipo
        if t == 'bool':
            self.widget.setChecked(bool(valor))
        elif t == 'combo':
            self.widget.setCurrentText('' if valor is None else str(valor))
        elif t in ('float', 'int'):
            self.widget.setText(a_texto_numero(valor))
        elif t == 'lista':
            self.widget.setText(', '.join(str(x) for x in (valor or [])))
        else:
            self.widget.setText('' if valor is None else str(valor))


class TablaEditable(QWidget):
    """Tabla con botones Agregar / Duplicar / Quitar. `columnas`: lista de (título, tipo, opciones, ancho);
    tipo = 'txt' | 'combo' | 'bool'. Con `boton_archivo` = índice de columna aparece «Agregar archivo…».
    Con `csv` = nombre de archivo aparecen «Importar CSV…» y «Exportar CSV…» (la importación casa las
    columnas por su título; la exportación propone la carpeta `CARPETA_CSV` con ese nombre).
    `tooltips`: {título de columna: ayuda} para el encabezado."""

    CARPETA_CSV = ''   # carpeta por defecto de los CSV de parámetros (la fija la Ventana)

    def __init__(self, columnas, altura=150, boton_archivo=None, filtro_archivo='', csv=None, tooltips=None):
        super().__init__()
        self.columnas = columnas
        self.boton_archivo, self.filtro_archivo = boton_archivo, filtro_archivo
        self.csv = csv
        self.tabla = QTableWidget(0, len(columnas))
        self.tabla.setHorizontalHeaderLabels([c[0] for c in columnas])
        for i, c in enumerate(columnas):
            ayuda = (tooltips or {}).get(c[0])
            if ayuda:
                self.tabla.horizontalHeaderItem(i).setToolTip(ayuda)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setMinimumHeight(altura)
        cab = self.tabla.horizontalHeader()
        cab.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        cab.setStretchLastSection(True)
        for i, c in enumerate(columnas):
            self.tabla.setColumnWidth(i, c[3] if len(c) > 3 and c[3] else 110)

        botones = QHBoxLayout()
        for texto, accion in (('Agregar', lambda: self.agregar()), ('Duplicar', self.duplicar),
                              ('Quitar', self.quitar)):
            b = QPushButton(texto)
            b.clicked.connect(accion)
            botones.addWidget(b)
        if boton_archivo is not None:
            b = QPushButton('Agregar archivo…')
            b.clicked.connect(self._agregar_archivo)
            botones.addWidget(b)
        botones.addStretch()
        if csv:
            for texto, accion, ayuda in (
                    ('Importar CSV…', self._importar_csv, 'Carga las filas desde un CSV (columnas por título).'),
                    ('Exportar CSV…', self._exportar_csv, 'Guarda la tabla en un CSV para editarla en Excel.')):
                b = QPushButton(texto)
                b.setToolTip(ayuda)
                b.clicked.connect(accion)
                botones.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabla)
        lay.addLayout(botones)

    def agregar(self, valores=None):
        fila = self.tabla.rowCount()
        self.tabla.insertRow(fila)
        for j, col in enumerate(self.columnas):
            tipo, opciones = col[1], col[2] if len(col) > 2 else None
            valor = valores[j] if valores is not None and j < len(valores) else ('' if tipo != 'bool' else False)
            if tipo == 'combo':
                combo = QComboBox()
                combo.setEditable(True)
                combo.addItems(opciones or [])
                combo.setCurrentText(str(valor))
                self.tabla.setCellWidget(fila, j, combo)
            elif tipo == 'bool':
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Checked if valor else Qt.CheckState.Unchecked)
                self.tabla.setItem(fila, j, item)
            else:
                self.tabla.setItem(fila, j, QTableWidgetItem('' if valor is None else str(valor)))
        return fila

    def _fila_actual(self):
        filas = sorted({i.row() for i in self.tabla.selectedIndexes()})
        return filas if filas else ([self.tabla.currentRow()] if self.tabla.currentRow() >= 0 else [])

    def duplicar(self):
        todas = self.filas()
        for f in self._fila_actual():
            self.agregar(todas[f])

    def quitar(self):
        for f in reversed(self._fila_actual()):
            self.tabla.removeRow(f)

    def _agregar_archivo(self):
        rutas, _ = QFileDialog.getOpenFileNames(self, 'Seleccionar archivo(s)', '', self.filtro_archivo)
        for r in rutas:
            valores = [''] * len(self.columnas)
            valores[self.boton_archivo] = os.path.normpath(r)
            self.agregar(valores)

    # -- CSV ----------------------------------------------------------------------------------------
    def ruta_csv(self, carpeta=None):
        return os.path.join(carpeta if carpeta is not None else self.CARPETA_CSV, self.csv)

    def exportar_a(self, ruta):
        """Escribe la tabla en `ruta` (CSV UTF-8 con BOM, coma; se abre bien en Excel)."""
        carpeta = os.path.dirname(ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        with open(ruta, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow([c[0] for c in self.columnas])
            for fila in self.filas():
                w.writerow([('TRUE' if v else 'FALSE') if isinstance(v, bool) else v for v in fila])
        return ruta

    def importar_de(self, ruta, reemplazar=True):
        """Carga las filas de un CSV casando las columnas por su título (sin distinguir mayúsculas ni
        tildes). Las columnas de la tabla que el CSV no trae quedan vacías; las del CSV que no existen
        en la tabla se ignoran. Devuelve (filas leídas, columnas faltantes, columnas ignoradas)."""
        cabecera, filas = leer_csv(ruta)
        if not cabecera:
            raise ValueError('El CSV está vacío.')
        por_titulo = {_norm_titulo(c[0]): j for j, c in enumerate(self.columnas)}
        origen, ignoradas = {}, []
        for k, nombre in enumerate(cabecera):
            j = por_titulo.get(_norm_titulo(nombre))
            if j is None or j in origen:
                ignoradas.append(nombre)
            else:
                origen[j] = k
        if not origen:
            raise ValueError('Ninguna columna del CSV coincide con las de la tabla.\n'
                             f'Se esperan: {", ".join(c[0] for c in self.columnas)}.')
        nuevas = []
        for r in filas:
            fila = []
            for j, col in enumerate(self.columnas):
                valor = r[origen[j]].strip() if j in origen and origen[j] < len(r) else ''
                fila.append(valor.lower() in VERDADEROS if col[1] == 'bool' else valor)
            nuevas.append(fila)
        if reemplazar:
            self.tabla.setRowCount(0)
        for fila in nuevas:
            self.agregar(fila)
        faltantes = [self.columnas[j][0] for j in range(len(self.columnas)) if j not in origen]
        return len(nuevas), faltantes, ignoradas

    def _importar_csv(self):
        ruta, _ = QFileDialog.getOpenFileName(self, 'Importar tabla desde CSV', self.ruta_csv(), FILTRO_CSV)
        if not ruta:
            return
        reemplazar = True
        if self.tabla.rowCount():
            r = QMessageBox.question(
                self, 'Importar CSV', 'La tabla ya tiene filas.\n\nSí = reemplazarlas · No = agregar al final',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            reemplazar = r == QMessageBox.StandardButton.Yes
        try:
            n, faltantes, ignoradas = self.importar_de(ruta, reemplazar)
        except Exception as ex:   # noqa: BLE001
            QMessageBox.warning(self, 'Importar CSV', f'No se pudo importar:\n{ex}')
            return
        aviso = [f'{n} fila(s) importadas de {os.path.basename(ruta)}.']
        if faltantes:
            aviso.append('Columnas que el CSV no trae (quedan vacías): ' + ', '.join(faltantes))
        if ignoradas:
            aviso.append('Columnas del CSV ignoradas: ' + ', '.join(ignoradas))
        QMessageBox.information(self, 'Importar CSV', '\n\n'.join(aviso))

    def _exportar_csv(self):
        ruta, _ = QFileDialog.getSaveFileName(self, 'Exportar tabla a CSV', self.ruta_csv(), FILTRO_CSV)
        if not ruta:
            return
        try:
            self.exportar_a(ruta)
        except OSError as ex:
            QMessageBox.warning(self, 'Exportar CSV', f'No se pudo escribir el archivo (¿está abierto en '
                                                      f'Excel?):\n{ex}')
            return
        QMessageBox.information(self, 'Exportar CSV', f'Tabla guardada en:\n{ruta}')

    def set_filas(self, filas):
        self.tabla.setRowCount(0)
        for f in filas:
            self.agregar(f)

    def filas(self):
        salida = []
        for i in range(self.tabla.rowCount()):
            fila = []
            for j, col in enumerate(self.columnas):
                tipo = col[1]
                if tipo == 'combo':
                    fila.append(self.tabla.cellWidget(i, j).currentText().strip())
                elif tipo == 'bool':
                    fila.append(self.tabla.item(i, j).checkState() == Qt.CheckState.Checked)
                else:
                    item = self.tabla.item(i, j)
                    fila.append(item.text().strip() if item else '')
            salida.append(fila)
        return salida


# ==================================================================================================
# v8: columnas de un archivo CSV en una tabla compacta
# ==================================================================================================
def leer_cabecera(ruta):
    """Nombres de columna de un CSV (solo se lee la primera línea; detecta separador y codificación).
    None si el archivo no se puede abrir; [] si está vacío."""
    try:
        for codificacion in ('utf-8-sig', 'latin1'):
            try:
                with open(ruta, 'r', encoding=codificacion, newline='') as f:
                    linea = f.readline()
                break
            except UnicodeDecodeError:
                continue
    except OSError:
        return None
    if not linea.strip():
        return []
    separador = max((',', ';', '\t'), key=linea.count)
    return [c.strip() for c in next(csv.reader([linea], delimiter=separador))]


def _clave_columna(texto):
    """Nombre de columna comparable: sin tildes, minúsculas, sin espacios, guiones ni guiones bajos."""
    return _norm_titulo(texto).replace(' ', '').replace('_', '').replace('-', '')


def F(clave, etiqueta, opcional=False, ayuda='', defecto=None, sinonimos=()):
    """Una fila de PanelColumnas: ruta del JSON, texto del campo, si puede quedar vacía, ayuda, valor que se muestra
    si el JSON no lo trae y sinónimos con que «Leer columnas del CSV» sugiere la columna."""
    return {'clave': clave, 'etiqueta': etiqueta, 'opcional': opcional, 'ayuda': ayuda, 'defecto': defecto,
            'sinonimos': tuple(sinonimos)}


class PanelColumnas(QGroupBox):
    """Las columnas de un CSV que usa una pestaña, resumidas en una tabla «Campo → Columna del CSV» (como la de
    Importar BD) en vez de un campo de texto por columna. Cada fila es un desplegable editable cuyas opciones son las
    columnas del archivo; el estado indica si la columna existe en él. El archivo se lee solo (primera línea) al
    cargar o cambiar la ruta, y «Leer columnas del CSV» además propone columnas por nombre y sinónimos.

    - `archivo`: ruta del JSON del campo con el archivo. Con `crear_archivo=True` el panel crea ese campo (arriba);
      si no, debe existir ya en la pestaña (p. ej. 'modelo').
    - `ruta_defecto`: callable con el archivo a leer cuando el campo está vacío.
    - `ruta_respaldo`: callable con el archivo a leer cuando el del campo no existe aún.
    - `vinculados`: otros campos desplegables de la pestaña que también reciben las columnas como opciones."""

    ALTO_FILA = 26
    COLOR_OK, COLOR_AVISO, COLOR_ERROR = '#2e9d57', '#d89614', '#d9534f'

    def __init__(self, pestana, titulo, filas, archivo, crear_archivo=False, etiqueta_archivo='Archivo CSV',
                 ruta_defecto=None, ruta_respaldo=None, nota=''):
        super().__init__(titulo)
        self.filas = filas
        self.ruta_defecto = ruta_defecto
        self.ruta_respaldo = ruta_respaldo
        self.usa_respaldo = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)   # no se estira con la pestaña
        self.cabecera = None
        self.vinculados = []
        lay = QVBoxLayout(self)
        if crear_archivo:
            self.campo_archivo = Campo('archivo', filtro=FILTRO_CSV)
            pestana.campos[archivo] = self.campo_archivo
            form = QFormLayout()
            form.addRow(etiqueta_archivo, self.campo_archivo.widget)
            lay.addLayout(form)
        else:
            self.campo_archivo = pestana.campos[archivo]
        self.tabla = QTableWidget(len(filas), 3)
        self.tabla.setHorizontalHeaderLabels(['Campo', 'Columna del CSV', 'Estado'])
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.verticalHeader().setDefaultSectionSize(self.ALTO_FILA)
        self.tabla.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabla.setColumnWidth(0, 200)
        self.tabla.setColumnWidth(2, 240)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabla.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabla.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.campos = []
        for i, f in enumerate(filas):
            campo = Campo('combo', defecto=f['defecto'], ayuda=f['ayuda'])
            pestana.campos[f['clave']] = campo
            self.campos.append(campo)
            item = QTableWidgetItem(f['etiqueta'] + ('' if not f['opcional'] else '  (opcional)'))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if f['ayuda']:
                item.setToolTip(f['ayuda'])
            self.tabla.setItem(i, 0, item)
            self.tabla.setCellWidget(i, 1, campo.widget)
            estado = QTableWidgetItem('')
            estado.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tabla.setItem(i, 2, estado)
            campo.widget.currentTextChanged.connect(self._revisar)
        self.tabla.setFixedHeight(self.tabla.horizontalHeader().sizeHint().height()
                                  + self.ALTO_FILA * len(filas) + 4)
        lay.addWidget(self.tabla)
        pie = QHBoxLayout()
        boton = QPushButton('Leer columnas del CSV')
        boton.setToolTip('Lee el encabezado del archivo, ofrece sus columnas en cada fila y propone columnas para las '
                         'filas vacías o que no existen en el archivo (por nombre o sinónimo).')
        boton.clicked.connect(lambda: self.leer(silencioso=False))
        self.lbl = QLabel('')
        pie.addWidget(boton)
        pie.addWidget(self.lbl, 1)
        lay.addLayout(pie)
        if nota:
            n = QLabel(nota)
            n.setWordWrap(True)
            lay.addWidget(n)
        pestana.layout_principal.addWidget(self)
        pestana.paneles.append(self)
        # Lectura automática (con retardo para no leer en cada tecla) al cambiar la ruta del archivo.
        self.temporizador = QTimer(self)
        self.temporizador.setSingleShot(True)
        self.temporizador.setInterval(400)
        self.temporizador.timeout.connect(lambda: self.leer(silencioso=True))
        linea = getattr(self.campo_archivo.widget, 'linea', None)
        if linea is not None:
            linea.textChanged.connect(lambda *_: self.temporizador.start())

    # -- lectura --------------------------------------------------------------------------------------
    def vincular(self, *campos):
        """Otros desplegables de la pestaña que reciben las columnas del archivo como opciones."""
        self.vinculados.extend(campos)

    def ruta_actual(self):
        """Archivo del que se leen las columnas: el del campo; si está vacío, `ruta_defecto`; si no existe (p. ej. un CSV
        intermedio que el flujo aún no genera), `ruta_respaldo`."""
        self.usa_respaldo = False
        ruta = self.campo_archivo.leer().strip()
        if not ruta and self.ruta_defecto is not None:
            ruta = str(self.ruta_defecto() or '').strip()
        if not os.path.isfile(ruta) and self.ruta_respaldo is not None:
            respaldo = str(self.ruta_respaldo() or '').strip()
            if os.path.isfile(respaldo):
                self.usa_respaldo = True
                return respaldo
        return ruta

    def leer(self, silencioso=True):
        ruta = self.ruta_actual()
        cabecera = leer_cabecera(ruta) if ruta and os.path.isfile(ruta) else None
        if not cabecera:
            self.cabecera = None
            if cabecera is None:
                self.lbl.setText('Sin leer: el CSV aún no existe (lo genera el flujo) o falta indicarlo.'
                                 if self.campo_archivo.leer().strip() else 'Sin leer: indique un CSV existente.')
            else:
                self.lbl.setText('El archivo no tiene encabezado.')
            self._poner_opciones([])
            self._revisar()
            if not silencioso:
                QMessageBox.warning(self, 'Archivo', 'Indique primero un archivo CSV existente (y con encabezado).\n\n'
                                    + (ruta or ''))
            return
        self.cabecera = cabecera
        self._poner_opciones(cabecera)
        if not silencioso:
            self._proponer()
        self.lbl.setText(f'{len(cabecera)} columnas en {os.path.basename(ruta)}'
                         + (' (el CSV indicado aún no existe; se muestran las columnas de este archivo)'
                            if self.usa_respaldo else ''))
        self._revisar()

    def _poner_opciones(self, cabecera):
        for campo in [*self.campos, *self.vinculados]:
            combo = campo.widget
            texto = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([''] + list(cabecera))
            combo.setCurrentText(texto)
            combo.blockSignals(False)

    def _proponer(self):
        """Rellena las filas vacías (obligatorias) o que no existen en el archivo con la columna de igual nombre
        (sin distinguir mayúsculas ni separadores) o un sinónimo."""
        por_clave = {_clave_columna(c): c for c in self.cabecera}
        for f, campo in zip(self.filas, self.campos):
            actual = campo.widget.currentText().strip()
            if actual in self.cabecera or (not actual and f['opcional']):
                continue
            candidatos = [actual] if actual else [f['defecto'] or '', *f['sinonimos']]
            for c in candidatos:
                if c and _clave_columna(c) in por_clave:
                    campo.widget.setCurrentText(por_clave[_clave_columna(c)])
                    break

    def _revisar(self, *_):
        """Estado de cada fila: la columna existe, difiere en mayúsculas, no existe o falta."""
        for i, (f, campo) in enumerate(zip(self.filas, self.campos)):
            actual = campo.widget.currentText().strip()
            if not actual:
                texto, color = ('(vacío)', None) if f['opcional'] else ('✖ falta indicar la columna', self.COLOR_ERROR)
            elif self.cabecera is None:
                texto, color = '', None
            elif actual in self.cabecera:
                texto, color = '✔ en el CSV', self.COLOR_OK
            else:
                parecida = next((c for c in self.cabecera if c.lower() == actual.lower()), None)
                texto, color = ((f'≈ el CSV dice «{parecida}»', self.COLOR_AVISO) if parecida
                                else ('✖ no está en el CSV', self.COLOR_ERROR))
            item = self.tabla.item(i, 2)
            item.setText(texto)
            item.setForeground(QColor(color) if color else QColor(Qt.GlobalColor.gray))

    def faltantes(self):
        """Columnas indicadas que no están en el CSV leído (lista vacía si no se pudo leer)."""
        if not self.cabecera:
            return []
        return [campo.widget.currentText().strip() for f, campo in zip(self.filas, self.campos)
                if campo.widget.currentText().strip() and campo.widget.currentText().strip() not in self.cabecera]


# ==================================================================================================
# Pestaña base
# ==================================================================================================
class Pestana(QWidget):
    """Una configuración (un JSON). Los campos escalares se declaran con `fila`/`celda` y se leen/escriben
    de forma genérica por su ruta; las tablas y estructuras especiales las resuelven las subclases en
    `_cargar_extra` y `_recolectar_extra`."""

    TITULO = ''
    ARCHIVO = ''
    MODULO = ''

    def __init__(self):
        super().__init__()
        self.campos = {}
        self.cfg = {}
        self.paneles = []      # PanelColumnas de la pestaña (columnas de archivos CSV)
        self.tablas_csv = []   # tablas con importar/exportar CSV
        self.layout_principal = QVBoxLayout(self)
        self._construir()
        self.layout_principal.addStretch()

    # -- construcción -----------------------------------------------------------------------------
    def grupo(self, titulo):
        caja = QGroupBox(titulo)
        form = QFormLayout(caja)
        self.layout_principal.addWidget(caja)
        return form

    def rejilla(self, titulo):
        caja = QGroupBox(titulo)
        grid = QGridLayout(caja)
        self.layout_principal.addWidget(caja)
        return grid

    def fila(self, form, clave, etiqueta, tipo, **kw):
        campo = Campo(tipo, **kw)
        self.campos[clave] = campo
        form.addRow(etiqueta, campo.widget)
        return campo

    def celda(self, grid, indice, ncols, clave, etiqueta, tipo, **kw):
        campo = Campo(tipo, **kw)
        self.campos[clave] = campo
        r, c = divmod(indice, ncols)
        grid.addWidget(QLabel(etiqueta), r, c * 2)
        grid.addWidget(campo.widget, r, c * 2 + 1)
        return campo

    def tabla(self, titulo, columnas, **kw):
        caja = QGroupBox(titulo)
        lay = QVBoxLayout(caja)
        t = TablaEditable(columnas, **kw)
        lay.addWidget(t)
        self.layout_principal.addWidget(caja)
        if t.csv:
            self.tablas_csv.append(t)
        return t

    def _construir(self):
        raise NotImplementedError

    # -- lectura / escritura ---------------------------------------------------------------------
    def cargar(self, config_dir):
        ruta = os.path.join(config_dir, self.ARCHIVO)
        self.cfg = {}
        if os.path.exists(ruta):
            with open(ruta, 'r', encoding='utf-8-sig') as f:
                self.cfg = json.load(f)
        for clave, campo in self.campos.items():
            campo.escribir(get_path(self.cfg, clave, campo.defecto))
        self._cargar_extra(self.cfg)

    def recolectar(self):
        cfg = copy.deepcopy(self.cfg)
        cfg.setdefault('__modulo__', self.MODULO)
        cfg.setdefault('__version__', '1.0')
        for clave, campo in self.campos.items():
            set_path(cfg, clave, campo.leer(clave))
        self._recolectar_extra(cfg)
        return cfg

    def _cargar_extra(self, cfg):
        pass

    def _recolectar_extra(self, cfg):
        pass

    def rutas_inexistentes(self):
        """Archivos de ENTRADA (campos tipo 'archivo' y columnas de sólidos) que no existen."""
        faltan = [c.leer() for c in self.campos.values()
                  if c.tipo == 'archivo' and c.leer() and not os.path.exists(c.leer())]
        return faltan

    def advertencias(self):
        """Avisos que no impiden guardar (se muestran junto a los archivos inexistentes)."""
        return []

    def avisos_columnas(self):
        """Columnas indicadas en los paneles que no están en el encabezado del CSV (solo si el CSV existe)."""
        avisos = []
        for panel in self.paneles:
            panel.leer(silencioso=True)
            faltan = panel.faltantes()
            if faltan:
                avisos.append(f"{os.path.basename(panel.ruta_actual())}: no tiene la(s) columna(s) "
                              + ', '.join(f'«{c}»' for c in faltan) + f" ({panel.title()})")
        return avisos


def f_str(v):
    return '' if v is None else str(v)


# ==================================================================================================
# Pestañas
# ==================================================================================================
class PestanaProyecto(Pestana):
    TITULO, ARCHIVO, MODULO = 'Rutas del flujo', 'conf_proyecto.json', 'Proyecto'

    def _construir(self):
        f = self.grupo('Carpetas y archivos del proyecto')
        self.fila(f, 'raiz', 'Carpeta del proyecto', 'carpeta')
        self.fila(f, 'base_datos', 'Base de datos (.db)', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'carpeta_database', 'Carpeta de CSV de la base', 'carpeta')
        self.fila(f, 'carpeta_modelo_bloques', 'Carpeta del modelo de bloques', 'carpeta')
        self.fila(f, 'carpeta_reporte', 'Carpeta del reporte', 'carpeta')
        self.fila(f, 'nombre_modelo', 'Nombre base del modelo', 'txt',
                  ayuda='Genera <nombre>.csv, <nombre>_estimado.csv, _valorizado.csv y _categorizado.csv')
        f = self.grupo('Modelo de bloques')
        self.fila(f, 'crear_modelo_bloques', 'Crear el modelo de bloques en el flujo', 'bool', defecto=True,
                  ayuda='Desmarcado: el paso «modelo» se omite y la estimación usa el modelo existente.')
        self.fila(f, 'modelo_bloques_existente', 'Modelo de bloques existente (CSV)', 'archivo', filtro=FILTRO_CSV,
                  ayuda='Se usa como entrada de la estimación cuando no se crea el modelo. Puede traer variables '
                        '(p. ej. lva_b, lva_p, lva_d) para Bearing/Plunge/Dip de cada pasada.')
        f = self.grupo('Pasos opcionales del flujo')
        self.fila(f, 'valorizar_modelo', 'Valorizar el modelo en el flujo', 'bool', defecto=True,
                  ayuda='Desmarcado: el paso «valorizar» se omite; la categorización toma el modelo estimado y el reporte '
                        'corta por la ley del primer metal (sin NSR ni equivalente).')
        self.boton_aplicar = QPushButton('Aplicar estas rutas a todas las pestañas')
        self.boton_aplicar.setToolTip(
            'Rellena la base de datos, los CSV de salida y la cadena de modelos (crear o existente → estimar → '
            '[valorizar] → categorizar → reporte) y el plan de sondajes en las demás pestañas. No guarda hasta pulsar '
            '«Guardar todo».')
        self.layout_principal.addWidget(self.boton_aplicar)

    def rutas_inexistentes(self):
        """El modelo existente solo es una entrada obligatoria cuando no se crea el modelo."""
        c = self.campos
        if c['crear_modelo_bloques'].leer():
            return []
        ruta = c['modelo_bloques_existente'].leer()
        return [ruta] if ruta and not os.path.exists(ruta) else []


class PanelTablaImportacion(QWidget):
    """Una tabla a importar (Collar / Survey / Intervalo): archivo, nombre y rol/tipo de cada campo del CSV.
    Con `compositos=True` es la tabla única de la importación desde un CSV de compósitos (v5): el tipo es
    fijo, solo se piden Sondaje/Desde/Hasta y los nombres de columna conservan sus mayúsculas."""

    def __init__(self, datos, compositos=False):
        super().__init__()
        self.datos = copy.deepcopy(datos)
        self.compositos = compositos
        self.tipo = Campo('combo', [TIPO_COMPOSITO] if compositos else ['Collar', 'Survey', 'Intervalo'])
        self.archivo = Campo('archivo', filtro=FILTRO_CSV)
        self.nombre = Campo('txt')
        form = QFormLayout()
        if not compositos:
            form.addRow('Tipo de tabla', self.tipo.widget)
        form.addRow('Archivo CSV', self.archivo.widget)
        form.addRow('Nombre de la tabla', self.nombre.widget)
        roles = ROLES_COMPOSITO if compositos else ROLES
        titulo_campo = 'Campo del CSV' if compositos else 'Campo del CSV (MAYÚSCULAS)'
        self.tabla = TablaEditable([(titulo_campo, 'txt', None, 220), ('Rol', 'combo', roles, 120),
                                    ('Tipo de dato (Incluir)', 'combo', TIPOS_DATO, 140), ('Entero', 'bool', None, 60)],
                                   altura=210)
        boton = QPushButton('Leer columnas del CSV')
        boton.setToolTip('Agrega las columnas del archivo que aún no están en la tabla, con el rol sugerido por sinónimos.')
        boton.clicked.connect(self.leer_columnas)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.tabla)
        lay.addWidget(boton)
        self._a_widgets()

    def _a_widgets(self):
        d = self.datos
        self.tipo.escribir(TIPO_COMPOSITO if self.compositos else d.get('tipo', 'Collar'))
        self.archivo.escribir(d.get('archivo'))
        self.nombre.escribir(d.get('nombre_tabla'))
        tipos, enteros = d.get('tipos', {}), {str(e).upper() for e in d.get('enteros', [])}
        self.tabla.set_filas([[c, rol, tipos.get(c, ''), c.upper() in enteros]
                              for c, rol in d.get('roles', {}).items()])

    def a_dict(self):
        d = copy.deepcopy(self.datos)
        # Collar/Survey/Intervalo se guardan en MAYÚSCULAS (el importador las compara así); los compósitos
        # conservan el nombre de columna tal cual (el motor casa sin distinguir mayúsculas).
        clave = (lambda c: c) if self.compositos else (lambda c: c.upper())
        d['tipo'] = self.tipo.leer()
        d['archivo'] = self.archivo.leer()
        d['nombre_tabla'] = self.nombre.leer()
        filas = [f for f in self.tabla.filas() if f[0]]
        d['roles'] = {clave(f[0]): f[1] or ('Incluir' if self.compositos else 'Omitir') for f in filas}
        d['tipos'] = {clave(f[0]): f[2] for f in filas if f[2]}
        enteros = [clave(f[0]) for f in filas if f[3]]
        if enteros:
            d['enteros'] = enteros
        else:
            d.pop('enteros', None)
        return d

    def leer_columnas(self):
        ruta = self.archivo.leer()
        if not os.path.exists(ruta):
            QMessageBox.warning(self, 'Archivo', 'Indique primero un archivo CSV existente.')
            return
        try:
            import csv
            try:
                with open(ruta, 'r', encoding='utf-8-sig', newline='') as f:
                    cabecera = next(csv.reader(f))
            except UnicodeDecodeError:
                with open(ruta, 'r', encoding='latin1', newline='') as f:
                    cabecera = next(csv.reader(f))
            from ImportarBD import MAPEO   # sinónimos de YupAI
        except Exception as ex:   # noqa: BLE001
            QMessageBox.warning(self, 'Archivo', f'No se pudo leer el encabezado:\n{ex}')
            return
        existentes = {f[0].upper() for f in self.tabla.filas()}
        mapeo = MAPEO.get(self.tipo.leer(), {})
        for original in (c.strip() for c in cabecera):
            col = original.upper()
            if col and col not in existentes:
                # Compósitos: lo que no es Sondaje/Desde/Hasta se incluye (el CSV ya está curado).
                rol = next((r for r, sinonimos in mapeo.items() if col in sinonimos),
                           'Incluir' if self.compositos else 'Omitir')
                self.tabla.agregar([original if self.compositos else col, rol, '', False])


class PestanaImportar(Pestana):
    TITULO, ARCHIVO, MODULO = 'Importar BD', 'conf_importar_bd.json', 'ImportarProyecto'

    def _construir(self):
        # v5: la base puede armarse con los CSV de sondajes (Collar/Survey/Assay) o con un CSV de compósitos
        # ya codificado y compositado. conf_importar_bd.json guarda la elección en 'origen'.
        caja_origen = QGroupBox('Origen de los datos')
        lay_o = QVBoxLayout(caja_origen)
        self.radio_sondajes = QRadioButton('Sondajes: Collar, Survey y Assay (un CSV por tabla)')
        self.radio_compositos = QRadioButton(
            'Compósitos ya codificados y compositados (un CSV, p. ej. assay_comp.csv): la base se genera solo '
            'con ellos y los pasos Codificación, Acotamiento y Compositación se omiten')
        self.radio_compositos.setToolTip(
            'Importa una data previamente codificada y compositada. La base queda sin Collar ni Survey, lista para '
            'estimar. Las columnas se conservan tal cual y no se redondean.')
        self.radio_sondajes.setChecked(True)
        lay_o.addWidget(self.radio_sondajes)
        lay_o.addWidget(self.radio_compositos)
        self.layout_principal.addWidget(caja_origen)

        f = self.grupo('Base de datos y CSV')
        self.fila(f, 'proyecto', 'Base de datos (.db) a crear', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'carpeta_csv_salida', 'Carpeta de CSV de salida', 'carpeta')
        self.fila(f, 'decimales', 'Decimales de los CSV', 'int')
        self.fila(f, 'excluir_sondajes_con_observaciones', 'Excluir sondajes con observaciones', 'bool',
                  ayuda='Política estricta de YupAI: los sondajes con errores de validación no se importan.')
        self.caja_sondajes = QGroupBox('Tablas a importar (en este orden: Collar primero)')
        lay = QVBoxLayout(self.caja_sondajes)
        self.sub = QTabWidget()
        lay.addWidget(self.sub)
        botones = QHBoxLayout()
        for texto, accion in (('Agregar tabla', self._agregar_vacia), ('Quitar tabla actual', self._quitar)):
            b = QPushButton(texto)
            b.clicked.connect(accion)
            botones.addWidget(b)
        botones.addStretch()
        lay.addLayout(botones)
        self.layout_principal.addWidget(self.caja_sondajes)

        self.caja_compositos = QGroupBox('Compósitos a importar (CSV ya codificado y compositado)')
        self.lay_compositos = QVBoxLayout(self.caja_compositos)
        self.panel_compositos = None
        self.layout_principal.addWidget(self.caja_compositos)
        self.radio_compositos.toggled.connect(self._actualizar_origen)
        self._actualizar_origen()

    def es_compositos(self):
        return self.radio_compositos.isChecked()

    def _actualizar_origen(self, *_):
        """Muestra solo el bloque del origen elegido; 'excluir sondajes' solo aplica a Collar/Survey/Assay."""
        comp = self.es_compositos()
        self.caja_sondajes.setVisible(not comp)
        self.caja_compositos.setVisible(comp)
        self.campos['excluir_sondajes_con_observaciones'].widget.setEnabled(not comp)

    def _poner_panel_compositos(self, datos):
        if self.panel_compositos is not None:
            self.lay_compositos.removeWidget(self.panel_compositos)
            self.panel_compositos.hide()
            self.panel_compositos.setParent(None)      # sale de la ventana ya; deleteLater llega después
            self.panel_compositos.deleteLater()
        self.panel_compositos = PanelTablaImportacion(datos, compositos=True)
        self.lay_compositos.addWidget(self.panel_compositos)

    def _agregar_panel(self, datos):
        panel = PanelTablaImportacion(datos)
        self.sub.addTab(panel, datos.get('nombre_tabla') or 'tabla')
        self.sub.setCurrentWidget(panel)

    def _agregar_vacia(self):
        self._agregar_panel({'tipo': 'Intervalo', 'archivo': '', 'nombre_tabla': 'nueva',
                             'roles': {}, 'tipos': {}})

    def _quitar(self):
        if self.sub.count():
            self.sub.removeTab(self.sub.currentIndex())

    def _cargar_extra(self, cfg):
        self.sub.clear()
        for t in cfg.get('tablas', []):
            self._agregar_panel(t)
        self._poner_panel_compositos(cfg.get('compositos') or {
            'archivo': '', 'nombre_tabla': 'assay_comp', 'roles': {}, 'tipos': {}})
        self.radio_compositos.setChecked(cfg.get('origen', 'sondajes') == 'compositos')
        self.radio_sondajes.setChecked(not self.radio_compositos.isChecked())
        self._actualizar_origen()

    def _recolectar_extra(self, cfg):
        cfg['tablas'] = [self.sub.widget(i).a_dict() for i in range(self.sub.count())]
        cfg['origen'] = 'compositos' if self.es_compositos() else 'sondajes'
        cfg['compositos'] = self.panel_compositos.a_dict()
        if self.es_compositos():
            comp = cfg['compositos']
            if not comp['archivo']:
                raise ValueError('Indique el CSV de compósitos o elija el origen «Sondajes».')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', comp['nombre_tabla']):
                raise ValueError('El nombre de la tabla de compósitos solo admite letras, números y guion bajo, '
                                 'y debe empezar con una letra (p. ej. assay_comp).')

    def rutas_inexistentes(self):
        """Solo se exigen los CSV del origen elegido."""
        if self.es_compositos():
            ruta = self.panel_compositos.archivo.leer()
            return [ruta] if ruta and not os.path.exists(ruta) else []
        faltan = []
        for i in range(self.sub.count()):
            ruta = self.sub.widget(i).archivo.leer()
            if ruta and not os.path.exists(ruta):
                faltan.append(ruta)
        return faltan


class PestanaCodificar(Pestana):
    TITULO, ARCHIVO, MODULO = 'Codificación', 'conf_codificar.json', 'CodificacionSondajes'

    def _construir(self):
        f = self.grupo('Tabla y campo a codificar')
        self.fila(f, 'proyecto', 'Base de datos (.db)', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'tabla', 'Tabla de intervalos', 'txt')
        self.fila(f, 'campo', 'Campo a codificar', 'txt')
        self.fila(f, 'no_codificados', 'Tramos no codificados', 'combo', opciones=['defecto', 'mantener'])
        self.fila(f, 'valor_defecto', 'Valor por defecto', 'txt')
        self.fila(f, 'tolerancia_pared', 'Tolerancia de pared (m)', 'float')
        self.fila(f, 'metodo_desurvey', 'Método de desurvey', 'txt',
                  ayuda='Informativo: el codificador usa siempre Tangente Vulcan.')
        self.campos['metodo_desurvey'].widget.setEnabled(False)
        self.t_solidos = self.tabla('Sólidos de codificación (mayor prioridad prevalece)',
                                    [('Ruta del sólido (.obj)', 'txt', None, 460), ('Valor', 'txt', None, 90),
                                     ('Prioridad', 'txt', None, 80)], boton_archivo=0, filtro_archivo=FILTRO_OBJ,
                                    csv='codificacion_solidos.csv')

    def _cargar_extra(self, cfg):
        self.t_solidos.set_filas([[s.get('ruta'), s.get('valor'), s.get('prioridad')] for s in cfg.get('solidos', [])])

    def _recolectar_extra(self, cfg):
        cfg['solidos'] = [{'ruta': r, 'valor': v, 'prioridad': int(float(p or 1))}
                          for r, v, p in self.t_solidos.filas() if r]

    def rutas_inexistentes(self):
        return [f[0] for f in self.t_solidos.filas() if f[0] and not os.path.exists(f[0])]


class PestanaAcotamiento(Pestana):
    TITULO, ARCHIVO, MODULO = 'Acotamiento', 'conf_acotamiento.json', 'Acotamiento'

    def _construir(self):
        f = self.grupo('Tabla y variables')
        self.fila(f, 'proyecto', 'Base de datos (.db)', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'tabla', 'Tabla de muestras', 'txt')
        self.fila(f, 'campo_dominio', 'Campo de dominio', 'txt')
        self.fila(f, 'variable', 'Variable a acotar', 'txt')
        self.fila(f, 'nulos', 'Valores nulos (separados por coma)', 'txt')
        self.fila(f, 'bdl', 'Tratamiento de BDL (negativos)', 'combo', opciones=['mitad', 'excluir', 'cero', 'tal_cual'])
        self.t_topes = self.tabla('Topes por variable y dominio',
                                  [('Variable', 'txt', None, 110), ('Dominio', 'txt', None, 90),
                                   ('Tope', 'txt', None, 90),
                                   ('Método', 'combo', ['percentil', 'media_sigma', 'cv_objetivo', 'metal_riesgo', 'manual'], 130),
                                   ('Parámetro', 'txt', None, 90)], altura=120, csv='acotamiento_topes.csv')

    def _cargar_extra(self, cfg):
        filas = []
        for var, doms in cfg.get('topes', {}).items():
            for dom, info in doms.items():
                filas.append([var, dom, a_texto_numero(info.get('tope')), info.get('metodo', ''),
                              a_texto_numero(info['parametro']) if info.get('parametro') is not None else ''])
        self.t_topes.set_filas(filas)

    def _recolectar_extra(self, cfg):
        topes = {}
        for var, dom, tope, metodo, par in self.t_topes.filas():
            if not var or not dom:
                continue
            try:
                info = {'tope': float(tope.replace(',', '.')), 'metodo': metodo or 'manual'}
                if par:
                    info['parametro'] = float(par.replace(',', '.'))
            except ValueError:
                raise ValueError(f"Topes: tope o parámetro no numérico en {var}/{dom}.") from None
            topes.setdefault(var, {})[dom] = info
        cfg['topes'] = topes


class PestanaCompositacion(Pestana):
    TITULO, ARCHIVO, MODULO = 'Compositación', 'conf_comp.json', 'Compositacion'

    def _construir(self):
        f = self.grupo('Tablas')
        self.fila(f, 'proyecto', 'Base de datos (.db)', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'tabla_assay', 'Tabla de muestras (assay)', 'txt')
        self.fila(f, 'tabla_composito', 'Tabla de compósito a generar', 'txt')
        f = self.grupo('Método')
        self.fila(f, 'metodo', 'Método', 'combo', opciones=['Run Length', 'Straight'])
        self.fila(f, 'longitud', 'Longitud del compósito (m)', 'float')
        self.fila(f, 'tolerancia', 'Tolerancia del residuo (m)', 'float')
        f = self.grupo('Datos faltantes')
        self.fila(f, 'faltantes_flag', 'Valores que indican faltante', 'txt', ayuda='Separados por ; o coma. Ej.: -99')
        self.fila(f, 'faltantes_accion', 'Acción con faltantes', 'combo', opciones=['Ignorar Tramo', 'Asignar valor fijo'])
        self.fila(f, 'faltantes_valor', 'Valor fijo para faltantes', 'float')
        self.fila(f, 'noregistrado_accion', 'Acción con huecos no registrados', 'combo',
                  opciones=['Ignorar Tramo', 'Asignar valor fijo'])
        self.fila(f, 'noregistrado_valor', 'Valor fijo para huecos', 'float')
        f = self.grupo('Geología y campos')
        self.fila(f, 'cortar_geologia', 'Cortar compósitos por geología', 'bool')
        self.fila(f, 'codigo_roca', 'Campo de código de roca', 'txt')
        self.fila(f, 'campos_numericos', 'Campos numéricos (coma)', 'lista')
        self.fila(f, 'campos_categoricos', 'Campos categóricos (coma)', 'lista')


MODOS_LEY_PLAN = ['modelo', 'dominio', 'constante']


class PestanaPlan(Pestana):
    """v7: evaluación de un plan de sondajes (conf_plan.json). Genera muestras de ancho fijo a lo largo de cada
    sondaje nuevo, las codifica y compósita con el seteo de las pestañas Codificación y Compositación y agrega los
    compósitos a la base antes de estimar."""

    TITULO, ARCHIVO, MODULO = 'Plan de sondajes', 'conf_plan.json', 'PlanSondajes'

    def _construir(self):
        f = self.grupo('Plan de sondajes')
        self.fila(f, 'activo', 'Evaluar el plan de sondajes en el flujo', 'bool', defecto=True,
                  ayuda='Desmarcado: el paso «plan» se omite y el flujo estima solo con los compósitos existentes.')
        nota = QLabel('El plan solo trae Collar y Survey. Se genera un assay temporal con muestras de ancho fijo a lo '
                      'largo de cada sondaje, se codifica con el seteo de la pestaña «Codificación», se compósita con el '
                      'de «Compositación» y los compósitos resultantes se agregan a los existentes de la base antes de '
                      'estimar. Los datos de entrada no se modifican; al repetir el paso se reemplazan los compósitos del '
                      'plan anterior.')
        nota.setWordWrap(True)
        self.layout_principal.addWidget(nota)
        PanelColumnas(self, 'Collar del plan (CSV)', [
            F('collar.col_sondaje', 'Sondaje', sinonimos=('id', 'holeid', 'hole_id', 'bhid', 'dhid', 'ddh')),
            F('collar.col_este', 'Este (X)', sinonimos=('x', 'este', 'east', 'easting', 'xcollar')),
            F('collar.col_norte', 'Norte (Y)', sinonimos=('y', 'norte', 'north', 'northing', 'ycollar')),
            F('collar.col_cota', 'Cota (Z)', sinonimos=('z', 'cota', 'elevacion', 'rl', 'zcollar')),
            F('collar.col_longitud', 'Longitud', sinonimos=('length', 'largo', 'depth', 'profundidad', 'maxdepth'))],
            archivo='collar.archivo', crear_archivo=True)
        PanelColumnas(self, 'Survey del plan (CSV)', [
            F('survey.col_sondaje', 'Sondaje', sinonimos=('id', 'holeid', 'hole_id', 'bhid', 'dhid', 'ddh')),
            F('survey.col_profundidad', 'Profundidad', sinonimos=('depth', 'at', 'md', 'prof')),
            F('survey.col_azimut', 'Azimut', sinonimos=('az', 'azimuth', 'azi', 'bearing')),
            F('survey.col_dip', 'Dip', ayuda='Dip negativo hacia abajo, positivo hacia arriba (igual que el resto del '
              'flujo).', sinonimos=('inclinacion', 'incl', 'plunge'))],
            archivo='survey.archivo', crear_archivo=True)
        f = self.grupo('Muestreo del assay temporal')
        self.fila(f, 'muestreo.ancho', 'Ancho de muestra (m)', 'float', defecto=0.5,
                  ayuda='Largo de cada muestra a lo largo del sondaje; la última se acorta para terminar en la longitud.')
        g = self.rejilla('Ley provisional de las muestras (el plan no tiene análisis)')
        self.celda(g, 0, 2, 'ley.modo', 'Modo', 'combo', opciones=MODOS_LEY_PLAN, defecto=MODOS_LEY_PLAN[0],
                   ayuda='modelo: ley del bloque del modelo existente que contiene la muestra (si no está estimado, la '
                         'media del dominio).\ndominio: ley media del dominio de los compósitos existentes.\n'
                         'constante: el mismo valor en todas las muestras.')
        self.celda(g, 1, 2, 'ley.constante', 'Valor constante', 'float', defecto=0.0)
        nota = QLabel('Los campos numéricos de la Compositación (p. ej. CUPCT y CUPCT_ACOT) reciben la misma ley '
                      'provisional. Sin ley provisional los compósitos del plan no participarían de la estimación y no '
                      'cambiarían la categoría.')
        nota.setWordWrap(True)
        g.addWidget(nota, 2, 0, 1, 4)
        PanelColumnas(self, 'Modelo de bloques para la ley provisional (modo «modelo»)', [
            F('modelo.xc', 'Centro X', defecto='XC', sinonimos=('midx', 'x')),
            F('modelo.yc', 'Centro Y', defecto='YC', sinonimos=('midy', 'y')),
            F('modelo.zc', 'Centro Z', defecto='ZC', sinonimos=('midz', 'z')),
            F('modelo.dx', 'Largo DX', defecto='DX', sinonimos=('xsize', 'sizex')),
            F('modelo.dy', 'Largo DY', defecto='DY', sinonimos=('ysize', 'sizey')),
            F('modelo.dz', 'Largo DZ', defecto='DZ', sinonimos=('zsize', 'sizez')),
            F('ley.columna_modelo', 'Ley provisional', defecto='cu_idw',
              ayuda='Columna del modelo con la ley que se asigna a las muestras del plan (modo «modelo»).')],
            archivo='modelo.archivo', crear_archivo=True, etiqueta_archivo='Modelo (CSV)')
        f = self.grupo('Base y salidas')
        self.fila(f, 'proyecto', 'Base de datos (.db) del flujo', 'guardar', filtro=FILTRO_DB,
                  ayuda='Base donde se agregan los compósitos del plan (la misma de Importar BD y de la estimación).')
        self.fila(f, 'tabla_composito', 'Tabla de compósitos', 'txt', defecto='assay_comp',
                  ayuda='Tabla de la base a la que se agregan los compósitos del plan (p. ej. assay_comp).')
        self.fila(f, 'salida', 'Carpeta de salida', 'carpeta',
                  ayuda='Aquí quedan el assay temporal, los compósitos del plan, Plan_Resumen.json (lo lee el reporte) '
                        'y la base temporal.')
        self.fila(f, 'conservar_temporal', 'Conservar la base temporal (Plan_Temporal.db)', 'bool', defecto=True)

    def rutas_inexistentes(self):
        """Solo se exigen las entradas si el plan está activo (el modelo, solo en el modo «modelo»)."""
        c = self.campos
        if not c['activo'].leer():
            return []
        claves = ['collar.archivo', 'survey.archivo'] + (['modelo.archivo'] if c['ley.modo'].leer() == 'modelo' else [])
        return [c[k].leer() if c[k].leer() else f'({k} sin indicar)' for k in claves
                if not c[k].leer() or not os.path.exists(c[k].leer())]

    def _recolectar_extra(self, cfg):
        if cfg['ley']['modo'] not in MODOS_LEY_PLAN:
            raise ValueError(f"'ley.modo' debe ser uno de: {', '.join(MODOS_LEY_PLAN)}.")
        if not cfg['muestreo']['ancho'] > 0:
            raise ValueError('El ancho de muestra del plan debe ser mayor que 0.')
        if cfg['ley']['modo'] == 'modelo' and not cfg['ley']['columna_modelo']:
            raise ValueError('Indique la columna de ley del modelo o elija otro modo de ley provisional.')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', cfg['tabla_composito']):
            raise ValueError('El nombre de la tabla de compósitos del plan solo admite letras, números y guion bajo, y '
                             'debe empezar con una letra (p. ej. assay_comp).')
        if cfg['activo']:
            for clave, texto in (('collar', 'Collar'), ('survey', 'Survey')):
                if not cfg[clave]['archivo']:
                    raise ValueError(f'Indique el CSV de {texto} del plan o desmarque «Evaluar el plan de sondajes».')
            if not cfg['salida']:
                raise ValueError('Indique la carpeta de salida del plan.')


class PestanaModeloBloques(Pestana):
    TITULO, ARCHIVO, MODULO = 'Modelo de bloques', 'conf_crear_mb.json', 'ModeloBloques'

    def _construir(self):
        nota = QLabel('Esta pestaña solo se usa si en «Rutas del flujo» está marcado «Crear el modelo de bloques '
                      'en el flujo»; si no, la estimación usa el modelo existente.')
        nota.setWordWrap(True)
        self.layout_principal.addWidget(nota)
        # v6: el esquema de la grilla se puede identificar desde un CSV de bloques ya existente.
        self.modelo_existente = lambda: ''    # la Ventana lo enlaza con «Modelo de bloques existente»
        def _col(clave, texto):
            return F(f'esquema_csv.{clave}', texto, defecto=texto, sinonimos=('midx', 'midy', 'midz'),
                     ayuda=f'Columna del CSV con {texto} (centroide o dimensión del bloque).')
        PanelColumnas(self, 'Identificar el esquema desde un CSV de bloques',
                      [_col('col_xc', 'XC'), _col('col_yc', 'YC'), _col('col_zc', 'ZC'),
                       _col('col_dx', 'DX'), _col('col_dy', 'DY'), _col('col_dz', 'DZ')],
                      archivo='esquema_csv.archivo', crear_archivo=True, etiqueta_archivo='CSV de bloques',
                      ruta_defecto=lambda: self.modelo_existente(),
                      nota='Archivo vacío = el modelo de bloques existente de «Rutas del flujo».')
        self.boton_identificar = QPushButton('Identificar esquema desde el CSV')
        self.boton_identificar.setToolTip('Rellena origen, extensión, tamaño de bloque, subbloque y bearing de abajo '
                                          'con lo que trae el CSV. No guarda hasta pulsar «Guardar todo».')
        self.boton_identificar.clicked.connect(self.identificar_esquema)
        self.layout_principal.addWidget(self.boton_identificar)
        nota = QLabel('Extensión = largo del modelo en metros en cada eje (n.º de bloques padre × tamaño). Bloque padre = '
                      'mayor DX/DY/DZ y subbloque = menor (iguales si no hay subbloques). Bearing = azimut del eje X '
                      'local (90 = sin rotación).')
        nota.setWordWrap(True)
        self.layout_principal.addWidget(nota)
        g = self.rejilla('Esquema de la grilla')
        etq = [('origen_x', 'Origen X'), ('origen_y', 'Origen Y'), ('origen_z', 'Origen Z'),
               ('extension_x', 'Extensión X'), ('extension_y', 'Extensión Y'), ('extension_z', 'Extensión Z'),
               ('x_size', 'Tamaño bloque X'), ('y_size', 'Tamaño bloque Y'), ('z_size', 'Tamaño bloque Z'),
               ('sub_x', 'Subbloque X'), ('sub_y', 'Subbloque Y'), ('sub_z', 'Subbloque Z'),
               ('bearing', 'Bearing (°)')]
        for i, (clave, texto) in enumerate(etq):
            self.celda(g, i, 3, f'esquema.{clave}', texto, 'float')
        f = self.grupo('Salida y opciones')
        self.fila(f, 'tolerancia_pared', 'Tolerancia de pared (m)', 'float')
        self.fila(f, 'salida', 'CSV del modelo de bloques', 'guardar', filtro=FILTRO_CSV)
        self.t_vars = self.tabla('Variables del modelo',
                                 [('Nombre', 'txt', None, 120), ('Tipo', 'combo', ['Float', 'Integer', 'String'], 90),
                                  ('Valor por defecto', 'txt', None, 110), ('Descripción', 'txt', None, 250)], altura=90,
                                 csv='modelo_bloques_variables.csv')
        self.t_solidos = self.tabla('Sólidos (variable = valor dentro del sólido; mayor prioridad prevalece)',
                                    [('Ruta del sólido (.obj)', 'txt', None, 420), ('Variable', 'txt', None, 100),
                                     ('Valor', 'txt', None, 70), ('Prioridad', 'txt', None, 70)],
                                    altura=90, boton_archivo=0, filtro_archivo=FILTRO_OBJ,
                                    csv='modelo_bloques_solidos.csv')
        self.t_exc = self.tabla('Excepciones (se eliminan los bloques que cumplan la expresión)',
                                [('Expresión', 'txt', None, 300), ('Descripción', 'txt', None, 250)], altura=80,
                                csv='modelo_bloques_excepciones.csv')

    def identificar_esquema(self):
        """Lee el CSV de bloques, identifica la grilla y rellena los campos de «Esquema de la grilla»."""
        from Herramientas.EsquemaDesdeBloques import inferir_esquema_csv, texto_resumen
        ruta = self.campos['esquema_csv.archivo'].leer() or self.modelo_existente()
        if not ruta or not os.path.exists(ruta):
            QMessageBox.warning(self, 'Identificar esquema',
                                'Indique un CSV de bloques existente (o el modelo de bloques existente en '
                                '«Rutas del flujo»).\n\n' + (ruta or ''))
            return
        columnas = [self.campos[f'esquema_csv.{c}'].leer() for c in
                    ('col_xc', 'col_yc', 'col_zc', 'col_dx', 'col_dy', 'col_dz')]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = inferir_esquema_csv(ruta, columnas)
        except Exception as ex:   # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, 'Identificar esquema', f'No se pudo identificar el esquema:\n{ex}')
            return
        QApplication.restoreOverrideCursor()
        for clave, valor in info['esquema'].items():
            self.campos[f'esquema.{clave}'].escribir(valor)
        QMessageBox.information(self, 'Identificar esquema',
                                f'{os.path.basename(ruta)}\n\n{texto_resumen(info)}\n\nSe cargó en «Esquema de la '
                                'grilla». Falta «Guardar todo».')

    def _cargar_extra(self, cfg):
        self.t_vars.set_filas([[v.get('nombre'), v.get('tipo'), v.get('valor_defecto'), v.get('descripcion')]
                               for v in cfg.get('variables', [])])
        self.t_solidos.set_filas([[s.get('ruta'), s.get('variable'), s.get('valor'), s.get('prioridad')]
                                  for s in cfg.get('solidos', [])])
        self.t_exc.set_filas([[e.get('expresion'), e.get('descripcion')] for e in cfg.get('excepciones', [])])

    def _recolectar_extra(self, cfg):
        cfg['variables'] = [{'nombre': n, 'tipo': t or 'Float', 'valor_defecto': vd, 'descripcion': d}
                            for n, t, vd, d in self.t_vars.filas() if n]
        cfg['solidos'] = [{'ruta': r, 'variable': v, 'valor': val, 'prioridad': int(float(p or 1))}
                          for r, v, val, p in self.t_solidos.filas() if r]
        cfg['excepciones'] = [{'expresion': e, 'descripcion': d} for e, d in self.t_exc.filas() if e]

    def rutas_inexistentes(self):
        return [f[0] for f in self.t_solidos.filas() if f[0] and not os.path.exists(f[0])]


class PestanaRecorte(Pestana):
    """v8: recorta el modelo de bloques a los bloques cuyo centroide cae dentro de un sólido (conf_recorte.json).
    La estimación, la valorización, la categorización y el reporte trabajan solo con esos bloques."""

    TITULO, ARCHIVO, MODULO = 'Recorte del modelo', 'conf_recorte.json', 'RecorteModelo'

    def _construir(self):
        f = self.grupo('Recorte')
        self.fila(f, 'activo', 'Recortar el modelo al sólido antes de estimar', 'bool', defecto=True,
                  ayuda='Desmarcado: el paso «recortar» se omite y la estimación usa el modelo completo.')
        nota = QLabel('Deja solo los bloques cuyo centroide cae dentro del sólido (la misma prueba del reporte dentro de '
                      'un sólido), de modo que la estimación, la valorización, la categorización y el reporte corren sobre '
                      'una fracción del modelo. El reporte dentro de ese sólido da los mismos resultados que con el modelo '
                      'completo. Los compósitos no se recortan.')
        nota.setWordWrap(True)
        self.layout_principal.addWidget(nota)
        f = self.grupo('Archivos')
        self.fila(f, 'modelo', 'Modelo de bloques completo (CSV)', 'archivo', filtro=FILTRO_CSV,
                  ayuda='Modelo creado en el flujo o modelo existente de «Rutas del flujo».')
        self.fila(f, 'salida', 'CSV del modelo recortado', 'guardar', filtro=FILTRO_CSV,
                  ayuda='Es la entrada de la estimación cuando el recorte está activo.')
        f = self.grupo('Sólido de recorte')
        self.fila(f, 'solido.ruta', 'Sólido (.obj)', 'archivo', filtro=FILTRO_OBJ,
                  ayuda='Normalmente el mismo sólido del reporte (p. ej. 01_Data\\05_Objetivo\\OXIDOS.obj).')
        self.fila(f, 'solido.tolerancia_pared', 'Tolerancia de pared (m)', 'float', defecto=0.0,
                  ayuda='Rescata centroides que quedan sobre la superficie del sólido (0 = sin rescate). Use el mismo '
                        'valor que el reporte.')
        PanelColumnas(self, 'Centroide del bloque (columnas del modelo)', [
            F('solido.campos.x', 'Centroide X', defecto='XC', sinonimos=('midx', 'x')),
            F('solido.campos.y', 'Centroide Y', defecto='YC', sinonimos=('midy', 'y')),
            F('solido.campos.z', 'Centroide Z', defecto='ZC', sinonimos=('midz', 'z'))],
            archivo='modelo')

    def rutas_inexistentes(self):
        """Las entradas solo son obligatorias con el recorte activo."""
        c = self.campos
        if not c['activo'].leer():
            return []
        return [c[k].leer() if c[k].leer() else f'({k} sin indicar)' for k in ('modelo', 'solido.ruta')
                if not c[k].leer() or not os.path.exists(c[k].leer())]

    def _recolectar_extra(self, cfg):
        if not cfg['activo']:
            return
        if not cfg['modelo'] or not cfg['salida'] or not cfg['solido']['ruta']:
            raise ValueError('Indique el modelo, el CSV recortado y el sólido, o desmarque «Recortar el modelo».')
        if os.path.normcase(os.path.normpath(cfg['modelo'])) == os.path.normcase(os.path.normpath(cfg['salida'])):
            raise ValueError('El CSV recortado no puede ser el mismo archivo que el modelo completo.')
        if cfg['solido']['tolerancia_pared'] < 0:
            raise ValueError('La tolerancia de pared no puede ser negativa.')


COLS_PASADA = ['ID', 'Pasada', 'Compósito', 'Campo Ley', 'Peso', 'Campo dominio', 'Valor dominio', 'Variable',
               'Potencia', 'Mayor', 'Semi', 'Menor', 'Bearing', 'Plunge', 'Dip', 'Mín Samp', 'Máx Samp',
               'Máx/oct', 'Máx samp/sond', 'Disc X', 'Disc Y', 'Disc Z', 'Condición']
COLS_ANGULO = ['Bearing', 'Plunge', 'Dip']   # número o nombre de una variable del modelo de bloques
TIPOS_MINADO = ['binario', 'porcentaje']     # variable de minado del reporte


class PestanaEstimacion(Pestana):
    TITULO, ARCHIVO, MODULO = 'Estimación IDW', 'conf_estimacion.json', 'EstimacionIDW'

    def _construir(self):
        f = self.grupo('Entradas y salida')
        self.fila(f, 'modelo', 'Modelo de bloques (CSV)', 'archivo', filtro=FILTRO_CSV)
        self.fila(f, 'proyecto', 'Base de datos (.db) con los compósitos', 'guardar', filtro=FILTRO_DB)
        self.fila(f, 'salida', 'CSV del modelo estimado', 'guardar', filtro=FILTRO_CSV)
        g = self.rejilla('Campos del compósito (tabla de la base de datos)')
        for i, (clave, texto) in enumerate([('x', 'X'), ('y', 'Y'), ('z', 'Z'), ('sondaje', 'Sondaje')]):
            self.celda(g, i, 4, f'campos.{clave}', texto, 'txt')
        PanelColumnas(self, 'Campos del bloque (columnas del modelo de bloques)', [
            F('campos.bloque_xc', 'Centro X (XC)', defecto='XC', sinonimos=('midx', 'x')),
            F('campos.bloque_yc', 'Centro Y (YC)', defecto='YC', sinonimos=('midy', 'y')),
            F('campos.bloque_zc', 'Centro Z (ZC)', defecto='ZC', sinonimos=('midz', 'z')),
            F('campos.bloque_dx', 'Largo DX', defecto='DX', sinonimos=('xsize', 'sizex')),
            F('campos.bloque_dy', 'Largo DY', defecto='DY', sinonimos=('ysize', 'sizey')),
            F('campos.bloque_dz', 'Largo DZ', defecto='DZ', sinonimos=('zsize', 'sizez'))],
            archivo='modelo')
        g = self.rejilla('Nombres de columnas de reporte (vacío = derivar de la variable)')
        for i, (clave, texto) in enumerate([('nsamp', 'nsamp'), ('nholes', 'nholes'), ('flag', 'pasada (flag)'),
                                            ('distclose_cart', 'distclose_cart'),
                                            ('distclose_anisot', 'distclose_anisot'), ('nn', 'nn'),
                                            ('avgdist_cart', 'avgdist_cart'), ('avgdist_anisot', 'avgdist_anisot'),
                                            ('sondaje', 'sondaje')]):
            self.celda(g, i, 3, f'reporte.{clave}', texto, 'txt')
        self.celda(g, 9, 3, 'reporte.valor_defecto', 'Valor por defecto', 'float')
        g = self.rejilla('Estimar en bloque padre')
        self.celda(g, 0, 4, 'est_parent.activo', 'Activo', 'bool')
        for i, (clave, texto) in enumerate([('origen_x', 'Origen X'), ('origen_y', 'Origen Y'), ('origen_z', 'Origen Z'),
                                            ('tam_x', 'Tamaño X'), ('tam_y', 'Tamaño Y'), ('tam_z', 'Tamaño Z'),
                                            ('bearing', 'Bearing')], start=1):
            self.celda(g, i, 4, f'est_parent.{clave}', texto, 'txt')
        g = self.rejilla('Distancia a 3 sondajes (para categorizar)')
        for i, (clave, texto) in enumerate([('mayor', 'Radio mayor'), ('semi', 'Radio semi'), ('menor', 'Radio menor'),
                                            ('elemento_principal', 'Elemento principal')]):
            self.celda(g, i, 4, f'd3h.{clave}', texto, 'txt')
        g = self.rejilla('Variables ya estimadas en el modelo de bloques')
        self.celda(g, 0, 2, 'reiniciar.activo', 'Reiniciar a «no estimado» antes de estimar', 'bool', defecto=True,
                   ayuda='Un modelo existente puede traer ya estimada la variable (p. ej. cu_idw). Las pasadas solo estiman '
                         'los bloques en el valor por defecto (-99): se reinician la variable y sus columnas de reporte.')
        self.celda(g, 1, 2, 'reiniciar.conservar_anterior', 'Guardar la ley previa como <variable>_anterior', 'bool',
                   defecto=True, ayuda='El reporte del plan la usa para comparar leyes y metal fino anterior vs actual.')
        nota = QLabel('Valor dominio acepta uno o varios dominios separados por coma (p. ej. 21, 22, 23); la '
                      'Condición sigue eligiendo los bloques a estimar. Bearing, Plunge y Dip aceptan un número '
                      '(igual para todos los bloques) o el nombre de una columna del modelo de bloques '
                      '(p. ej. lva_b, lva_p, lva_d), que da el ángulo de cada bloque.')
        nota.setWordWrap(True)
        self.layout_principal.addWidget(nota)
        ayuda_angulo = 'Número o nombre de una variable del modelo de bloques (p. ej. lva_b).'
        ayuda_dominio = ('Uno o varios dominios separados por coma (p. ej. 21 o 21, 22, 23). Las muestras de '
                         'todos esos dominios se usan juntas en la pasada. Vacío = sin filtro de dominio.')
        self.t_pasadas = self.tabla('Pasadas (filas con la misma Variable forman un elemento multipasada)',
                                    [(c, 'txt', None, 190 if c in ('Condición',) else
                                      95 if c in ('ID', 'Compósito', 'Campo Ley', 'Campo dominio') else
                                      110 if c == 'Valor dominio' else 70)
                                     for c in COLS_PASADA], altura=190, csv='estimacion_pasadas.csv',
                                    tooltips={**{c: ayuda_angulo for c in COLS_ANGULO},
                                              'Valor dominio': ayuda_dominio})

    def _cargar_extra(self, cfg):
        self.t_pasadas.set_filas([[f_str(p.get(c)) for c in COLS_PASADA] for p in cfg.get('pasadas', [])])

    def _recolectar_extra(self, cfg):
        cfg['pasadas'] = [dict(zip(COLS_PASADA, fila)) for fila in self.t_pasadas.filas() if fila[0]]

    def advertencias(self):
        """Variables de orientación (Bearing/Plunge/Dip no numéricos) que no están en el modelo de bloques."""
        variables = {}
        for fila in self.t_pasadas.filas():
            reg = dict(zip(COLS_PASADA, fila))
            for c in COLS_ANGULO:
                if reg[c] and not es_numero(reg[c]):
                    variables.setdefault(reg[c], []).append(f"{reg['ID']} ({c})")
        ruta = self.campos['modelo'].leer()
        if not variables or not os.path.exists(ruta):
            return []
        try:
            with open(ruta, 'r', encoding='utf-8-sig', newline='') as f:
                columnas = {c.strip().lower() for c in next(csv.reader(f))}
        except (OSError, StopIteration, UnicodeDecodeError):
            return []
        return [f"El modelo no tiene la columna «{var}» usada como orientación en: {', '.join(usos)}"
                for var, usos in variables.items() if var.lower() not in columnas]


class PestanaValorizacion(Pestana):
    TITULO, ARCHIVO, MODULO = 'Valorización', 'conf_valorizacion.json', 'Valorizacion'

    def _construir(self):
        f = self.grupo('Entrada y salida')
        self.fila(f, 'modelo', 'Modelo estimado (CSV)', 'archivo', filtro=FILTRO_CSV)
        self.fila(f, 'salida', 'CSV del modelo valorizado', 'guardar', filtro=FILTRO_CSV)
        f = self.grupo('Densidad y expresiones económicas')
        self.fila(f, 'asignar_densidad', 'Asignar una densidad fija a todos los bloques', 'bool', defecto=True,
                  ayuda='Desmarcado: no se escribe ninguna densidad y se respeta la que ya trae el modelo de '
                        'bloques (densidad variable por bloque).')
        self.fila(f, 'densidad', 'Densidad', 'float')
        self.fila(f, 'col_densidad', 'Columna de densidad', 'txt',
                  ayuda='Columna donde se escribe la densidad fija; con la asignación desmarcada es la columna '
                        'del modelo que ya trae la densidad de cada bloque (la misma que usa el reporte).')
        self.campos['asignar_densidad'].widget.stateChanged.connect(self.actualizar_densidad)
        self.fila(f, 'exp_nsr', 'Expresión de NSR', 'txt')
        self.fila(f, 'col_nsr', 'Columna de NSR', 'txt')
        self.fila(f, 'exp_equiv', 'Expresión del equivalente', 'txt')
        self.fila(f, 'col_equiv', 'Columna del equivalente', 'txt')
        self.t_metales = self.tabla('Metales (la recuperación genera la columna pct_rec_<clave>)',
                                    [('Clave', 'txt', None, 80), ('Columna de ley', 'txt', None, 130),
                                     ('Expresión de recuperación', 'txt', None, 420)], altura=100,
                                    csv='valorizacion_metales.csv')

    def actualizar_densidad(self, *_):
        """El valor de densidad solo se usa cuando se asigna una densidad fija."""
        self.campos['densidad'].widget.setEnabled(self.campos['asignar_densidad'].leer())

    def recolectar(self):
        d = self.campos['densidad']
        if not self.campos['asignar_densidad'].leer() and not d.widget.text().strip():
            d.widget.setText('2.7')   # sin asignar no se usa, pero el campo numérico no puede quedar vacío
        return super().recolectar()

    def _cargar_extra(self, cfg):
        self.actualizar_densidad()
        self.t_metales.set_filas([[m.get('clave'), m.get('columna'), m.get('exp_recuperacion')]
                                  for m in cfg.get('metales', [])])

    def _recolectar_extra(self, cfg):
        cfg['metales'] = [{'clave': c, 'columna': col, 'exp_recuperacion': e}
                          for c, col, e in self.t_metales.filas() if c]


class PestanaCategorizacion(Pestana):
    TITULO, ARCHIVO, MODULO = 'Categorización', 'conf_categ.json', 'Categorizacion'

    def _construir(self):
        f = self.grupo('Entrada y salida')
        self.fila(f, 'modelo', 'Modelo de entrada: valorizado o, sin valorizar, estimado (CSV)', 'archivo',
                  filtro=FILTRO_CSV)
        self.fila(f, 'salida', 'CSV del modelo categorizado', 'guardar', filtro=FILTRO_CSV)
        self.fila(f, 'metodo', 'Método', 'combo', opciones=['IDW'])
        PanelColumnas(self, 'Columnas del modelo de entrada', [
            F('campos.distancia', 'Distancia a 3 sondajes', defecto='d3h_avgdist_anisot',
              sinonimos=('d3h_avganis', 'd3h_avgdist_anisot')),
            F('campos.nholes', 'Número de sondajes', defecto='cu_idw_nholes', sinonimos=('nholes',)),
            F('campos.pass', 'Pasada de la estimación', opcional=True, defecto='cu_idw_pass',
              ayuda='Opcional: sin ella no se filtra por pasada.', sinonimos=('pass', 'flag')),
            F('piso.columna_anterior', 'Categoría anterior (piso)', defecto='categ', opcional=True,
              ayuda='Columna del modelo con la categoría original (p. ej. categ). Se usa solo si el piso está activo.')],
            archivo='modelo')
        f = self.grupo('Salida y piso')
        self.fila(f, 'campos.col_categoria', 'Columna de categoría (salida)', 'txt')
        self.fila(f, 'piso.activo', 'Aplicar piso: la categoría nueva no puede ser peor que la anterior', 'bool',
                  defecto=True,
                  ayuda='La estimación multipasada se detiene en la primera pasada que reúne las muestras mínimas; un '
                        'sondaje nuevo puede completarlas en una pasada chica y dejar el bloque con 1-2 sondajes aunque '
                        'antes tuviera ~10, lo que bajaría la categoría. Con el piso, un bloque nunca empeora (Medido > '
                        'Indicado > Inferido > Potencial). La categoría sin piso queda en <salida>_sin_piso.')
        self.campos['piso.activo'].widget.stateChanged.connect(self.actualizar_piso)
        f = self.grupo('Distancias de corte (m)')
        self.fila(f, 'distancias.medido', 'Medido', 'float')
        self.fila(f, 'distancias.indicado', 'Indicado', 'float')
        self.fila(f, 'distancias.inferido', 'Inferido', 'float')
        self.fila(f, 'distancias.potencial', 'Potencial', 'float')

    def actualizar_piso(self, *_):
        self.campos['piso.columna_anterior'].widget.setEnabled(self.campos['piso.activo'].leer())

    def _cargar_extra(self, cfg):
        self.actualizar_piso()


class PestanaReporte(Pestana):
    TITULO, ARCHIVO, MODULO = 'Reporte', 'conf_reporte.json', 'ReporteRecursos'

    def _construir(self):
        f = self.grupo('Entrada y salida')
        self.fila(f, 'modelo', 'Modelo categorizado (CSV)', 'archivo', filtro=FILTRO_CSV)
        self.fila(f, 'salida', 'Carpeta del reporte', 'carpeta')
        sin_vol = 'Opcional: vacío = DX·DY·DZ.'
        panel = PanelColumnas(self, 'Columnas del modelo', [
            F('columnas.coboGrupo', 'Grupo (dominio/veta)', sinonimos=('domcu', 'dominio', 'domain', 'envau')),
            F('columnas.coboCategoria', 'Categoría', sinonimos=('categ', 'categoria')),
            F('columnas.coboDensidad', 'Densidad', sinonimos=('densidad', 'density', 'dens')),
            F('columnas.coboVolumen', 'Volumen', opcional=True, ayuda=sin_vol),
            F('columnas.coboXLen', 'X Length', opcional=True, ayuda=sin_vol + ' Con volumen vacío se usa DX·DY·DZ.',
              sinonimos=('dx',)),
            F('columnas.coboYLen', 'Y Length', opcional=True, sinonimos=('dy',)),
            F('columnas.coboZLen', 'Z Length', opcional=True, sinonimos=('dz',)),
            F('columnas.coboNsr', 'NSR', opcional=True, ayuda='Solo con el modelo valorizado.', sinonimos=('nsr',)),
            F('columnas.coboEquiv', 'Equivalente', opcional=True, ayuda='Solo con el modelo valorizado.',
              sinonimos=('equiv', 'equivalente'))],
            archivo='modelo', nota='Con el modelo sin valorizar se dejan NSR y Equivalente vacíos.')
        f = self.grupo('Corte y metal principal')
        self.fila(f, 'valorizado', 'El modelo está valorizado (NSR / equivalente)', 'bool', defecto=True,
                  ayuda='Desmarcado: el reporte no usa NSR ni equivalente (sin esas columnas) y aplica el cut-off a la '
                        'ley del primer metal de la tabla «Metales a reportar».')
        self.fila(f, 'variable_corte', 'Variable de corte (si está valorizado)', 'combo', opciones=['equiv', 'nsr'])
        self.fila(f, 'cutoff', 'Cut-off', 'float')
        self.fila(f, 'metal_principal', 'Metal principal', 'txt')
        self.fila(f, 'unidad_principal', 'Unidad del principal', 'combo', opciones=['oz', 'ppm', 'g/t'])
        self.fila(f, 'fecha_reporte', 'Fecha del reporte (AAAA-MM-DD)', 'txt')
        self.fila(f, 'etiqueta_precios', 'Nota de precios (pie)', 'txt')
        g = self.rejilla('Reportar dentro de un sólido (solo bloques cuyo centroide cae dentro del .obj)')
        self.celda(g, 0, 2, 'solido.activo', 'Reportar solo dentro del sólido', 'bool', defecto=False)
        self.celda(g, 1, 2, 'solido.tolerancia_pared', 'Tolerancia de pared (m)', 'float', defecto=0.0,
                   ayuda='Rescata centroides que quedan sobre la superficie del sólido (0 = sin rescate).')
        self.celda(g, 2, 2, 'solido.ruta', 'Sólido (.obj)', 'archivo', filtro=FILTRO_OBJ)
        for i, (clave, texto) in enumerate([('x', 'Centroide X'), ('y', 'Centroide Y'), ('z', 'Centroide Z')], start=3):
            panel.vincular(self.celda(g, i, 2, f'solido.campos.{clave}', texto, 'combo', defecto=clave.upper() + 'C'))
        g = self.rejilla('Variable de minado (los bloques ya minados no se reportan)')
        self.celda(g, 0, 3, 'minado.activo', 'Considerar variable de minado', 'bool', defecto=False)
        panel.vincular(self.celda(g, 1, 3, 'minado.variable', 'Variable de minado', 'combo', defecto='',
                                  ayuda='Columna del modelo de bloques que indica lo minado.'))
        self.celda(g, 2, 3, 'minado.tipo', 'Tipo', 'combo', opciones=TIPOS_MINADO, defecto=TIPOS_MINADO[0],
                   ayuda='binario: 1 = minado, 0 = no minado.\n'
                         'porcentaje: valor continuo, 100 = no minado, 0 = minado; un valor intermedio deja esa '
                         'proporción del bloque (se reduce su tonelaje).')
        nota = QLabel('binario: 1 = minado, 0 = no minado.   porcentaje: 100 = no minado, 0 = minado '
                      '(los valores intermedios reducen el tonelaje del bloque).')
        nota.setWordWrap(True)
        g.addWidget(nota, 1, 0, 1, 6)
        g = self.rejilla('Comparar con la categoría anterior del modelo (hoja «Comparacion_Categ»)')
        self.celda(g, 0, 2, 'comparacion.activo', 'Comparar tonelajes anterior vs calculada', 'bool', defecto=False,
                   ayuda='Agrega al Excel una hoja con el tonelaje por categoría y dominio de la categoría anterior '
                         'frente a la calculada, y la matriz de cambio de categoría.')
        panel.vincular(self.celda(g, 1, 2, 'comparacion.categoria_anterior', 'Columna de categoría anterior', 'combo',
                                  defecto='categ-anterior',
                                  ayuda='Columna del modelo con la categoría anterior (1 Medido, 2 Indicado, 3 Inferido, '
                                        '4 Potencial; cualquier otro valor = sin categoría).'))
        nota = QLabel('Compara con la categoría calculada (columna «Categoría» de arriba) sobre los mismos bloques: '
                      'mismo sólido, variable de minado y cut-off.')
        nota.setWordWrap(True)
        g.addWidget(nota, 1, 0, 1, 4)
        g = self.rejilla('Ganancia de recursos del plan de sondajes (hoja «Ganancia_Plan»)')
        self.celda(g, 0, 2, 'ganancia.activo', 'Calcular ganancia y ratio de conversión', 'bool', defecto=False,
                   ayuda='Agrega la hoja con la ganancia de tonelaje por categoría (categoría con el plan menos categoría '
                         'anterior) y el ratio de conversión = ganancia / longitud total del plan. Requiere la comparación '
                         'con la categoría anterior.')
        self.celda(g, 1, 2, 'ganancia.longitud_manual', 'Longitud del plan (m), vacío = del plan', 'txt', defecto='',
                   ayuda='Vacío: se toma la longitud total del Plan_Resumen.json que escribe el paso «plan». Con un '
                         'número se usa ese valor.')
        self.celda(g, 2, 2, 'ganancia.resumen_plan', 'Resumen del plan (Plan_Resumen.json)', 'archivo', filtro=
                   'JSON (*.json);;Todos los archivos (*)')
        nota = QLabel('Con la columna de categoría anterior de la comparación (p. ej. categ) y la categoría calculada '
                      '(«Categoría» de arriba, p. ej. categ_nuevo). Si el modelo trae <ley>_anterior (lo guarda la '
                      'estimación) se agregan la ley media y el metal fino anterior vs actual.')
        nota.setWordWrap(True)
        g.addWidget(nota, 3, 0, 1, 4)
        self.t_metales = self.tabla('Metales a reportar',
                                    [('Columna de ley', 'txt', None, 140), ('Etiqueta', 'txt', None, 90),
                                     ('Unidad', 'txt', None, 80), ('Factor', 'txt', None, 80)], altura=90,
                                    csv='reporte_metales.csv')

    def rutas_inexistentes(self):
        """El sólido solo es una entrada obligatoria si el reporte dentro de sólido está activo."""
        faltan = [c.leer() for k, c in self.campos.items()
                  if c.tipo == 'archivo' and k not in ('solido.ruta', 'ganancia.resumen_plan') and c.leer()
                  and not os.path.exists(c.leer())]
        c = self.campos
        ruta = c['solido.ruta'].leer()
        if c['solido.activo'].leer() and (not ruta or not os.path.exists(ruta)):
            faltan.append(ruta or '(sólido del reporte sin indicar)')
        return faltan

    def _cargar_extra(self, cfg):
        self.t_metales.set_filas([[m.get('columna'), m.get('etiqueta'), m.get('unidad'), m.get('factor')]
                                  for m in cfg.get('metales', [])])

    def _recolectar_extra(self, cfg):
        cfg['metales'] = [{'columna': c, 'etiqueta': e, 'unidad': u, 'factor': fac or '1'}
                          for c, e, u, fac in self.t_metales.filas() if c]
        import datetime
        try:
            datetime.datetime.strptime(cfg['fecha_reporte'], '%Y-%m-%d')
        except ValueError:
            raise ValueError("'fecha_reporte' debe tener el formato AAAA-MM-DD.") from None
        minado = cfg['minado']
        if minado['tipo'] not in TIPOS_MINADO:
            raise ValueError(f"'minado.tipo' debe ser {' o '.join(TIPOS_MINADO)}.")
        if minado['activo'] and not minado['variable']:
            raise ValueError("Indique la variable de minado o desmarque «Considerar variable de minado».")
        comparacion = cfg['comparacion']
        if comparacion['activo'] and not comparacion['categoria_anterior']:
            raise ValueError('Indique la columna de categoría anterior o desmarque «Comparar tonelajes».')
        ganancia = cfg['ganancia']
        if ganancia['activo'] and not comparacion['activo']:
            raise ValueError('La ganancia del plan necesita activar la comparación con la categoría anterior.')
        if ganancia['longitud_manual'] and not es_numero(ganancia['longitud_manual']):
            raise ValueError('La longitud manual del plan debe ser un número (o vacía para tomarla del plan).')
        if ganancia['activo'] and not ganancia['longitud_manual'] and not ganancia['resumen_plan']:
            raise ValueError('Indique el resumen del plan (Plan_Resumen.json) o una longitud manual para la ganancia.')


class PestanaEjecutar(QWidget):
    """Ejecuta EjecutarFlujo.py como subproceso y muestra su salida."""

    TITULO = 'Ejecutar'

    def __init__(self, ventana):
        super().__init__()
        self.ventana = ventana
        self.checks = {}
        caja = QGroupBox('Pasos del flujo a ejecutar')
        grid = QGridLayout(caja)
        for i, (clave, descripcion, _modulo, archivo) in enumerate(EjecutarFlujo.PASOS):
            cb = QCheckBox(f'{i + 1}. {descripcion}')
            cb.setChecked(True)
            cb.setToolTip(archivo)
            self.checks[clave] = cb
            grid.addWidget(cb, i // 2, i % 2)
        self.boton_ejecutar = QPushButton('Guardar y ejecutar los pasos marcados')
        self.boton_detener = QPushButton('Detener')
        self.boton_detener.setEnabled(False)
        todo, nada = QPushButton('Marcar todos'), QPushButton('Desmarcar todos')
        todo.clicked.connect(lambda: [c.setChecked(True) for c in self.checks.values()])
        nada.clicked.connect(lambda: [c.setChecked(False) for c in self.checks.values()])
        self.boton_ejecutar.clicked.connect(self.ejecutar)
        self.boton_detener.clicked.connect(lambda: self.proc.kill())
        botones = QHBoxLayout()
        for b in (self.boton_ejecutar, self.boton_detener, todo, nada):
            botones.addWidget(b)
        botones.addStretch()
        self.salida = QPlainTextEdit()
        self.salida.setReadOnly(True)
        fuente = QFont('Consolas')
        fuente.setStyleHint(QFont.StyleHint.Monospace)
        self.salida.setFont(fuente)
        lay = QVBoxLayout(self)
        lay.addWidget(caja)
        lay.addLayout(botones)
        lay.addWidget(self.salida, 1)

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyRead.connect(self._leer)
        self.proc.finished.connect(self._terminado)

    def ejecutar(self):
        claves = [c for c, cb in self.checks.items() if cb.isChecked()]
        if not claves:
            QMessageBox.information(self, 'Ejecutar', 'Marque al menos un paso.')
            return
        if not self.ventana.guardar_todo():
            return
        self.salida.clear()
        entorno = QProcessEnvironment.systemEnvironment()
        entorno.insert('PYTHONIOENCODING', 'utf-8')
        entorno.insert('PYTHONUNBUFFERED', '1')
        self.proc.setProcessEnvironment(entorno)
        self.proc.setWorkingDirectory(AQUI)
        self.proc.start(sys.executable, [os.path.join(AQUI, 'EjecutarFlujo.py'),
                                         '--config-dir', self.ventana.config_dir, '--pasos', ','.join(claves)])
        self.boton_ejecutar.setEnabled(False)
        self.boton_detener.setEnabled(True)

    def _leer(self):
        texto = bytes(self.proc.readAll()).decode('utf-8', errors='replace')
        self.salida.moveCursor(QTextCursor.MoveOperation.End)
        self.salida.insertPlainText(texto)
        self.salida.moveCursor(QTextCursor.MoveOperation.End)

    def _terminado(self, codigo, _estado):
        self._leer()
        self.salida.appendPlainText(f'\n[Proceso terminado con código {codigo}]')
        self.boton_ejecutar.setEnabled(True)
        self.boton_detener.setEnabled(False)


# ==================================================================================================
# Ventana principal
# ==================================================================================================
class Ventana(QMainWindow):
    def __init__(self, config_dir):
        super().__init__()
        self.config_dir = os.path.abspath(config_dir)
        self.setWindowTitle('Parámetros del flujo de estimación')
        self.resize(1180, 820)

        self.pestanas = [PestanaProyecto(), PestanaImportar(), PestanaCodificar(), PestanaAcotamiento(),
                         PestanaCompositacion(), PestanaPlan(), PestanaModeloBloques(), PestanaRecorte(),
                         PestanaEstimacion(),
                         PestanaValorizacion(), PestanaCategorizacion(), PestanaReporte()]
        self.por_archivo = {p.ARCHIVO: p for p in self.pestanas}
        self.tabs = QTabWidget()
        for p in self.pestanas:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(p)
            self.tabs.addTab(scroll, p.TITULO)
        self.pestana_ejecutar = PestanaEjecutar(self)
        self.tabs.addTab(self.pestana_ejecutar, PestanaEjecutar.TITULO)
        proyecto = self.por_archivo['conf_proyecto.json']
        proyecto.boton_aplicar.clicked.connect(self.aplicar_rutas)
        proyecto.campos['crear_modelo_bloques'].widget.stateChanged.connect(self.actualizar_paso_modelo)
        self.por_archivo['conf_importar_bd.json'].radio_compositos.toggled.connect(self.actualizar_pasos_compositos)
        proyecto.campos['valorizar_modelo'].widget.stateChanged.connect(self.actualizar_paso_valorizar)
        self.por_archivo['conf_plan.json'].campos['activo'].widget.stateChanged.connect(self.actualizar_paso_plan)
        self._cargando = False
        self.por_archivo['conf_recorte.json'].campos['activo'].widget.stateChanged.connect(self.al_cambiar_recorte)
        # «Identificar esquema» usa el modelo existente de «Rutas del flujo» si no se indica otro CSV.
        self.por_archivo['conf_crear_mb.json'].modelo_existente = \
            lambda: proyecto.campos['modelo_bloques_existente'].leer()
        # El modelo recortado aún no existe antes de correr el flujo: las columnas de bloque se leen del modelo completo.
        self.por_archivo['conf_estimacion.json'].paneles[0].ruta_respaldo = \
            lambda: self.por_archivo['conf_recorte.json'].campos['modelo'].leer()

        barra = QHBoxLayout()
        self.lbl_dir = QLabel()
        b_guardar, b_recargar, b_dir = (QPushButton('Guardar todo'), QPushButton('Recargar desde disco'),
                                        QPushButton('Cambiar carpeta…'))
        b_exp, b_imp = QPushButton('Exportar tablas a CSV'), QPushButton('Importar tablas desde CSV')
        b_exp.setToolTip('Guarda todas las tablas de parámetros en la carpeta 01_Data\\04_Parametros_Entrada.')
        b_imp.setToolTip('Carga las tablas desde los CSV de 01_Data\\04_Parametros_Entrada (los que existan).')
        b_guardar.clicked.connect(self.guardar_todo)
        b_recargar.clicked.connect(self.cargar_todo)
        b_dir.clicked.connect(self.cambiar_dir)
        b_exp.clicked.connect(self.exportar_tablas_csv)
        b_imp.clicked.connect(self.importar_tablas_csv)
        barra.addWidget(QLabel('Configuraciones:'))
        barra.addWidget(self.lbl_dir, 1)
        for b in (b_dir, b_recargar, b_exp, b_imp, b_guardar):
            barra.addWidget(b)
        centro = QWidget()
        lay = QVBoxLayout(centro)
        lay.addLayout(barra)
        lay.addWidget(self.tabs)
        self.setCentralWidget(centro)
        self.cargar_todo()

    def cargar_todo(self):
        self.lbl_dir.setText(self.config_dir)
        errores = []
        self._cargando = True   # cargar un JSON no debe disparar los enlaces entre pestañas
        for p in self.pestanas:
            try:
                p.cargar(self.config_dir)
            except Exception as ex:   # noqa: BLE001
                errores.append(f'{p.ARCHIVO}: {ex}')
        self._cargando = False
        if errores:
            QMessageBox.warning(self, 'Carga', 'No se pudo cargar:\n' + '\n'.join(errores))
        self.fijar_carpeta_csv()
        self.actualizar_paso_modelo()
        self.actualizar_pasos_compositos()
        self.actualizar_paso_valorizar()
        self.actualizar_paso_plan()
        self.actualizar_paso_recortar()
        for p in self.pestanas:       # columnas de los CSV en los paneles (ya con las rutas de respaldo enlazadas)
            for panel in p.paneles:
                panel.leer(silencioso=True)
        self.statusBar().showMessage(f'Configuraciones cargadas de {self.config_dir}', 5000)

    def fijar_carpeta_csv(self):
        """Carpeta de los CSV de parámetros: <raíz del proyecto>\\01_Data\\04_Parametros_Entrada."""
        raiz = self.por_archivo['conf_proyecto.json'].campos['raiz'].leer().strip()
        if not raiz or not os.path.isdir(raiz):
            raiz = os.path.normpath(os.path.join(self.config_dir, '..'))
        TablaEditable.CARPETA_CSV = os.path.join(raiz, '01_Data', '04_Parametros_Entrada')

    def actualizar_paso_modelo(self, *_):
        """El paso «modelo» queda marcado en la pestaña Ejecutar solo si se crea el modelo de bloques."""
        crear = self.por_archivo['conf_proyecto.json'].campos['crear_modelo_bloques'].leer()
        cb = self.pestana_ejecutar.checks['modelo']
        cb.setChecked(crear)
        n = [p[0] for p in EjecutarFlujo.PASOS].index('modelo') + 1
        descripcion = [p[1] for p in EjecutarFlujo.PASOS if p[0] == 'modelo'][0]
        cb.setText(f'{n}. {descripcion}' + ('' if crear else ' (omitido: se usa el modelo existente)'))

    def _marcar_paso(self, clave, activo, motivo):
        """Marca o desmarca un paso en la pestaña Ejecutar y aclara en su texto por qué se omite."""
        cb = self.pestana_ejecutar.checks[clave]
        cb.setChecked(activo)
        n = [p[0] for p in EjecutarFlujo.PASOS].index(clave) + 1
        descripcion = [p[1] for p in EjecutarFlujo.PASOS if p[0] == clave][0]
        cb.setText(f'{n}. {descripcion}' + ('' if activo else f' (omitido: {motivo})'))

    def actualizar_paso_valorizar(self, *_):
        """El paso «valorizar» queda marcado solo si se valoriza el modelo en el flujo (Rutas del flujo)."""
        self._marcar_paso('valorizar', self.por_archivo['conf_proyecto.json'].campos['valorizar_modelo'].leer(),
                          'el modelo no se valoriza')

    def actualizar_paso_plan(self, *_):
        """El paso «plan» queda marcado solo si el plan de sondajes está activo."""
        self._marcar_paso('plan', self.por_archivo['conf_plan.json'].campos['activo'].leer(),
                          'plan de sondajes desactivado')

    def actualizar_paso_recortar(self, *_):
        """El paso «recortar» queda marcado solo si el recorte del modelo está activo."""
        self._marcar_paso('recortar', self.por_archivo['conf_recorte.json'].campos['activo'].leer(),
                          'recorte desactivado')

    def al_cambiar_recorte(self, *_):
        """Al activar/desactivar el recorte (a mano), la estimación pasa a leer el modelo recortado / el completo."""
        self.actualizar_paso_recortar()
        if self._cargando:
            return
        rec = self.por_archivo['conf_recorte.json'].campos
        entrada = rec['salida'].leer() if rec['activo'].leer() else rec['modelo'].leer()
        if entrada:
            self.por_archivo['conf_estimacion.json'].campos['modelo'].escribir(os.path.normpath(entrada))
            self.statusBar().showMessage('La estimación lee ahora: ' + os.path.normpath(entrada)
                                         + ' (falta «Guardar todo»).', 8000)

    def actualizar_pasos_compositos(self, *_):
        """Con origen «compósitos» (Importar BD) los pasos codificar, acotar y compositar quedan desmarcados:
        el CSV ya viene codificado y compositado."""
        comp = self.por_archivo['conf_importar_bd.json'].es_compositos()
        descripciones = {p[0]: p[1] for p in EjecutarFlujo.PASOS}
        for n, (clave, _d, _m, _a) in enumerate(EjecutarFlujo.PASOS, 1):
            if clave in ('codificar', 'acotar', 'compositar'):
                cb = self.pestana_ejecutar.checks[clave]
                cb.setChecked(not comp)
                cb.setText(f'{n}. {descripciones[clave]}' + (' (omitido: compósitos ya hechos)' if comp else ''))

    def exportar_tablas_csv(self):
        """Escribe todas las tablas con CSV en la carpeta de parámetros de entrada."""
        self.fijar_carpeta_csv()
        escritos, errores = [], []
        for p in self.pestanas:
            for t in p.tablas_csv:
                try:
                    escritos.append(t.exportar_a(t.ruta_csv()))
                except OSError as ex:
                    errores.append(f'{t.csv}: {ex}')
        texto = f'{len(escritos)} tabla(s) exportadas a:\n{TablaEditable.CARPETA_CSV}'
        if errores:
            texto += '\n\nNo se pudo escribir (¿abiertos en Excel?):\n' + '\n'.join(errores)
        QMessageBox.information(self, 'Exportar tablas a CSV', texto)

    def importar_tablas_csv(self):
        """Carga cada tabla desde su CSV de la carpeta de parámetros, si existe (reemplaza sus filas)."""
        self.fijar_carpeta_csv()
        pendientes = [(p, t) for p in self.pestanas for t in p.tablas_csv if os.path.exists(t.ruta_csv())]
        if not pendientes:
            QMessageBox.information(self, 'Importar tablas desde CSV',
                                    f'No hay CSV de parámetros en:\n{TablaEditable.CARPETA_CSV}')
            return
        lista = '\n'.join(f'  [{p.TITULO}] {t.csv}' for p, t in pendientes)
        r = QMessageBox.question(self, 'Importar tablas desde CSV',
                                 f'Se reemplazará el contenido de estas tablas:\n\n{lista}\n\n¿Continuar?')
        if r != QMessageBox.StandardButton.Yes:
            return
        errores, nota = [], []
        for p, t in pendientes:
            try:
                n, faltantes, _ = t.importar_de(t.ruta_csv())
                if faltantes:
                    nota.append(f'{t.csv}: sin columnas {", ".join(faltantes)}')
            except Exception as ex:   # noqa: BLE001
                errores.append(f'{t.csv}: {ex}')
        texto = f'{len(pendientes) - len(errores)} tabla(s) importadas (falta «Guardar todo»).'
        if nota:
            texto += '\n\n' + '\n'.join(nota)
        if errores:
            texto += '\n\nErrores:\n' + '\n'.join(errores)
        QMessageBox.information(self, 'Importar tablas desde CSV', texto)

    def cambiar_dir(self):
        ruta = QFileDialog.getExistingDirectory(self, 'Carpeta de configuraciones', self.config_dir)
        if ruta:
            self.config_dir = os.path.abspath(ruta)
            self.cargar_todo()

    def guardar_todo(self):
        """Valida y escribe todos los JSON. Devuelve True si se guardó."""
        cfgs, errores = {}, []
        for p in self.pestanas:
            try:
                cfgs[p.ARCHIVO] = p.recolectar()
            except Exception as ex:   # noqa: BLE001
                errores.append(f'[{p.TITULO}] {ex}')
        if errores:
            QMessageBox.critical(self, 'Parámetros inválidos', 'No se guardó nada:\n\n' + '\n'.join(errores))
            return False
        faltan = [(p.TITULO, r) for p in self.pestanas for r in p.rutas_inexistentes()]
        avisos = [(p.TITULO, a) for p in self.pestanas for a in p.advertencias()]
        avisos += [('Recorte del modelo', a) for a in self._avisos_recorte(cfgs)]
        avisos += [(p.TITULO, a) for p in self.pestanas for a in p.avisos_columnas()]
        if faltan or avisos:
            partes = []
            if faltan:
                partes.append('Estos archivos no existen:\n' + '\n'.join(f'  [{t}] {r}' for t, r in faltan[:15]))
            if avisos:
                partes.append('Advertencias:\n' + '\n'.join(f'  [{t}] {a}' for t, a in avisos[:15]))
            r = QMessageBox.question(self, 'Revisar antes de guardar',
                                     '\n\n'.join(partes) + '\n\n¿Guardar de todos modos?')
            if r != QMessageBox.StandardButton.Yes:
                return False
        os.makedirs(self.config_dir, exist_ok=True)
        for archivo, cfg in cfgs.items():
            with open(os.path.join(self.config_dir, archivo), 'w', encoding='utf-8', newline='\n') as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
                f.write('\n')
        for p in self.pestanas:
            p.cfg = cfgs[p.ARCHIVO]
        self.statusBar().showMessage(f'{len(cfgs)} configuraciones guardadas en {self.config_dir}', 6000)
        return True

    @staticmethod
    def _avisos_recorte(cfgs):
        """Con el recorte activo, la estimación debe leer el CSV recortado (y no el modelo completo)."""
        rec, est = cfgs.get('conf_recorte.json', {}), cfgs.get('conf_estimacion.json', {})
        if not rec.get('activo'):
            return []
        norm = lambda r: os.path.normcase(os.path.normpath(str(r or '')))   # noqa: E731
        if norm(est.get('modelo')) != norm(rec.get('salida')):
            return ['La estimación no lee el modelo recortado: usa ' + str(est.get('modelo')) + ' en vez de '
                    + str(rec.get('salida')) + '. Se estimará todo el modelo.']
        return []

    def aplicar_rutas(self):
        """Propaga las rutas generales a las demás pestañas (sin guardar)."""
        pr = self.por_archivo['conf_proyecto.json'].campos
        try:
            db = pr['base_datos'].leer()
            carpeta_db = pr['carpeta_database'].leer()
            mb, rep = pr['carpeta_modelo_bloques'].leer(), pr['carpeta_reporte'].leer()
            nombre = pr['nombre_modelo'].leer() or 'modelo'
            crear = pr['crear_modelo_bloques'].leer()
            existente = pr['modelo_bloques_existente'].leer()
        except ValueError as ex:
            QMessageBox.warning(self, 'Rutas', str(ex))
            return
        modelo_creado = os.path.join(mb, f'{nombre}.csv')
        # La estimación parte del modelo creado en el flujo o, si no se crea, del modelo existente.
        modelo = modelo_creado if crear or not existente else existente
        recortado = os.path.join(mb, f'{nombre}_recortado.csv')
        recorta = self.por_archivo['conf_recorte.json'].campos['activo'].leer()
        estimado = os.path.join(mb, f'{nombre}_estimado.csv')
        valorizado = os.path.join(mb, f'{nombre}_valorizado.csv')
        valoriza = pr['valorizar_modelo'].leer()
        categorizado = os.path.join(mb, f'{nombre}_categorizado.csv')
        asignaciones = {
            'conf_importar_bd.json': {'proyecto': db, 'carpeta_csv_salida': carpeta_db},
            'conf_codificar.json': {'proyecto': db}, 'conf_acotamiento.json': {'proyecto': db},
            'conf_comp.json': {'proyecto': db},
            'conf_crear_mb.json': {'salida': modelo_creado},
            'conf_recorte.json': {'modelo': modelo, 'salida': recortado},
            'conf_estimacion.json': {'proyecto': db, 'modelo': recortado if recorta else modelo, 'salida': estimado},
            'conf_valorizacion.json': {'modelo': estimado, 'salida': valorizado},
            'conf_categ.json': {'modelo': valorizado if valoriza else estimado, 'salida': categorizado},
            'conf_reporte.json': {'modelo': categorizado, 'salida': rep,
                                  'ganancia.resumen_plan': os.path.join(carpeta_db, 'Plan_Resumen.json')},
            'conf_plan.json': {'proyecto': db, 'salida': carpeta_db, 'modelo.archivo': modelo},
        }
        for archivo, campos in asignaciones.items():
            for clave, valor in campos.items():
                self.por_archivo[archivo].campos[clave].escribir(os.path.normpath(valor))
        self.statusBar().showMessage('Rutas aplicadas a las pestañas (falta «Guardar todo»).', 8000)

    def closeEvent(self, evento):
        if self.pestana_ejecutar.proc.state() != QProcess.ProcessState.NotRunning:
            if QMessageBox.question(self, 'Salir', 'Hay un proceso en ejecución. ¿Detenerlo y salir?') \
                    != QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
            self.pestana_ejecutar.proc.kill()
        super().closeEvent(evento)


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    ventana = Ventana(argv[1] if len(argv) > 1 else CONFIG_DIR_DEFECTO)
    ventana.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
