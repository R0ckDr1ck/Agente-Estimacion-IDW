"""
Nombre : ProyectoSondajes.py
Propósito: Capa de acceso a datos (DAO) sobre una base de datos SQLite que consolida la información de
           sondajes de un proyecto (Collar, Survey y tablas intervalares como Assay, Litología, etc.),
           replicando el concepto de la base ISIS de Vulcan. No depende de Qt.

           Garantías de robustez:
             - Fidelidad de tipos: se persiste el dtype exacto de cada columna al importar y se reaplica
               al leer, para que pandas no degrade los tipos (p. ej. enteros con nulos -> float64).
             - Guardado seguro y atómico: prechequeos de estructura + escritura en tabla temporal y
               swap dentro de una transacción, de modo que un DataFrame vacío/corrupto nunca destruya
               la tabla previa.
Creacion : 09-Junio-2026
Autor : Eloy Paredes Quispe
Lugar : Buenaventura - Lima
Modificado : 11-Junio-2026 (tipo 'composito' para las tablas generadas por compositación)
Modificado :
"""

import os
import re
import json
import sqlite3
import datetime

import numpy as np
import pandas as pd

from Util.Const import Const


# Columnas requeridas (estandarizadas) por tipo de tabla. Son la última línea de defensa del DAO:
# si faltan, se aborta la escritura para no destruir datos existentes.
REQUERIDAS_COLLAR = ['Sondaje', 'Este', 'Norte', 'Cota']
REQUERIDAS_SURVEY = ['Sondaje', 'Profundidad', 'Azimut', 'Dip']
REQUERIDAS_INTERVALO = ['Sondaje', 'Desde', 'Hasta']

# Patrón para nombres válidos de tablas intervalares (identificadores SQL seguros)
PATRON_NOMBRE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


