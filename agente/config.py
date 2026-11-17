"""Configuración del agente (config/agent.yaml) validada con Pydantic.

v3: los modelos por defecto son SLM alojados en Hugging Face (API compatible con OpenAI, con tu API key). Solo viajan tu
frase, el catálogo de herramientas y las fichas del manual; nunca tus datos ni tus rutas. El SLM local (Ollama) sigue
disponible como alternativa sin red.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

BASE_DIR = Path(__file__).resolve().parents[1]          # raíz del repositorio (donde están agente/, config/ y Proyecto_IDW/)
DEFAULT_CONFIG = BASE_DIR / "config" / "agent.yaml"



class LLMEndpoint(BaseModel):
    """SLM servido por Ollama en tu propio equipo."""
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str = "http://localhost:11434"
    temperature: float = 0.0
    num_ctx: int = 4096
    num_predict: int = 400
    timeout_s: float = 120
    keep_alive: str = "10m"
    use_json_format: bool = False


class CloudModel(BaseModel):
    """Un modelo de la lista desplegable (nube)."""
    model_config = ConfigDict(extra="forbid")
    id: str                                       # id de Hugging Face, p. ej. google/gemma-3-4b-it (admite sufijo :proveedor)
    label: str = ""                               # nombre corto para mostrar
    params: str = ""                              # tamaño aproximado (p. ej. 4B)
    note: str = ""                                # por qué está en la lista
    price_in: Optional[float] = None              # US$ por millón de tokens de entrada (referencia: cambia por proveedor)
    price_out: Optional[float] = None             # US$ por millón de tokens de salida

    @property
    def name(self) -> str:
        return self.label or self.id


class CloudConfig(BaseModel):
    """Modelos en la nube vía Hugging Face (Inference Providers), con una API key tuya."""
    model_config = ConfigDict(extra="forbid")
    base_url: str = "https://router.huggingface.co/v1"
    token_env: str = "HF_TOKEN"                   # variable de entorno de la que se lee la clave si no se escribe en la interfaz
    model: str = "google/gemma-3-4b-it"           # modelo por defecto
    models: list[CloudModel] = Field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 600
    timeout_s: float = 90
    retries: int = 2                              # reintentos ante límite de solicitudes (429) o error del servidor (5xx)
    max_models: int = 5                           # tope de modelos de la lista (comparar demasiados dispersa el análisis)
    # La clave NUNCA se guarda: ni en el YAML, ni en el estado, ni en las trazas (exclude + repr oculto).
    api_key: Optional[SecretStr] = Field(default=None, exclude=True, repr=False)

    @field_validator("models")
    @classmethod
    def _tope(cls, v: list[CloudModel]) -> list[CloudModel]:
        if len(v) > 5:
            raise ValueError("La lista de modelos en la nube admite como máximo 5.")
        return v

    def resolve_key(self) -> str:
        """Clave escrita en la interfaz o, si no hay, la de la variable de entorno."""
        def clean(raw: str) -> str:                     # espacios, saltos de línea, comillas o «Bearer » pegados con el token
            k = (raw or "").strip().strip("\"'`").strip()
            if k.lower().startswith("bearer "):
                k = k[7:]
            return "".join(k.split())

        if self.api_key is not None and clean(self.api_key.get_secret_value()):
            return clean(self.api_key.get_secret_value())
        return clean(os.environ.get(self.token_env) or "")

    def model_info(self, model_id: str) -> Optional[CloudModel]:
        return next((m for m in self.models if m.id == model_id), None)


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    provider: Literal["huggingface", "ollama"] = "huggingface"      # v3: nube por defecto; ollama = SLM local
    report_with_llm: bool = False          # el Reporter pide un resumen en prosa al SLM (más lento; opcional)
    max_retries: int = 2
    # Arbitraje determinista sobre la respuesta del modelo (ver planner._arbitrate):
    #  - intención explícita (verifica/lista/explica/reanuda/corrige rutas), workflow y rango los fija el léxico;
    #  - toda herramienta elegida por el modelo debe tener respaldo léxico en la frase, o se descarta y se pregunta.
    arbitrate_with_rules: bool = True
    models: list[str] = Field(default_factory=lambda: ["gemma3:4b", "qwen3-vl:4b-instruct"])   # lista desplegable (Ollama local)
    primary: LLMEndpoint                              # SLM local (Ollama)
    cloud: CloudConfig = Field(default_factory=CloudConfig)


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_destructive: bool = True
    backup_overwritten: bool = False
    backup_max_mb: int = 200
    backup_config_before_fix: bool = True
    extra_allowed_roots: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idw_root: Path
    config_dir: str = "Configuraciones"
    python: str = sys.executable
    catalogs: list[Path] = Field(default_factory=lambda: [Path("catalog/tools_idw.yaml")])
    runs_dir: Path = Path("runs")
    templates_dir: Path = Path("plantillas_generadas")
    prompts_dir: Path = Path("prompts")
    manual: Path = Path("catalog/manual_idw.yaml")      # manual de parámetros (chatbot y descarga)
    default_timeout_s: int = 3600
    llm: LLMConfig
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    path_anchors: list[str] = Field(default_factory=lambda: ["01_Data", "02_Resultados", "Configuraciones"])
    # Carpetas de trabajo elegidas por el usuario (la interfaz las pide al abrir). None = las de dentro de idw_root.
    data_dir: Optional[Path] = None          # equivale a 01_Data: archivos de ENTRADA
    results_dir: Optional[Path] = None       # equivale a 02_Resultados: archivos de SALIDA

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None, *, root: Path | str | None = None,
             catalogs: list[str] | None = None, data_dir: Path | str | None = None,
             results_dir: Path | str | None = None) -> "AgentConfig":
        cfg_path = Path(path) if path else DEFAULT_CONFIG
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        cfg = cls.model_validate(raw)
        cfg._resolve(root_override=root, catalogs_override=catalogs)
        if data_dir or results_dir:
            cfg.set_workspace(data_dir, results_dir)
        return cfg

    def _resolve(self, *, root_override=None, catalogs_override=None) -> None:
        def absolute(p: Path | str) -> Path:
            p = Path(p)
            return p if p.is_absolute() else (BASE_DIR / p).resolve()

        env_root = os.environ.get("AGENTE_ROOT")
        self.idw_root = absolute(root_override or env_root or self.idw_root)
        self.python = os.environ.get("AGENTE_PYTHON") or self.python
        if not Path(self.python).exists():          # el venv configurado no existe: se usa el intérprete actual
            self.python = sys.executable
        self.catalogs = [absolute(c) for c in (catalogs_override or self.catalogs)]
        self.runs_dir = absolute(os.environ.get("AGENTE_RUNS_DIR") or self.runs_dir)               # las pruebas lo redirigen a tmp
        self.templates_dir = absolute(os.environ.get("AGENTE_TEMPLATES_DIR") or self.templates_dir)
        self.prompts_dir = absolute(self.prompts_dir)
        self.manual = absolute(os.environ.get("AGENTE_MANUAL") or self.manual)

    def set_workspace(self, data_dir: Path | str | None, results_dir: Path | str | None) -> None:
        self.data_dir = Path(data_dir).resolve() if data_dir else None
        self.results_dir = Path(results_dir).resolve() if results_dir else None

    @property
    def config_path(self) -> Path:
        return self.idw_root / self.config_dir

    @property
    def has_workspace(self) -> bool:
        return self.data_dir is not None and self.results_dir is not None


# ----------------------------------------------------------------------------- carpetas recordadas
def workspace_file() -> Path:
    """Archivo local con las últimas carpetas elegidas. Se lee AL USAR (no al importar): así las pruebas pueden
    redirigirlo con AGENTE_WORKSPACE_FILE sin tocar el archivo del usuario."""
    return Path(os.environ.get("AGENTE_WORKSPACE_FILE") or BASE_DIR / "config" / "workspace.json")


def load_saved_workspace(path: Path | None = None) -> dict[str, str]:
    """Últimas carpetas usadas. Solo se devuelven si la de entrada todavía existe (si no, se ofrecen las de por defecto)."""
    try:
        data = json.loads((path or workspace_file()).read_text(encoding="utf-8"))
        saved = {k: str(data[k]) for k in ("data_dir", "results_dir") if data.get(k)}
    except (OSError, ValueError):
        return {}
    return saved if Path(saved.get("data_dir", "")).is_dir() else {}


def save_workspace(data_dir: Path | str, results_dir: Path | str, path: Path | None = None) -> None:
    try:
        (path or workspace_file()).write_text(json.dumps({"data_dir": str(data_dir), "results_dir": str(results_dir)}, indent=2), encoding="utf-8")
    except OSError:
        pass            # recordar las carpetas es una comodidad, no un requisito
