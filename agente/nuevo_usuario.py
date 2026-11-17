"""Usuario nuevo: crea las carpetas de ENTRADA (01_Data) y de SALIDA (02_Resultados) con la estructura que esperan los conf.

Lo que hace:
  * crea  <destino>/<proyecto>/01_Data/{01_Database, 02_Modelo-Bloques, ..., 07_Sondajes_Nuevos}
          <destino>/<proyecto>/02_Resultados/{01_Database, 03_Modelo_Bloques, 04_Reporte}
  * deja un LEEME.txt en cada subcarpeta: qué archivos van ahí, con qué nombre y para qué ruta de trabajo
  * opcionalmente deja plantillas CSV (cabecera + filas ficticias) en  <destino>/<proyecto>/Plantillas_para_llenar/
    (FUERA de 01_Data a propósito: el agente reconoce los datos por sus cabeceras y una plantilla con filas de ejemplo
    se confundiría con datos reales)
  * guarda archivos subidos desde la interfaz en la subcarpeta elegida.

Nunca sobrescribe ni borra nada: si algo ya existe, lo deja como está y lo informa. Los nombres de subcarpeta son los que los
conf_*.json usan como «ancla» (01_Data, 02_Resultados), por eso no se pueden cambiar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ENTRADA = "01_Data"
SALIDA = "02_Resultados"
PLANTILLAS = "Plantillas_para_llenar"


@dataclass(frozen=True)
class Carpeta:
    nombre: str
    titulo: str
    que_va: str
    archivos: tuple[str, ...] = ()
    cuando: str = ""                 # para qué ruta de trabajo hace falta


GUIA_ENTRADA: tuple[Carpeta, ...] = (
    Carpeta("01_Database", "Base de datos de sondajes",
            "Los datos de perforación en CSV. Elige UNA de las dos formas: collar + survey + ensayos (estimar desde sondajes) "
            "o un solo CSV de compósitos ya codificados.",
            ("ddh_collar.csv", "ddh_survey.csv", "ddh_assay.csv", "o bien: assay_comp.csv"),
            "Estimar desde sondajes (los tres primeros) · Estimar desde compósitos (assay_comp.csv)"),
    Carpeta("02_Modelo-Bloques", "Modelo de bloques existente",
            "Un modelo de bloques en CSV (columnas XC, YC, ZC, DX, DY, DZ y atributos). Si el flujo va a CREAR el modelo, "
            "esta carpeta puede quedar vacía.",
            ("mb_<nombre>.csv",),
            "Estimar sobre un modelo ya hecho · recortar, valorizar, categorizar, reportar un modelo existente"),
    Carpeta("03_Dominio", "Sólidos de dominio",
            "Un sólido (.obj) por dominio geológico: sirven para codificar los sondajes y para crear el modelo de bloques.",
            ("21_DOMINIO_A.obj", "31_DOMINIO_B.obj", "Prioridad_Dominios.csv (opcional)"),
            "Codificar sondajes · crear el modelo de bloques"),
    Carpeta("04_Parametros_Entrada", "Tablas de parámetros",
            "Tablas CSV que se cargan a los conf (sólidos de codificación, topes de altas leyes, pasadas IDW, metales…). "
            "Se llenan desde la pestaña «Parámetros» o a mano con las plantillas.",
            ("codificacion_solidos.csv", "acotamiento_topes.csv", "modelo_bloques_solidos.csv", "modelo_bloques_variables.csv",
             "modelo_bloques_excepciones.csv", "estimacion_pasadas.csv", "valorizacion_metales.csv", "reporte_metales.csv"),
            "Casi todos los pasos usan alguna"),
    Carpeta("05_Objetivo", "Sólido objetivo",
            "El sólido (.obj) con el que se recorta el modelo de bloques o se limita el reporte (p. ej. la zona de óxidos).",
            ("OBJETIVO.obj",),
            "Recortar el modelo · reporte limitado a un sólido"),
    Carpeta("06_Topografia", "Topografías (opcional)",
            "Superficies topográficas (.obj) de referencia. El flujo actual no las lee; guárdalas aquí para tenerlas ordenadas.",
            ("TOPO_2025.obj",),
            "Opcional"),
    Carpeta("07_Sondajes_Nuevos", "Plan de sondajes",
            "Collares y survey de los sondajes que planeas perforar (para evaluar cuánto mejora la estimación).",
            ("Plan-DDH_collar.csv", "Plan-DDH_survey.csv"),
            "Evaluar un plan de sondajes"),
)

GUIA_SALIDA: tuple[Carpeta, ...] = (
    Carpeta("01_Database", "Base de datos y CSV exportados",
            "Aquí escribe el flujo la base Database.db, el log de importación y los CSV exportados. No pongas nada tú.",
            ("Database.db", "Importacion_Log.txt")),
    Carpeta("03_Modelo_Bloques", "Modelos de bloques generados",
            "El modelo creado, recortado, estimado, valorizado y categorizado (un CSV por paso).",
            ("mb_<nombre>_estimado.csv", "mb_<nombre>_categorizado.csv")),
    Carpeta("04_Reporte", "Reporte de recursos",
            "El reporte de recursos en Excel y sus CSV de apoyo. La pestaña «Reporte final» del agente lee esta carpeta.",
            ("Recursos_<...>.xlsx",)),
)


@dataclass
class Resultado:
    base: Path
    data_dir: Path
    results_dir: Path
    creadas: list[str] = field(default_factory=list)
    existentes: list[str] = field(default_factory=list)
    plantillas: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def nombre_valido(nombre: str) -> Optional[str]:
    """None si el nombre de proyecto sirve como carpeta; si no, el motivo."""
    n = (nombre or "").strip()
    if not n:
        return "Escribe un nombre para el proyecto."
    if n in {".", ".."} or _INVALIDO.search(n):
        return 'El nombre no puede llevar \\ / : * ? " < > | ni ser «.» o «..».'
    if n.rstrip(" .") != n:
        return "El nombre no puede terminar en punto ni en espacio."
    if n.split(".")[0].lower() in _RESERVADOS:
        return f"«{n}» es un nombre reservado de Windows."
    if len(n) > 80:
        return "El nombre es demasiado largo (máximo 80 caracteres)."
    return None


def _leeme(c: Carpeta, entrada: bool) -> str:
    L = [c.titulo.upper(), "=" * len(c.titulo), "", c.que_va, ""]
    if c.archivos:
        L += ["Archivos típicos:"] + [f"  - {a}" for a in c.archivos] + [""]
    if c.cuando:
        L += [f"Se usa en: {c.cuando}", ""]
    if entrada:
        L += ["Si no sabes cómo debe ser un archivo, en el agente abre la pestaña «Formatos y plantillas»: muestra las columnas",
              "y te deja descargar una plantilla. Este LEEME.txt lo puedes borrar cuando quieras; el agente no lo lee."]
    else:
        L += ["Carpeta de SALIDA: la llena el flujo al ejecutarse. Lo que ya exista y vaya a ser sobrescrito se te pregunta antes."]
    return "\n".join(L) + "\n"


def _mkdir(p: Path, r: Resultado) -> None:
    if p.is_dir():
        r.existentes.append(str(p))
        return
    p.mkdir(parents=True, exist_ok=True)
    r.creadas.append(str(p))


def _escribir(p: Path, texto: str, r: Resultado, *, lista: Optional[list[str]] = None) -> None:
    if p.exists():
        r.existentes.append(str(p))
        return
    p.write_text(texto, encoding="utf-8", newline="\n")
    (r.creadas if lista is None else lista).append(str(p))


def crear_estructura(destino: str | Path, proyecto: str = "Mi_Proyecto", *, plantillas: bool = False, registry=None) -> Resultado:
    """Crea <destino>/<proyecto>/{01_Data, 02_Resultados} (y opcionalmente Plantillas_para_llenar). Idempotente y sin sobrescribir.

    `registry` (ToolRegistry) aporta las plantillas CSV del catálogo; sin él no se generan plantillas."""
    dest = Path(str(destino).strip().strip('"')).expanduser()
    base = dest / (proyecto or "").strip()
    r = Resultado(base=base, data_dir=base / ENTRADA, results_dir=base / SALIDA)
    motivo = nombre_valido(proyecto)
    if motivo:
        r.errores.append(motivo)
        return r
    if not dest.is_absolute():
        r.errores.append("Escribe una ruta completa para la carpeta donde se creará el proyecto (p. ej. D:\\Proyectos).")
        return r
    if dest.exists() and not dest.is_dir():
        r.errores.append(f"«{dest}» es un archivo, no una carpeta.")
        return r
    try:
        _mkdir(base, r)
        _mkdir(r.data_dir, r)
        for c in GUIA_ENTRADA:
            _mkdir(r.data_dir / c.nombre, r)
            _escribir(r.data_dir / c.nombre / "LEEME.txt", _leeme(c, True), r)
        _mkdir(r.results_dir, r)
        for c in GUIA_SALIDA:
            _mkdir(r.results_dir / c.nombre, r)
            _escribir(r.results_dir / c.nombre / "LEEME.txt", _leeme(c, False), r)
        if plantillas:
            _escribir_plantillas(base / PLANTILLAS, r, registry)
    except OSError as e:
        r.errores.append(f"No pude crear las carpetas en «{dest}»: {e}")
    return r


def _escribir_plantillas(carpeta: Path, r: Resultado, registry) -> None:
    from .tablas import render_table_template
    from .templates import render_data_template
    if registry is None:
        r.errores.append("No hay catálogo cargado: no pude generar las plantillas.")
        return
    _mkdir(carpeta, r)
    guia = ["PLANTILLAS PARA LLENAR", "======================", "",
            "Cada CSV trae la cabecera correcta y 1-3 filas FICTICIAS. Reemplázalas por tus datos y copia el archivo a la carpeta",
            "indicada dentro de 01_Data (sin la palabra «plantilla» en el nombre). No las dejes aquí: esta carpeta es solo de ayuda.", ""]
    for tid, ft in sorted(registry.file_templates.items()):
        ft = ft if ft.id else ft.model_copy(update={"id": tid})
        csv_text, expl = render_data_template(ft)
        _escribir(carpeta / f"{tid}.csv", csv_text, r, lista=r.plantillas)
        _escribir(carpeta / f"{tid}.txt", expl, r, lista=r.plantillas)
        guia.append(f"  {tid}.csv  ->  {ENTRADA}/{ft.folder or '(según el caso)'}/")
    for tid, pt in sorted(registry.param_tables.items()):
        _escribir(carpeta / pt.file, render_table_template(pt), r, lista=r.plantillas)
        guia.append(f"  {pt.file}  ->  {ENTRADA}/{pt.folder}/   (o súbela en la pestaña «Parámetros»)")
    _escribir(carpeta / "LEEME.txt", "\n".join(guia) + "\n", r)


def subcarpetas_entrada(data_dir: str | Path) -> list[str]:
    """Subcarpetas de entrada que existen realmente dentro de `data_dir` (para elegir dónde subir un archivo)."""
    d = Path(data_dir)
    return [c.nombre for c in GUIA_ENTRADA if (d / c.nombre).is_dir()]


def guardar_subido(data_dir: str | Path, subcarpeta: str, nombre: str, contenido: bytes, *, sobrescribir: bool = False) -> tuple[Optional[Path], str]:
    """Guarda un archivo subido en data_dir/subcarpeta. Devuelve (ruta | None, mensaje). Solo nombres simples y subcarpetas conocidas;
    no sobrescribe salvo que se pida."""
    if subcarpeta not in {c.nombre for c in GUIA_ENTRADA}:
        return None, f"«{subcarpeta}» no es una subcarpeta de entrada."
    simple = re.split(r"[\\/]", nombre.strip())[-1]          # no Path(): en Windows «a:b.csv» se leería como unidad «a:»
    if not simple or simple in {".", ".."} or _INVALIDO.search(simple):
        return None, f"Nombre de archivo no válido: «{nombre}»."
    if Path(simple).suffix.lower() not in {".csv", ".obj", ".txt", ".json", ".xlsx", ".db"}:
        return None, f"«{simple}»: solo se aceptan .csv, .obj, .txt, .json, .xlsx y .db."
    dest_dir = Path(data_dir) / subcarpeta
    if not dest_dir.is_dir():
        return None, f"La subcarpeta «{subcarpeta}» no existe en la carpeta de entrada."
    dest = dest_dir / simple
    if dest.exists() and not sobrescribir:
        return None, f"«{simple}» ya existe en {subcarpeta} (no se sobrescribe)."
    dest.write_bytes(contenido)
    return dest, f"Guardado en {subcarpeta}/{simple}"


def resumen_arbol(r: Resultado) -> str:
    """Árbol de texto de lo creado (para mostrar en la interfaz o la consola)."""
    def hijos(guia, pref):
        return [f"{pref}{'└' if i == len(guia) - 1 else '├'}── {c.nombre}/" for i, c in enumerate(guia)]
    ultimo = not r.plantillas
    L = [f"{r.base.name}/", f"├── {ENTRADA}/  (entrada)", *hijos(GUIA_ENTRADA, "│   "),
         f"{'└' if ultimo else '├'}── {SALIDA}/  (salida)", *hijos(GUIA_SALIDA, "    " if ultimo else "│   ")]
    if r.plantillas:
        L.append(f"└── {PLANTILLAS}/")
    return "\n".join(L)