class ProyectoSondajes:
    """
    DAO de un proyecto de sondajes en SQLite. Uso típico:

        proy = ProyectoSondajes.crear('mina.ydb', nombre='Mina X')
        proy.guardar_collar(df_collar)
        proy.guardar_survey(df_survey)
        proy.guardar_intervalo('assay', df_assay)
        proy.cerrar()

        proy = ProyectoSondajes.abrir('mina.ydb')
        df = proy.get_intervalo('assay', sondajes=['DDH-001', 'DDH-002'])
    """

    TIPO_COLLAR = 'collar'
    TIPO_SURVEY = 'survey'
    TIPO_INTERVALO = 'intervalo'
    TIPO_COMPOSITO = 'composito'

    # Etiquetas de tipo de dato que el usuario puede fijar por columna en el importador. Viven aquí
    # (capa de almacenamiento) para que la etiqueta que ve el usuario y la que interpreta el guardado
    # sean literalmente la misma; la UI las reutiliza en su combo.
    TIPO_DATO_NUMERICO = 'Numérico'
    TIPO_DATO_TEXTO = 'Texto'

    NOMBRE_COLLAR = 'collar'
    NOMBRE_SURVEY = 'survey'

    # Nombres reservados que no pueden usarse como tabla intervalar de usuario
    NOMBRES_RESERVADOS = {'_proyecto', '_tablas', NOMBRE_COLLAR, NOMBRE_SURVEY}

    # Autodetección de columnas de atributo numéricas: fracción mínima de valores CON CONTENIDO
    # (no vacíos) que deben parsear como número para reclasificar una columna de texto como numérica.
    # Alto a propósito: solo actúa cuando la columna es claramente numérica (p. ej. una ley cargada
    # como texto por unas pocas celdas con texto o vacías), no cuando es ambigua o genuinamente
    # categórica (litología, alteración, códigos). Los vacíos no cuentan para decidir.
    UMBRAL_ATRIBUTO_NUMERICO = 0.9

    # ----------------------------------------------------------------------------------------------
    # Ciclo de vida
    # ----------------------------------------------------------------------------------------------
    def __init__(self, ruta):
        self.ruta = ruta
        # isolation_level=None -> modo autocommit; las transacciones se controlan manualmente con
        # BEGIN/COMMIT/ROLLBACK, lo que da control total sobre la atomicidad del swap (incluido DDL).
        self.con = sqlite3.connect(ruta, isolation_level=None)
        # Journal por defecto (rollback): el .ydb queda como un único archivo portable (estilo ISIS),
        # sin archivos -wal/-shm adjuntos que compliquen copiar/respaldar el proyecto.
        self._crear_esquema_base()

    @classmethod
    def crear(cls, ruta, nombre=None, unidades='m', sobrescribir=False):
        """Crea un proyecto nuevo. Si el archivo existe y sobrescribir es False, lanza FileExistsError."""
        if os.path.exists(ruta):
            if not sobrescribir:
                raise FileExistsError(f"El proyecto '{ruta}' ya existe.")
            os.remove(ruta)
        proy = cls(ruta)
        proy._set_meta('nombre', nombre or os.path.splitext(os.path.basename(ruta))[0])
        proy._set_meta('unidades', unidades)
        proy._set_meta('fecha_creacion', datetime.datetime.now().isoformat())
        proy._set_meta('version_app', Const.VER)
        return proy

    @classmethod
    def abrir(cls, ruta):
        """Abre un proyecto existente."""
        if not os.path.exists(ruta):
            raise FileNotFoundError(f"No se encontró el proyecto: {ruta}")
        return cls(ruta)

    def cerrar(self):
        if self.con is not None:
            self.con.close()
            self.con = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cerrar()

    # ----------------------------------------------------------------------------------------------
    # Esquema base y metadatos del proyecto
    # ----------------------------------------------------------------------------------------------
    def _crear_esquema_base(self):
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS _proyecto (clave TEXT PRIMARY KEY, valor TEXT)')
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS _tablas ('
            ' nombre TEXT PRIMARY KEY,'
            ' tipo TEXT,'
            ' rol_sondaje TEXT, rol_desde TEXT, rol_hasta TEXT,'
            ' columnas_originales TEXT,'
            ' dtypes TEXT,'
            ' archivo_origen TEXT, fecha_import TEXT,'
            ' n_filas INTEGER, n_sondajes INTEGER)')
        # Topes de acotamiento de altas leyes (capping/top-cut), uno por tabla+variable+dominio.
        # Es la "receta" del corte; la ley acotada se materializa como columna <variable>_ACOT en
        # la propia tabla de muestras. Idempotente: no afecta proyectos creados antes de esta versión.
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS _acotamiento ('
            ' tabla TEXT,'
            ' variable TEXT,'
            ' dominio TEXT,'
            ' tope REAL,'
            ' metodo TEXT,'
            ' parametros TEXT,'
            ' campo_salida TEXT,'
            ' n_afectados INTEGER,'
            ' pct_metal REAL,'
            ' fecha TEXT,'
            ' version_app TEXT,'
            ' PRIMARY KEY (tabla, variable, dominio))')
        # Veredicto de consistencia por sondaje (paso no destructivo). No modifica las tablas de
        # datos: solo registra si cada sondaje de Collar es apto para el flujo de estimación y por
        # qué. Es recomputable y reversible. Idempotente: no afecta proyectos previos a esta versión.
        self.con.execute(
            'CREATE TABLE IF NOT EXISTS _consistencia ('
            ' sondaje TEXT PRIMARY KEY,'
            ' estimable INTEGER,'
            ' motivo TEXT,'
            ' n_survey INTEGER,'
            ' n_muestras INTEGER,'
            ' fecha TEXT)')

    def _set_meta(self, clave, valor):
        self.con.execute('INSERT OR REPLACE INTO _proyecto (clave, valor) VALUES (?, ?)',
                         (clave, str(valor) if valor is not None else None))

    def get_meta(self, clave, defecto=None):
        cur = self.con.execute('SELECT valor FROM _proyecto WHERE clave = ?', (clave,))
        fila = cur.fetchone()
        return fila[0] if fila is not None else defecto

    # ----------------------------------------------------------------------------------------------
    # Guardado (con prechequeos + escritura atómica)
    # ----------------------------------------------------------------------------------------------
    def guardar_collar(self, df, archivo_origen='', roles=None, columnas_originales=None, tipos=None):
        self._validar_estructura(df, REQUERIDAS_COLLAR, 'Collar')
        df = self._forzar_tipos(df.copy(), self.TIPO_COLLAR, tipos)
        self._guardar_tabla(self.NOMBRE_COLLAR, df, self.TIPO_COLLAR,
                            archivo_origen, roles, columnas_originales)

    def guardar_survey(self, df, archivo_origen='', roles=None, columnas_originales=None, tipos=None):
        self._validar_estructura(df, REQUERIDAS_SURVEY, 'Survey')
        df = self._forzar_tipos(df.copy(), self.TIPO_SURVEY, tipos)
        self._guardar_tabla(self.NOMBRE_SURVEY, df, self.TIPO_SURVEY,
                            archivo_origen, roles, columnas_originales)

    def guardar_intervalo(self, nombre, df, archivo_origen='', roles=None, columnas_originales=None,
                          tipos=None):
        nombre = self._validar_nombre(nombre)
        self._validar_estructura(df, REQUERIDAS_INTERVALO, f"Intervalo '{nombre}'")
        df = self._forzar_tipos(df.copy(), self.TIPO_INTERVALO, tipos)
        self._guardar_tabla(nombre, df, self.TIPO_INTERVALO,
                            archivo_origen, roles, columnas_originales)

    def guardar_composito(self, nombre, df, archivo_origen='', roles=None, columnas_originales=None,
                          tipos=None):
        """
        Guarda una tabla de compósito (resultado del módulo de Compositación). Estructuralmente es
        una tabla intervalar, pero se registra con tipo propio para distinguirla de las tablas
        importadas: no aparece como tabla de muestras candidata y es la única clase de tabla que el
        módulo de Compositación puede eliminar. `tipos` fija el tipo de columnas de atributo
        ({columna: 'Numérico'|'Texto'}) cuando el compósito viene de un CSV ya hecho (ImportarBD).
        """
        nombre = self._validar_nombre(nombre)
        self._validar_estructura(df, REQUERIDAS_INTERVALO, f"Compósito '{nombre}'")
        df = self._forzar_tipos(df.copy(), self.TIPO_INTERVALO, tipos)
        self._guardar_tabla(nombre, df, self.TIPO_COMPOSITO,
                            archivo_origen, roles, columnas_originales)

    def actualizar_tabla(self, nombre, df):
        """
        Reescribe in-place una tabla intervalar o de compósito existente preservando sus metadatos
        (tipo, roles, columnas_originales, archivo_origen); solo actualiza dtypes, n_filas,
        n_sondajes y fecha_import. Pensado para módulos que agregan/modifican columnas sobre una
        tabla ya registrada (p. ej. Codificación de Sondajes). Collar y Survey no son actualizables
        desde aquí: se administran con sus métodos de guardado propios.
        """
        tipo = self.tipo_tabla(nombre)
        if tipo not in (self.TIPO_INTERVALO, self.TIPO_COMPOSITO):
            raise ValueError(
                f"'{nombre}' no es una tabla intervalar o de compósito actualizable (tipo: {tipo}).")
        self._validar_estructura(df, REQUERIDAS_INTERVALO, f"Tabla '{nombre}'")
        # Fidelidad de tipos: al reescribir se PRESERVA el tipo actual de cada columna de atributo
        # (derivado de su dtype) en vez de volver a autodetectar. Sin esto, una columna de texto con
        # valores enteros-en-apariencia (p. ej. SAMPLE, un identificador) se degradaría a número en la
        # primera pasada de Codificación/Acotamiento, rompiendo la garantía de fidelidad de tipos.
        tipos_actuales = self._tipos_desde_dtypes(df)
        df = self._forzar_tipos(df.copy(), self.TIPO_INTERVALO, tipos_usuario=tipos_actuales)

        # Mismo staging + swap atómico de _guardar_tabla, pero con UPDATE parcial de metadatos.
        tmp = nombre + '__tmp'
        self.con.isolation_level = ''
        try:
            df.to_sql(tmp, self.con, if_exists='replace', index=False)
        finally:
            self.con.isolation_level = None

        try:
            self.con.execute('BEGIN')
            self.con.execute(f'DROP TABLE IF EXISTS "{nombre}"')
            self.con.execute(f'ALTER TABLE "{tmp}" RENAME TO "{nombre}"')
            self.con.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{nombre}_sondaje" ON "{nombre}" ("Sondaje")')
            n_sondajes = int(df['Sondaje'].nunique()) if 'Sondaje' in df.columns else 0
            self.con.execute(
                'UPDATE _tablas SET dtypes = ?, n_filas = ?, n_sondajes = ?, fecha_import = ? '
                'WHERE nombre = ?',
                (self._serializar_dtypes(df), int(len(df)), n_sondajes,
                 datetime.datetime.now().isoformat(), nombre))
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            self.con.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            raise

    def _guardar_tabla(self, nombre, df, tipo, archivo_origen, roles, columnas_originales):
        """
        Escribe la tabla de forma atómica: primero a una tabla temporal y luego, dentro de una
        transacción, reemplaza la tabla destino y actualiza los metadatos. Si algo falla se hace
        ROLLBACK y la tabla original permanece intacta.
        """
        tmp = nombre + '__tmp'

        # 1. Escribir el staging fuera de la transacción de swap (no toca la tabla viva).
        #    En modo autocommit (isolation_level=None) cada INSERT de to_sql haría su propio
        #    fsync a disco (~185 filas/s: minutos u horas para tablas grandes). Se restaura
        #    temporalmente el isolation_level por defecto para que sqlite3 agrupe todos los
        #    INSERT del staging en una sola transacción.
        self.con.isolation_level = ''
        try:
            df.to_sql(tmp, self.con, if_exists='replace', index=False)
        finally:
            self.con.isolation_level = None

        # 2. Swap atómico.
        try:
            self.con.execute('BEGIN')
            self.con.execute(f'DROP TABLE IF EXISTS "{nombre}"')
            self.con.execute(f'ALTER TABLE "{tmp}" RENAME TO "{nombre}"')
            self.con.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{nombre}_sondaje" ON "{nombre}" ("Sondaje")')
            self._registrar_tabla(nombre, df, tipo, archivo_origen, roles, columnas_originales)
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            # Limpiar el staging si quedó colgado tras el rollback.
            self.con.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            raise

    def _registrar_tabla(self, nombre, df, tipo, archivo_origen, roles, columnas_originales):
        roles = roles or {}
        n_sondajes = int(df['Sondaje'].nunique()) if 'Sondaje' in df.columns else 0
        self.con.execute(
            'INSERT OR REPLACE INTO _tablas '
            '(nombre, tipo, rol_sondaje, rol_desde, rol_hasta, columnas_originales, dtypes, '
            ' archivo_origen, fecha_import, n_filas, n_sondajes) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (nombre, tipo,
             roles.get('Sondaje'), roles.get('Desde'), roles.get('Hasta'),
             json.dumps(columnas_originales or {}, ensure_ascii=False),
             self._serializar_dtypes(df),
             archivo_origen, datetime.datetime.now().isoformat(),
             int(len(df)), n_sondajes))

    # ----------------------------------------------------------------------------------------------
    # Lectura (reaplica los dtypes originales)
    # ----------------------------------------------------------------------------------------------
    def get_collar(self):
        df = pd.read_sql_query(f'SELECT * FROM "{self.NOMBRE_COLLAR}"', self.con)
        return self._aplicar_dtypes(df, self._dtypes_de(self.NOMBRE_COLLAR))

    def get_survey(self):
        df = pd.read_sql_query(
            f'SELECT * FROM "{self.NOMBRE_SURVEY}" ORDER BY "Sondaje", "Profundidad"', self.con)
        return self._aplicar_dtypes(df, self._dtypes_de(self.NOMBRE_SURVEY))

    def get_intervalo(self, nombre, sondajes=None):
        if not self.existe_tabla(nombre):
            raise ValueError(f"La tabla intervalar '{nombre}' no existe en el proyecto.")
        if sondajes:
            marcadores = ','.join('?' * len(sondajes))
            consulta = (f'SELECT * FROM "{nombre}" WHERE "Sondaje" IN ({marcadores}) '
                        f'ORDER BY "Sondaje", "Desde"')
            df = pd.read_sql_query(consulta, self.con, params=[str(s) for s in sondajes])
        else:
            df = pd.read_sql_query(f'SELECT * FROM "{nombre}" ORDER BY "Sondaje", "Desde"', self.con)
        return self._aplicar_dtypes(df, self._dtypes_de(nombre))

    # ----------------------------------------------------------------------------------------------
    # Consultas de catálogo
    # ----------------------------------------------------------------------------------------------
    def listar_tablas(self):
        """
        Devuelve una lista de diccionarios con los metadatos de todas las tablas registradas,
        en orden jerárquico estilo ISIS: collar, survey, las intervalares y al final los
        compósitos, cada grupo ordenado por nombre.
        """
        cur = self.con.execute(
            'SELECT nombre, tipo, archivo_origen, fecha_import, n_filas, n_sondajes '
            'FROM _tablas '
            "ORDER BY CASE tipo WHEN 'collar' THEN 0 WHEN 'survey' THEN 1 "
            "WHEN 'intervalo' THEN 2 ELSE 3 END, nombre")
        columnas = [d[0] for d in cur.description]
        return [dict(zip(columnas, fila)) for fila in cur.fetchall()]

    def listar_intervalos(self):
        """Tablas intervalares importadas (no incluye los compósitos generados)."""
        cur = self.con.execute(
            'SELECT nombre FROM _tablas WHERE tipo = ? ORDER BY nombre', (self.TIPO_INTERVALO,))
        return [fila[0] for fila in cur.fetchall()]

    def tipo_tabla(self, nombre):
        """Tipo registrado de una tabla ('collar', 'survey', 'intervalo', 'composito') o None."""
        cur = self.con.execute('SELECT tipo FROM _tablas WHERE nombre = ?', (nombre,))
        fila = cur.fetchone()
        return fila[0] if fila is not None else None

    def columnas(self, nombre):
        """Nombres de columna de una tabla (vía PRAGMA, sin cargar datos)."""
        cur = self.con.execute(f'PRAGMA table_info("{nombre}")')
        return [fila[1] for fila in cur.fetchall()]

    def dtypes(self, nombre):
        """Devuelve {columna: dtype_str} con los dtypes guardados de una tabla (sin cargar datos);
        diccionario vacío si no hay metadatos. Útil para conocer qué columnas son numéricas sin leer
        la tabla completa."""
        js = self._dtypes_de(nombre)
        try:
            return json.loads(js) if js else {}
        except (ValueError, TypeError):
            return {}

    def contar_valores_distintos(self, nombre, columna):
        """Número de valores distintos NO nulos de una columna y total de filas no nulas, vía SQL
        (sin cargar la tabla). Útil para validar de forma barata la cardinalidad de un campo antes
        de emprender un análisis pesado (p. ej. detectar que se eligió por error un identificador
        único por registro como campo de dominio). Devuelve (n_distintos, n_no_nulos); (0, 0) si la
        tabla o la columna no existen."""
        if not self.existe_tabla(nombre) or columna not in self.columnas(nombre):
            return 0, 0
        cur = self.con.execute(
            f'SELECT COUNT(DISTINCT "{columna}"), COUNT("{columna}") FROM "{nombre}"')
        fila = cur.fetchone()
        return (int(fila[0]), int(fila[1])) if fila else (0, 0)

    def existe_tabla(self, nombre):
        cur = self.con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (nombre,))
        return cur.fetchone() is not None

    def eliminar_tabla(self, nombre):
        """Elimina una tabla y su registro de metadatos de forma atómica."""
        if nombre in (self.NOMBRE_COLLAR, self.NOMBRE_SURVEY):
            tipo = nombre
        else:
            tipo = self.TIPO_INTERVALO
        _ = tipo  # nombre y tipo se conservan por claridad; no afecta el borrado
        try:
            self.con.execute('BEGIN')
            self.con.execute(f'DROP TABLE IF EXISTS "{nombre}"')
            self.con.execute('DELETE FROM _tablas WHERE nombre = ?', (nombre,))
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            raise

    def eliminar_sondajes(self, sondajes):
        """
        Elimina por completo uno o más sondajes del proyecto: borra sus filas de Collar, Survey y de
        TODAS las tablas intervalares y de compósito, y reajusta los metadatos (n_filas, n_sondajes)
        de cada tabla afectada. También limpia su veredicto en _consistencia. Atómico (todo o nada).

        Es la ÚNICA vía destructiva del DAO y es siempre EXPLÍCITA (la dispara el usuario, sondaje por
        sondaje, desde la pestaña Consistencia), a diferencia del resto de la capa que nunca destruye
        datos. Devuelve un dict {tabla: filas_borradas} con lo efectivamente eliminado.
        """
        sondajes = [str(s) for s in sondajes if str(s).strip()]
        if not sondajes:
            return {}
        marcadores = ','.join('?' * len(sondajes))
        borrados = {}
        try:
            self.con.execute('BEGIN')
            for meta in self.listar_tablas():
                nombre = meta['nombre']
                if 'Sondaje' not in self.columnas(nombre):
                    continue
                cur = self.con.execute(
                    f'DELETE FROM "{nombre}" WHERE "Sondaje" IN ({marcadores})', sondajes)
                if cur.rowcount:
                    borrados[nombre] = int(cur.rowcount)
                fila = self.con.execute(
                    f'SELECT COUNT(*), COUNT(DISTINCT "Sondaje") FROM "{nombre}"').fetchone()
                self.con.execute(
                    'UPDATE _tablas SET n_filas = ?, n_sondajes = ? WHERE nombre = ?',
                    (int(fila[0]), int(fila[1]), nombre))
            self.con.execute(
                f'DELETE FROM _consistencia WHERE sondaje IN ({marcadores})', sondajes)
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            raise
        return borrados

    def exportar_csv(self, nombre, ruta):
        """Exporta una tabla del proyecto a CSV (reaplicando los dtypes originales)."""
        if not self.existe_tabla(nombre):
            raise ValueError(f"La tabla '{nombre}' no existe en el proyecto.")
        df = pd.read_sql_query(f'SELECT * FROM "{nombre}"', self.con)
        df = self._aplicar_dtypes(df, self._dtypes_de(nombre))
        df.to_csv(ruta, index=False, encoding='utf-8-sig')
        return ruta

    # ----------------------------------------------------------------------------------------------
    # Acotamiento de altas leyes (topes por dominio)
    # ----------------------------------------------------------------------------------------------
    def guardar_acotamiento(self, tabla, variable, filas):
        """
        Persiste los topes de acotamiento de un par (tabla, variable): una fila por dominio.
        'filas' es una lista de dicts con claves: dominio, tope, metodo, parametros (dict|str|None),
        campo_salida, n_afectados, pct_metal. Reemplaza por completo los topes previos de ese
        (tabla, variable) dentro de una transacción, de modo que quitar un dominio del grid se refleje.
        Un 'tope' None/NaN persiste como NULL (dominio sin tope).
        """
        ahora = datetime.datetime.now().isoformat()
        try:
            self.con.execute('BEGIN')
            self.con.execute('DELETE FROM _acotamiento WHERE tabla = ? AND variable = ?',
                             (str(tabla), str(variable)))
            for f in filas:
                params = f.get('parametros')
                if isinstance(params, (dict, list)):
                    params = json.dumps(params, ensure_ascii=False)
                self.con.execute(
                    'INSERT OR REPLACE INTO _acotamiento '
                    '(tabla, variable, dominio, tope, metodo, parametros, campo_salida, '
                    ' n_afectados, pct_metal, fecha, version_app) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (str(tabla), str(variable), str(f.get('dominio')),
                     self._real_o_none(f.get('tope')), f.get('metodo'), params,
                     f.get('campo_salida'), self._entero_o_none(f.get('n_afectados')),
                     self._real_o_none(f.get('pct_metal')), ahora, Const.VER))
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            raise

    def get_acotamiento(self, tabla, variable=None):
        """Devuelve un DataFrame con los topes guardados de una tabla (opcionalmente de una sola
        variable), ordenado por variable y dominio. DataFrame vacío si no hay registros."""
        if variable is None:
            cur = self.con.execute(
                'SELECT * FROM _acotamiento WHERE tabla = ? ORDER BY variable, dominio',
                (str(tabla),))
        else:
            cur = self.con.execute(
                'SELECT * FROM _acotamiento WHERE tabla = ? AND variable = ? ORDER BY dominio',
                (str(tabla), str(variable)))
        columnas = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columnas)

    def listar_acotamiento(self, tabla=None):
        """Resumen (para auditoría) de qué pares tabla/variable tienen topes definidos: nº de
        dominios, nº con tope y fecha del último guardado."""
        base = ('SELECT tabla, variable, COUNT(*) AS n_dominios, '
                'SUM(CASE WHEN tope IS NOT NULL THEN 1 ELSE 0 END) AS n_con_tope, '
                'MAX(fecha) AS fecha FROM _acotamiento ')
        if tabla is None:
            cur = self.con.execute(base + 'GROUP BY tabla, variable ORDER BY tabla, variable')
        else:
            cur = self.con.execute(
                base + 'WHERE tabla = ? GROUP BY tabla, variable ORDER BY variable', (str(tabla),))
        columnas = [d[0] for d in cur.description]
        return [dict(zip(columnas, fila)) for fila in cur.fetchall()]

    def eliminar_acotamiento(self, tabla, variable=None):
        """Elimina los topes de una tabla (o solo de una variable) de forma atómica."""
        try:
            self.con.execute('BEGIN')
            if variable is None:
                self.con.execute('DELETE FROM _acotamiento WHERE tabla = ?', (str(tabla),))
            else:
                self.con.execute('DELETE FROM _acotamiento WHERE tabla = ? AND variable = ?',
                                 (str(tabla), str(variable)))
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            raise

    # ----------------------------------------------------------------------------------------------
    # Consistencia de la base (veredicto por sondaje, no destructivo)
    # ----------------------------------------------------------------------------------------------
    def evaluar_consistencia(self):
        """
        Evalúa la consistencia de TODO el proyecto (Collar + Survey + tablas intervalares) de una
        sola pasada y persiste un veredicto por sondaje en la tabla lateral _consistencia. NO es
        destructivo: no toca las tablas de datos, solo escribe metadatos recomputables.

        Devuelve (verdictos, errores): 'verdictos' clasifica cada sondaje de Collar como estimable o
        no y con qué motivo; 'errores' es la validación estructurada del conjunto para el reporte.
        """
        from Proyecto import ValidadorSondajes
        df_collar = self.get_collar() if self.existe_tabla(self.NOMBRE_COLLAR) else pd.DataFrame()
        df_survey = self.get_survey() if self.existe_tabla(self.NOMBRE_SURVEY) else pd.DataFrame()
        intervalos = {n: self.get_intervalo(n) for n in self.listar_intervalos()}

        verdictos, errores = ValidadorSondajes.clasificar_sondajes(df_collar, df_survey, intervalos)
        self.guardar_consistencia(verdictos)
        return verdictos, errores

    def guardar_consistencia(self, verdictos):
        """Reemplaza por completo el veredicto de consistencia (una fila por sondaje) de forma
        atómica, de modo que un sondaje retirado del Collar no quede como veredicto huérfano."""
        ahora = datetime.datetime.now().isoformat()
        try:
            self.con.execute('BEGIN')
            self.con.execute('DELETE FROM _consistencia')
            for v in verdictos:
                self.con.execute(
                    'INSERT OR REPLACE INTO _consistencia '
                    '(sondaje, estimable, motivo, n_survey, n_muestras, fecha) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (str(v.get('sondaje')), 1 if v.get('estimable') else 0,
                     v.get('motivo') or '', self._entero_o_none(v.get('n_survey')),
                     self._entero_o_none(v.get('n_muestras')), ahora))
            self.con.execute('COMMIT')
        except Exception:
            self.con.execute('ROLLBACK')
            raise

    def get_consistencia(self, solo_no_estimables=False):
        """DataFrame con el veredicto guardado (vacío si aún no se evaluó). Ordena primero todo lo
        que requiere atención: los sondajes con alguna observación (motivo no vacío, sean no
        estimables o estimables con advertencia) van arriba, y dentro de ellos los no estimables
        antes que las advertencias; los sondajes limpios quedan al final."""
        consulta = 'SELECT * FROM _consistencia'
        if solo_no_estimables:
            consulta += ' WHERE estimable = 0'
        consulta += " ORDER BY (motivo IS NULL OR motivo = '') ASC, estimable ASC, sondaje"
        cur = self.con.execute(consulta)
        columnas = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=columnas)

    def sondajes_estimables(self):
        """Lista de sondajes marcados como estimables en la última evaluación de consistencia
        PERSISTIDA (vacía si aún no se evaluó). Alimenta el reporte de la pestaña Consistencia.
        Para filtrar aguas abajo use calcular_estimables(), que no depende de que se haya pulsado
        'Verificar consistencia'."""
        cur = self.con.execute(
            'SELECT sondaje FROM _consistencia WHERE estimable = 1 ORDER BY sondaje')
        return [fila[0] for fila in cur.fetchall()]

    def calcular_estimables(self):
        """
        Conjunto (set de str) de sondajes estimables según el estado ACTUAL de Collar y Survey,
        calculado al vuelo y SIN persistir (no altera el veredicto guardado ni las tablas de datos).
        Aplica el mismo criterio que el flag de consistencia —coordenadas de Collar completas y al
        menos un registro de Survey— por lo que es coherente con la pestaña Consistencia, pero no
        exige haber pulsado 'Verificar consistencia' y siempre refleja el estado vigente.

        Es la fuente que deben usar los módulos aguas abajo (compositación, estimación, acotamiento)
        para operar solo con sondajes aptos. Devuelve un set vacío si no hay Collar/Survey; en ese
        caso quien filtre debe optar por no excluir nada (no se puede afirmar la aptitud).
        """
        from Proyecto import ValidadorSondajes
        df_collar = self.get_collar() if self.existe_tabla(self.NOMBRE_COLLAR) else pd.DataFrame()
        df_survey = self.get_survey() if self.existe_tabla(self.NOMBRE_SURVEY) else pd.DataFrame()
        verdictos, _ = ValidadorSondajes.clasificar_sondajes(df_collar, df_survey, {})
        return {v['sondaje'] for v in verdictos if v['estimable']}

    # ----------------------------------------------------------------------------------------------
    # Helpers internos
    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _real_o_none(x):
        """Convierte a float para SQLite; None y NaN -> NULL."""
        if x is None:
            return None
        try:
            f = float(x)
            return None if f != f else f  # f != f detecta NaN
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _entero_o_none(x):
        """Convierte a int para SQLite; None y NaN -> NULL."""
        if x is None:
            return None
        try:
            f = float(x)
            return None if f != f else int(f)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _forzar_tipos(df, tipo, tipos_usuario=None):
        """Fuerza el tipo de 'Sondaje' a texto y de las columnas canónicas numéricas correspondientes.
        Para las columnas de atributo respeta el tipo que el usuario haya fijado explícitamente en el
        importador (`tipos_usuario`: {columna: 'Numérico'|'Texto'}); las que no aparezcan ahí se
        autodetectan como siempre: se convierten a numérico si son mayoritariamente numéricas (p. ej.
        leyes cargadas como texto por unas pocas celdas con texto o vacías), dejando en NaN tanto los
        valores no numéricos como los vacíos."""
        if df.empty:
            return df
        tipos_usuario = tipos_usuario or {}
        if 'Sondaje' in df.columns:
            df['Sondaje'] = df['Sondaje'].astype(str).str.strip()

        numericas = []
        if tipo == ProyectoSondajes.TIPO_COLLAR:
            numericas = ['Este', 'Norte', 'Cota', 'Profundidad']
        elif tipo == ProyectoSondajes.TIPO_SURVEY:
            numericas = ['Profundidad', 'Azimut', 'Dip']
        elif tipo == ProyectoSondajes.TIPO_INTERVALO:
            numericas = ['Desde', 'Hasta']

        for col in numericas:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Columnas de atributo (todo lo que no sea 'Sondaje' ni una columna canónica ya forzada).
        # 'Sondaje' se preserva siempre como texto; las canónicas ya se coercionaron arriba. La
        # elección explícita del usuario tiene prioridad; en su ausencia, se autodetecta.
        estructurales = set(numericas) | {'Sondaje'}
        for col in df.columns:
            if col in estructurales:
                continue
            if col in tipos_usuario:
                df[col] = ProyectoSondajes._aplicar_tipo_usuario(df[col], tipos_usuario[col])
            else:
                df[col] = ProyectoSondajes._coercer_si_mayoritariamente_numerica(df[col])
        return df

    @staticmethod
    def _tipos_desde_dtypes(df):
        """Deriva {columna: 'Numérico'|'Texto'} a partir de los dtypes ACTUALES de un DataFrame, para
        preservar la fidelidad de tipos al reescribir una tabla existente (actualizar_tabla) sin
        volver a autodetectar. Cada columna de atributo se mapea según su dtype: numérica (int/float,
        incluidos los nullable) -> 'Numérico'; cualquier otra (object, etc.) -> 'Texto'. Así una
        columna de texto con valores enteros-en-apariencia conserva su naturaleza textual. Las
        canónicas y 'Sondaje' se incluyen sin efecto: _forzar_tipos las salta (su tipo lo fija el
        rol)."""
        tipos = {}
        for col in df.columns:
            es_num = (pd.api.types.is_numeric_dtype(df[col])
                      and not pd.api.types.is_bool_dtype(df[col]))
            tipos[col] = (ProyectoSondajes.TIPO_DATO_NUMERICO if es_num
                          else ProyectoSondajes.TIPO_DATO_TEXTO)
        return tipos

    @staticmethod
    def _aplicar_tipo_usuario(serie, tipo_deseado):
        """Aplica el tipo que el usuario fijó a mano para una columna de atributo. 'Numérico'
        coerciona a número (valores no numéricos y vacíos -> NaN); 'Texto' preserva el contenido como
        cadena (vacíos -> None; los enteros leídos como float se guardan sin el '.0' espurio) para que
        una columna numérica-en-apariencia (p. ej. un código categórico) no se degrade a número."""
        if tipo_deseado == ProyectoSondajes.TIPO_DATO_NUMERICO:
            return ProyectoSondajes._preservar_entero(pd.to_numeric(serie, errors='coerce'))

        def _a_texto(v):
            if pd.isna(v):
                return None
            if isinstance(v, float) and float(v).is_integer():
                return str(int(v))
            return str(v)
        return serie.map(_a_texto).astype('object')

    @staticmethod
    def _coercer_si_mayoritariamente_numerica(serie):
        """Convierte la serie a numérico si la mayor parte de sus valores CON CONTENIDO parsean como
        número; en caso contrario la deja intacta. En la versión convertida, los valores no numéricos
        y los vacíos quedan como NaN. No toca columnas ya numéricas ni las genuinamente textuales
        (códigos de litología, alteración, etc.), que se quedan como están."""
        # Ya numérica (o booleana): no hay nada que reinterpretar. Se sale antes del trabajo costoso.
        if pd.api.types.is_numeric_dtype(serie):
            return ProyectoSondajes._preservar_entero(serie)

        convertida = pd.to_numeric(serie, errors='coerce')

        # Valores "con contenido": no nulos y, como texto, no vacíos tras strip (ni el literal 'nan').
        # Los vacíos son neutros para decidir —serán NaN de todos modos— así que no cuentan.
        texto = serie.astype(str).str.strip()
        con_contenido = serie.notna() & (texto != '') & (texto.str.lower() != 'nan')
        total = int(con_contenido.sum())
        if total == 0:
            return serie  # columna sin datos con contenido: no se reinterpreta

        numericos = int((convertida.notna() & con_contenido).sum())
        if numericos / total >= ProyectoSondajes.UMBRAL_ATRIBUTO_NUMERICO:
            return ProyectoSondajes._preservar_entero(convertida)
        return serie

    @staticmethod
    def _preservar_entero(serie_num):
        """Conserva la naturaleza entera de una columna numérica de atributo: si TODOS sus valores
        presentes son enteros (típicamente un conteo o un código con algunas celdas vacías, que pandas
        promueve a float64 por los NaN), la convierte a Int64 nullable para no arrastrar el '.0'
        espurio ni perder que son enteros. Si hay algún decimal real, un infinito o no hay datos, la
        deja tal cual (float64). No se aplica a las columnas canónicas (coordenadas, profundidad,
        ángulos, desde/hasta), que se mantienen como flotantes por ser magnitudes continuas."""
        if not pd.api.types.is_numeric_dtype(serie_num) or pd.api.types.is_bool_dtype(serie_num):
            return serie_num
        if pd.api.types.is_integer_dtype(serie_num):
            return serie_num  # ya es entera (int64/Int64): nada que hacer
        validos = serie_num.dropna()
        if validos.empty:
            return serie_num
        if np.isfinite(validos).all() and (validos == validos.round()).all():
            return serie_num.astype('Int64')
        return serie_num

    @staticmethod
    def _validar_estructura(df, requeridas, contexto):
        """Prechequeos: aborta si el DataFrame es None, está vacío o le faltan columnas requeridas."""
        if df is None:
            raise ValueError(f"{contexto}: el DataFrame es None; se aborta la escritura.")
        if df.empty:
            raise ValueError(f"{contexto}: el DataFrame está vacío; se aborta para no destruir datos existentes.")
        faltantes = [c for c in requeridas if c not in df.columns]
        if faltantes:
            raise ValueError(f"{contexto}: faltan columnas requeridas: {faltantes}.")

    def _validar_nombre(self, nombre):
        """Valida el nombre de una tabla intervalar (identificador SQL seguro y no reservado)."""
        nombre = str(nombre).strip()
        if not PATRON_NOMBRE.match(nombre):
            raise ValueError(
                f"Nombre de tabla inválido: '{nombre}'. Use solo letras, números y guion bajo, "
                "comenzando por una letra.")
        if nombre.lower() in self.NOMBRES_RESERVADOS:
            raise ValueError(f"El nombre '{nombre}' está reservado por el sistema.")
        return nombre

    @staticmethod
    def _serializar_dtypes(df):
        """Devuelve un JSON {columna: dtype} con los dtypes exactos del DataFrame."""
        return json.dumps({col: str(dt) for col, dt in df.dtypes.items()}, ensure_ascii=False)

    def _dtypes_de(self, nombre):
        cur = self.con.execute('SELECT dtypes FROM _tablas WHERE nombre = ?', (nombre,))
        fila = cur.fetchone()
        return fila[0] if fila is not None else None

    @staticmethod
    def _aplicar_dtypes(df, dtypes_json):
        """Reaplica los dtypes originales tras leer de SQLite, preservando la fidelidad de tipos."""
        if not dtypes_json:
            return df
        try:
            dtypes = json.loads(dtypes_json)
        except (ValueError, TypeError):
            return df

        for col, dt in dtypes.items():
            if col not in df.columns:
                continue
            try:
                if dt == 'Int64':
                    # Enteros con posibles nulos: pasar por float para absorber NaN y luego a Int64.
                    df[col] = df[col].astype('float64').astype('Int64')
                else:
                    df[col] = df[col].astype(dt)
            except (ValueError, TypeError):
                # Si la conversión no es posible, se deja la columna tal como vino de SQLite.
                pass
        return df
