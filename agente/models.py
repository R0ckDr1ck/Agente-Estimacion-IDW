"""Modelos Pydantic: catálogo declarativo, salida del planner y estado de ejecución.

Tres familias:
  1. Catálogo   (Condition, PathRef, ParamSpec, ToolSpec, Catalog)  -> lo que se escribe en YAML.
  2. Planner    (PlannerOutput)                                      -> lo que debe devolver el SLM (JSON estricto).
  3. Ejecución  (Finding, PlanStep, RunState ...)                    -> lo que se guarda para reanudar.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    """Base del catálogo: un campo mal escrito en el YAML falla al cargar, no se ignora en silencio."""
    model_config = ConfigDict(extra="forbid")


# ============================================================================ 1. CATÁLOGO

class Condition(Strict):
    """Condición sobre un valor de un conf_*.json (p. ej. 'conf_recorte.json: activo es falso')."""
    config: Optional[str] = None          # None = el conf de la propia herramienta
    key: str                              # ruta JSON: 'a.b', 'lista[0].x'
    op: Literal["eq", "ne", "truthy", "falsy", "exists", "missing"] = "truthy"
    value: Any = None
    default: Any = None                   # valor asumido si el conf o la clave no existen


class PathRef(Strict):
    """Referencia a un archivo/carpeta que una herramienta lee o escribe. Exactamente UNA fuente de ruta."""
    name: str
    config: Optional[str] = None          # conf que contiene la clave (None = el de la herramienta)
    key: Optional[str] = None             # ruta JSON dentro del conf; admite [*] (todos los elementos)
    literal: Optional[str] = None         # ruta fija relativa a la raíz del proyecto
    param: Optional[str] = None           # la ruta viene de un parámetro de la herramienta
    join: Optional[str] = None            # se añade a la ruta (p. ej. carpeta + 'Plan_Resumen.json')
    kind: Literal["file", "dir"] = "file"
    when: list[Condition] = Field(default_factory=list)   # la referencia solo aplica si TODAS se cumplen
    description: str = ""
    template: Optional[str] = None        # id en Catalog.file_templates (para generar un ejemplo si falta)
    optional: bool = False                # si falta: aviso en lugar de error

    @model_validator(mode="after")
    def _una_fuente(self) -> "PathRef":
        fuentes = [x for x in (self.key, self.literal, self.param) if x is not None]
        if len(fuentes) != 1:
            raise ValueError(f"PathRef '{self.name}': indica exactamente una de key / literal / param")
        return self


class ParamSpec(Strict):
    """Parámetro que el usuario debe aportar (no está en ningún conf)."""
    name: str
    type: Literal["string", "path", "integer", "number", "boolean"] = "string"
    required: bool = True
    description: str = ""
    why: str = ""                         # por qué se necesita (se muestra al pedirlo)
    example: str = ""
    default: Any = None
    choices: Optional[list[Any]] = None


class ConfigField(Strict):
    """Campo de un conf_*.json: sirve para validar y para generar la plantilla."""
    name: str                             # nombre con puntos: 'solido.ruta'
    type: Literal["string", "path", "integer", "number", "boolean", "object", "array"] = "string"
    default: Any = None                   # admite {root}
    required: bool = True
    description: str = ""


class ConfigTemplate(Strict):
    format: Literal["json", "yaml"] = "json"
    module: Optional[str] = None          # valor de "__modulo__" que espera el motor
    fields: list[ConfigField] = Field(default_factory=list)


class Column(Strict):
    name: str
    example: Any = ""
    description: str = ""


class FileTemplate(Strict):
    """Plantilla de un archivo de datos tabular (CSV) que una herramienta espera como entrada."""
    id: str = ""
    format: Literal["csv"] = "csv"
    title: str = ""
    description: str = ""
    columns: list[Column]
    n_rows: int = 2
    notes: list[str] = Field(default_factory=list)
    folder: str = ""                      # subcarpeta de la carpeta de ENTRADA donde se espera el archivo (p. ej. 01_Database)
    example_file: str = ""                # archivo de ejemplo real (relativo a la carpeta de entrada); se muestran sus primeras filas
    tools: list[str] = Field(default_factory=list)   # herramientas que lo usan (se completa al cargar el catálogo)


class TableColumn(Strict):
    header: str                           # título de la columna en el CSV (tal como lo escribe la interfaz original)
    key: str = ""                         # clave dentro del conf_*.json ('' = usa el mismo título)
    type: Literal["string", "number", "integer"] = "string"
    default: Any = ""                     # valor si la celda viene vacía
    description: str = ""
    example: Any = ""


class ParamTable(Strict):
    """Tabla de parámetros que se llena en un CSV y se carga a una clave de un conf_*.json (reemplaza al editor de la
    interfaz de escritorio original). Ejemplos reales en <entrada>/04_Parametros_Entrada/."""
    id: str = ""
    file: str                             # nombre del CSV (p. ej. acotamiento_topes.csv)
    title: str
    tool: str                             # herramienta cuyo conf se llena
    config: str                           # conf_*.json destino
    target: str                           # clave del conf donde se guardan las filas ('solidos', 'topes', 'pasadas'...)
    shape: Literal["list", "topes"] = "list"   # list = lista de objetos; topes = {variable: {dominio: {tope, metodo, parametro}}}
    folder: str = "04_Parametros_Entrada"
    description: str = ""
    columns: list[TableColumn]
    example_rows: list[list[Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    required_cols: list[str] = Field(default_factory=list)   # filas sin valor en estas columnas se descartan (como la interfaz)


class PyDep(Strict):
    module: str                           # nombre de import (p. ej. 'trimesh')
    pip: Optional[str] = None             # nombre en pip si difiere


class Runner(Strict):
    kind: Literal["script", "gui"] = "script"
    script: str                           # relativo a la raíz del proyecto
    args: list[str] = Field(default_factory=list)   # {config} {config_dir} {root} {param.NOMBRE}
    cwd: Optional[str] = None             # relativo a la raíz (por defecto, carpeta del script)
    timeout_s: Optional[int] = None


class DataAlt(Strict):
    """Una forma de ejecutar la herramienta: con ESTOS datos disponibles produce ESTOS otros (v3: viabilidad)."""
    id: str = ""
    needs: list[str]                      # ids de `data_kinds` que deben existir (todos)
    gives: list[str] = Field(default_factory=list)    # ids de `data_kinds` que quedan disponibles tras ejecutarla
    note: str = ""


class ToolData(Strict):
    alternatives: list[DataAlt]           # basta UNA alternativa satisfecha
    result_kind: Optional[str] = None     # si el archivo de salida ya existe, este tipo de dato está disponible


class KindDetect(Strict):
    """Cómo reconocer un tipo de dato mirando los archivos de la carpeta de entrada (sin abrirlos enteros)."""
    ext: str = ".csv"                     # .csv (por cabeceras) | .obj (por carpeta)
    require: list[str] = Field(default_factory=list)     # roles de columna que deben estar (todos)
    any_of: list[str] = Field(default_factory=list)      # al menos uno de estos roles
    exclude: list[str] = Field(default_factory=list)     # roles que NO deben estar
    name_has: list[str] = Field(default_factory=list)    # el nombre/carpeta contiene alguno de estos textos
    name_not: list[str] = Field(default_factory=list)    # …y ninguno de estos


class DataKind(Strict):
    id: str
    title: str
    description: str = ""
    detect: Optional[KindDetect] = None   # None = se deduce de otra forma (base de datos, resultados, derivados)
    template: Optional[str] = None        # plantilla de `file_templates` para descargarla si falta


class RouteStep(Strict):
    tool: str
    via: str = ""                         # id de la alternativa de la herramienta que usa esta ruta
    unless: str = ""                      # el paso se omite si ya existe este tipo de dato (p. ej. el modelo de bloques)

    @model_validator(mode="before")
    @classmethod
    def _from_str(cls, v: Any) -> Any:
        return {"tool": v} if isinstance(v, str) else v


class Route(Strict):
    id: str
    title: str
    description: str = ""
    steps: list[RouteStep]
    conf: dict[str, Any] = Field(default_factory=dict)   # valores del conf que corresponden a esta ruta (p. ej. origen)
    # Si la ruta no fija el conf pero una de sus herramientas elige entre alternativas (p. ej. importar: compositos | sondajes), el
    # conf que le corresponde sale de la alternativa que los datos permiten: {tool, file, key}.
    conf_via: dict[str, str] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=list)     # herramientas por las que se pregunta «¿es viable?» y que esta ruta responde


class ColumnFallback(Strict):
    """Qué valor por defecto usar cuando el modelo es NUEVO (lo crea el propio flujo) y no trae la columna que el conf nombra."""
    text: str                             # explicación para el usuario; admite {col} (la columna que falta) y {clave} (valores del conf)
    replace_with: Optional[str] = None    # reemplaza el nombre de la columna por este valor en cada clave que la usa (p. ej. ángulo "0")
    set: dict[str, Any] = Field(default_factory=dict)       # claves del conf a fijar (con puntos para las anidadas)
    config: Optional[str] = None          # conf donde se aplican `set` y `append` (None = el de la lectura)
    append: Optional[dict[str, Any]] = None   # agrega un elemento a una lista del conf: {key, item, unique}


class ColumnRead(Strict):
    """Una clave del conf que NOMBRA una columna del modelo de bloques que el paso necesita (p. ej. la ley de un metal)."""
    key: str                              # ruta JSON dentro del conf; admite [*]
    label: str = ""                       # para qué la usa (se muestra en la alerta)
    config: Optional[str] = None          # conf que la contiene (None = el de la herramienta)
    when: list[Condition] = Field(default_factory=list)   # solo se exige si TODAS se cumplen
    numeric_ok: bool = False              # un número es un valor fijo (p. ej. un ángulo), no una columna
    expr: bool = False                    # la clave es una EXPRESIÓN: se revisan los nombres que usa
    uses_added: bool = False              # puede usar las columnas que agrega este mismo paso
    fallback: Optional[ColumnFallback] = None   # valor por defecto si el modelo es nuevo y la columna no existe


class SolidCover(Strict):
    """Un sólido (.obj) que el paso usa para elegir bloques por su centroide: el modelo debe CUBRIR ese sólido."""
    key: str                              # ruta JSON del conf que trae la ruta del .obj
    label: str = ""                       # p. ej. «sólido de recorte»
    fields: str = ""                      # ruta JSON del objeto {x, y, z} con las columnas del centroide (por defecto XC, YC, ZC)
    when: list[Condition] = Field(default_factory=list)
    clip: bool = False                    # el paso recorta el modelo a este sólido: el ajuste propuesto lo deja justo sobre el sólido


class ColumnAdd(Strict):
    name: str                             # plantilla: texto con {clave.del.conf}; con [*] genera una columna por elemento
    when: list[Condition] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _from_str(cls, v: Any) -> Any:
        return {"name": v} if isinstance(v, str) else v


class ToolColumns(Strict):
    """Cómo un paso lee y transforma las columnas del modelo de bloques (para avisar ANTES de ejecutar si una no existirá)."""
    model_in: str = ""                    # nombre de la entrada (`inputs`) que es el modelo de bloques
    model_out: str = ""                   # nombre de la salida (`outputs`) que es el modelo resultante
    chained: bool = True                  # el modelo de entrada debe ser el que dejó el paso anterior del plan
    creates: list[ColumnAdd] = Field(default_factory=list)   # el paso CREA el modelo con estas columnas (reemplaza las de entrada)
    reads: list[ColumnRead] = Field(default_factory=list)
    adds: list[ColumnAdd] = Field(default_factory=list)
    grid: str = ""                        # (solo si `creates`) ruta JSON del esquema de la grilla: origen_*, extension_*, *_size, bearing
    solid: list[SolidCover] = Field(default_factory=list)   # sólidos con los que el paso elige bloques (el modelo debe cubrirlos)


class ToolSpec(Strict):
    id: str
    title: str
    description: str
    summary: str = ""                     # explicación breve y llana para el usuario (se muestra en «Herramientas»)
    category: Literal["flujo", "utilidad"] = "flujo"
    order: int = 100                      # orden canónico dentro del flujo
    keywords: list[str] = Field(default_factory=list)    # para el fallback por reglas
    synonyms: list[str] = Field(default_factory=list)    # más formas de pedirlo (sinónimos, inglés, jerga); suman al léxico
    examples: list[str] = Field(default_factory=list)    # frases de usuario -> few-shot automático
    data: Optional[ToolData] = None       # qué datos necesita y qué deja disponible (viabilidad)
    runner: Runner
    config: Optional[str] = None          # conf principal
    also_configs: list[str] = Field(default_factory=list)  # otros conf que el motor lee (se verifica su existencia)
    config_template: Optional[ConfigTemplate] = None
    required_keys: list[str] = Field(default_factory=list)  # claves obligatorias aunque no haya config_template
    requires_python: list[PyDep] = Field(default_factory=list)
    params: list[ParamSpec] = Field(default_factory=list)
    inputs: list[PathRef] = Field(default_factory=list)
    outputs: list[PathRef] = Field(default_factory=list)     # lo que CREA (permite inferir dependencias)
    modifies: list[PathRef] = Field(default_factory=list)    # lo que MODIFICA en el sitio (p. ej. la base .db)
    skip_when: list[Condition] = Field(default_factory=list)  # se omite del flujo si ALGUNA se cumple
    columns: Optional[ToolColumns] = None    # columnas del modelo de bloques que lee / agrega (coherencia entre pasos)
    destructive: bool = False
    destructive_reason: str = ""
    hints: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_ok(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or v != v.lower():
            raise ValueError("el id debe ser minúsculas, dígitos y '_'")
        return v


class WorkflowSpec(Strict):
    id: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)   # frases de usuario -> few-shot automático
    tools: list[str] = Field(default_factory=list)   # vacío + all_flow = todo el flujo canónico
    all_flow: bool = False
    exclude: list[str] = Field(default_factory=list)  # herramientas que NO se agregan solas por dependencia (p. ej. el plan de sondajes)
    conf: dict[str, dict[str, Any]] = Field(default_factory=dict)   # valores de conf que este flujo necesita ({conf: {clave.anidada: valor}})


class Catalog(Strict):
    version: int = 1
    description: str = ""
    config_dir: Optional[str] = None
    file_templates: dict[str, FileTemplate] = Field(default_factory=dict)
    param_tables: dict[str, ParamTable] = Field(default_factory=dict)
    column_roles: dict[str, list[str]] = Field(default_factory=dict)   # rol de columna -> nombres con que suele aparecer
    data_kinds: list[DataKind] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    workflows: list[WorkflowSpec] = Field(default_factory=list)
    tools: list[ToolSpec]


# ============================================================================ 2. PLANNER

class PlannerOutput(BaseModel):
    """Contrato JSON que debe cumplir el SLM. Los campos extra se ignoran; los inválidos se rechazan."""
    model_config = ConfigDict(extra="ignore")

    intent: Literal["run", "explain", "list", "status", "resume", "fix_paths", "formats", "viability", "ask", "unknown"] = "run"
    tools: list[str] = Field(default_factory=list)
    workflow: Optional[str] = None
    from_tool: Optional[str] = None
    to_tool: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_clarification: Optional[str] = None
    rationale: str = ""

    @field_validator("tools", mode="before")
    @classmethod
    def _tools_lista(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v

    @field_validator("params", mode="before")
    @classmethod
    def _params_dict(cls, v: Any) -> Any:
        return {} if v is None else v

    @field_validator("workflow", "from_tool", "to_tool", "needs_clarification", mode="before")
    @classmethod
    def _vacio_a_none(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "ninguno"):
            return None
        return v


# ============================================================================ 3. EJECUCIÓN

Severity = Literal["error", "warn", "info"]
StepStatus = Literal["pending", "running", "ok", "error", "skipped", "blocked"]
RunStatus = Literal["info", "needs_input", "blocked", "ready", "running", "failed", "completed", "aborted"]


class FixAction(BaseModel):
    """Acción concreta que resolvería un hallazgo."""
    kind: Literal["generate_template", "fix_path", "install_dep", "ask_param", "edit_config", "load_table", "manual"]
    detail: dict[str, Any] = Field(default_factory=dict)
    command: Optional[str] = None         # comando exacto, si aplica


class Finding(BaseModel):
    tool: str
    code: str
    severity: Severity
    message: str
    hint: str = ""
    fix: Optional[FixAction] = None


class PathFix(BaseModel):
    """Corrección propuesta de una ruta dentro de un conf_*.json."""
    config_file: str                      # nombre del conf
    jpath: str                            # ruta concreta dentro del JSON (con índices)
    old: str
    new: str
    reason: str
    confidence: Literal["alta", "media"] = "alta"
    ambiguous: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    tool_id: str
    order: int
    reason: str
    forced: bool = False
    status: StepStatus = "pending"
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_s: Optional[float] = None
    returncode: Optional[int] = None
    log_file: Optional[str] = None
    tail: list[str] = Field(default_factory=list)
    diagnosis: str = ""
    command: list[str] = Field(default_factory=list)


class OmittedTool(BaseModel):
    tool_id: str
    reason: str


class ExecutionPlan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    omitted: list[OmittedTool] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class GeneratedFile(BaseModel):
    path: str                             # plantilla generada (en plantillas_generadas/)
    target: str                           # dónde debería quedar para que la herramienta la use
    tool: str
    kind: Literal["config", "data"]
    promoted: bool = False


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class PlannerMetrics(BaseModel):
    """Qué costó interpretar la solicitud (las mismas medidas que se piden en los notebooks de la clase)."""
    model: str = ""
    calls: int = 0                        # llamadas al SLM (1 + reintentos)
    retries: int = 0
    json_first: Optional[bool] = None     # ¿JSON válido a la primera?
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0                  # tiempo de las llamadas al modelo
    load_seconds: float = 0.0             # parte que fue cargar el modelo en memoria
    tokens_per_s: float = 0.0
    wall_seconds: float = 0.0             # tiempo total de la interpretación (incluye reglas y validación)
    vram_gb: Optional[float] = None       # memoria de GPU que ocupa el modelo
    provider: str = ""                    # huggingface | ollama
    cost_usd: Optional[float] = None      # costo estimado de la solicitud con los precios de referencia del modelo (nube)
    source: str = ""


class AnswerSource(BaseModel):
    id: str
    title: str
    kind: str = ""                        # param | tool | concept
    conf: str = ""
    key: str = ""


class AnswerInfo(BaseModel):
    """Respuesta del chatbot a una duda sobre procesos o parámetros (v3). Siempre trae las fichas del manual que la respaldan."""
    question: str = ""
    text: str = ""
    sources: list[AnswerSource] = Field(default_factory=list)
    mode: str = "manual"                  # slm | manual (solo fichas, sin modelo)
    model: str = ""
    provider: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0
    cost_usd: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    run_id: str
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    request: str = ""
    status: RunStatus = "ready"
    planner_source: str = ""              # slm | rules | provided
    metrics: Optional[PlannerMetrics] = None
    answer: Optional[AnswerInfo] = None   # respuesta del chat (intención ask/explain)
    planner: Optional[PlannerOutput] = None
    planner_notes: list[str] = Field(default_factory=list)
    question: Optional[str] = None        # pregunta de aclaración pendiente
    params: dict[str, Any] = Field(default_factory=dict)
    plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    findings: list[Finding] = Field(default_factory=list)
    path_fixes: list[PathFix] = Field(default_factory=list)
    generated: list[GeneratedFile] = Field(default_factory=list)
    overwrites: list[str] = Field(default_factory=list)   # rutas que se sobrescribirían (requieren confirmación)
    confirmed: bool = False
    frozen: bool = False                  # la ejecución ya empezó: el plan no se recalcula al reanudar
    dry_run: bool = True

    def touch(self) -> None:
        self.updated_at = now_iso()

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]
