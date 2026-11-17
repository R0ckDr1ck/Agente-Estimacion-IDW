"""Clientes de modelo (SLM en Hugging Face o local en Ollama) y utilidades de medición.

Interfaz mínima: chat(messages, json_mode) -> str. El agente nunca confía en la respuesta: el planner la valida
con Pydantic y contra el catálogo (ver planner.py).

Cada llamada deja una `CallStats` (tokens de entrada/salida y tiempos). Con ellas la interfaz muestra las métricas,
igual que los notebooks de la clase (tokens, latencia, costo). Hugging Face usa la API compatible con OpenAI
(`/chat/completions`) y clasifica los errores por código HTTP como el notebook «duelo de modelos» (401/403 clave,
404 no servido, 410 retirado, 429 límite de solicitudes).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

import httpx

from .config import CloudConfig, LLMConfig, LLMEndpoint


class LLMError(Exception):
    """El modelo no está disponible o respondió con error de transporte (no confundir con 'JSON inválido')."""


@dataclass
class CallStats:
    """Medidas de UNA llamada al modelo."""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    seconds: float = 0.0              # tiempo total de la llamada (incluye la carga del modelo si estaba descargado)
    load_seconds: float = 0.0         # parte de ese tiempo que fue cargar el modelo en memoria
    gen_seconds: float = 0.0          # tiempo generando los tokens de salida

    @property
    def tokens_per_s(self) -> float:
        return self.tokens_out / self.gen_seconds if self.gen_seconds > 0 else 0.0


@dataclass
class UsageLog:
    """Acumulado de llamadas de un cliente. `mark()`/`since()` permiten medir una solicitud concreta."""
    calls: list[CallStats] = field(default_factory=list)

    def mark(self) -> int:
        return len(self.calls)

    def since(self, mark: int) -> list[CallStats]:
        return self.calls[mark:]


class LLMClient(Protocol):
    name: str
    usage: UsageLog

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str: ...


class OllamaClient:
    def __init__(self, ep: LLMEndpoint):
        self.ep = ep
        self.name = f"ollama:{ep.model}"
        self.usage = UsageLog()

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.ep.model, "messages": messages, "stream": False, "keep_alive": self.ep.keep_alive,
            "options": {"temperature": self.ep.temperature, "num_ctx": self.ep.num_ctx,
                        "num_predict": self.ep.num_predict},
        }
        if json_mode or self.ep.use_json_format:
            payload["format"] = "json"
        try:
            r = httpx.post(f"{self.ep.base_url.rstrip('/')}/api/chat", json=payload, timeout=self.ep.timeout_s)
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama no responde en {self.ep.base_url} ({type(e).__name__}: {e}). "
                           f"¿Está iniciado el servicio? (ollama serve)") from e
        if r.status_code == 404:
            raise LLMError(f"El modelo '{self.ep.model}' no está instalado en Ollama. Instálalo con:  ollama pull {self.ep.model}")
        if r.status_code >= 400:
            raise LLMError(f"Ollama devolvió HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
            text = data["message"]["content"]
        except (ValueError, KeyError) as e:
            raise LLMError(f"Respuesta inesperada de Ollama: {r.text[:300]}") from e
        ns = 1e9                                                  # Ollama informa los tiempos en nanosegundos
        self.usage.calls.append(CallStats(
            model=self.ep.model, tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0), seconds=float(data.get("total_duration") or 0) / ns,
            load_seconds=float(data.get("load_duration") or 0) / ns, gen_seconds=float(data.get("eval_duration") or 0) / ns))
        return text


class MockLLM:
    """Respuestas guiadas, para pruebas y evaluación sin modelo. Si el elemento es una excepción, se lanza."""

    def __init__(self, responses: list, name: str = "mock"):
        self.responses = list(responses)
        self.name = name
        self.calls: list[list[dict[str, str]]] = []
        self.usage = UsageLog()

    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        self.calls.append(messages)
        if not self.responses:
            raise LLMError("MockLLM sin respuestas")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        self.usage.calls.append(CallStats(model=self.name, tokens_in=sum(len(m["content"]) for m in messages) // 4,
                                          tokens_out=len(r) // 4, seconds=0.01, gen_seconds=0.01))
        return r


class CloudClient:
    """Chat de un modelo alojado en Hugging Face (API compatible con OpenAI). La clave no aparece en repr, errores ni trazas."""

    def __init__(self, cfg: CloudConfig, model: Optional[str] = None, api_key: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None):
        self.cfg = cfg
        self.model = model or cfg.model
        self._key = (api_key if api_key is not None else cfg.resolve_key())
        self._transport = transport
        self.name = f"hf:{self.model}"
        self.usage = UsageLog()
        info = cfg.model_info(self.model)
        self.price = (info.price_in, info.price_out) if info and info.price_in is not None and info.price_out is not None else None

    def __repr__(self) -> str:                      # nunca incluye la clave
        return f"CloudClient({self.name})"

    # ------------------------------------------------------------------ errores
    @staticmethod
    def explain_status(code: int, body: str = "") -> str:
        detail = re.sub(r"\s+", " ", body or "")[:200]
        if code == 401:
            return ("HTTP 401: Hugging Face rechazó la API key (inválida, mal copiada o revocada). Usa «Diagnosticar mi clave» en la barra "
                    "lateral o crea un token nuevo en huggingface.co/settings/tokens.")
        if code == 403:
            if "inference" in detail.lower() or "permission" in detail.lower():
                return ("HTTP 403: tu token es válido pero NO tiene permiso para llamar a Inference Providers. En "
                        "huggingface.co/settings/tokens crea (o edita) un token de permisos finos y marca «Make calls to Inference Providers». "
                        f"({detail[:120]})")
            return (f"HTTP 403: la API key no tiene acceso ({detail[:120]}). Revisa que el token tenga permiso para «Inference Providers» y que el "
                    "proveedor del modelo esté habilitado en huggingface.co/settings/inference-providers.")
        if code == 402:
            return f"HTTP 402: se agotó el crédito gratuito de Hugging Face para tu cuenta. {detail}"
        if code == 404:
            return "HTTP 404: ese modelo no está servido para tu cuenta en este momento. Prueba otro modelo de la lista."
        if code == 410:
            return "HTTP 410: ese modelo fue retirado (fin de vida). Elige otro."
        if code == 429:
            return "HTTP 429: límite de solicitudes alcanzado; espera unos segundos y reintenta."
        if code in (400, 422):
            return f"HTTP {code}: el proveedor rechazó la solicitud. {detail}"
        return f"HTTP {code}: {detail or 'error del servidor'}"

    # ------------------------------------------------------------------ llamada
    def chat(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        if not self._key:
            raise LLMError(f"Falta la API key de Hugging Face: escríbela en la barra lateral o define la variable {self.cfg.token_env}.")
        payload = {"model": self.model, "messages": messages, "temperature": self.cfg.temperature,
                   "max_tokens": self.cfg.max_tokens, "stream": False}
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        t0 = time.perf_counter()
        last = ""
        for attempt in range(self.cfg.retries + 1):
            try:
                with httpx.Client(timeout=self.cfg.timeout_s, transport=self._transport) as cli:
                    r = cli.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as e:
                raise LLMError(f"Hugging Face no respondió en {self.cfg.timeout_s:.0f} s ({type(e).__name__}).") from None
            except httpx.HTTPError as e:
                raise LLMError(f"No pude conectar con Hugging Face ({type(e).__name__}). ¿Hay internet?") from None
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.cfg.retries:
                last = self.explain_status(r.status_code, r.text)
                wait = min(float(r.headers.get("retry-after", "") or 0) or 1.5 * (attempt + 1), 20.0)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise LLMError(self.explain_status(r.status_code, r.text))
            break
        else:                                        # pragma: no cover - el bucle siempre sale por break/raise
            raise LLMError(last or "Hugging Face no respondió.")
        try:
            data = r.json()
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMError(f"Respuesta inesperada de Hugging Face: {r.text[:200]}") from None
        if not text.strip():
            raise LLMError("El modelo devolvió una respuesta vacía (algunos modelos de razonamiento gastan todo el límite de "
                           "tokens pensando). Prueba otro modelo.")
        wall = time.perf_counter() - t0
        u = data.get("usage") or {}
        self.usage.calls.append(CallStats(
            model=self.model, tokens_in=int(u.get("prompt_tokens") or 0), tokens_out=int(u.get("completion_tokens") or 0),
            seconds=wall, load_seconds=0.0, gen_seconds=wall))
        return text


@dataclass
class Probe:
    model: str
    ok: bool
    seconds: float = 0.0
    detail: str = ""


def probe_model(cfg: CloudConfig, model: str, api_key: Optional[str] = None, transport: Optional[httpx.BaseTransport] = None) -> Probe:
    """«La única prueba de que un modelo está disponible es llamarlo» (notebook de la clase): una llamada mínima."""
    cli = CloudClient(cfg.model_copy(update={"retries": 0, "max_tokens": 8, "timeout_s": min(cfg.timeout_s, 45)}), model, api_key, transport)
    t0 = time.perf_counter()
    try:
        out = cli.chat([{"role": "user", "content": "Responde solo: listo"}])
        return Probe(model, True, time.perf_counter() - t0, out.strip()[:30])
    except LLMError as e:
        return Probe(model, False, time.perf_counter() - t0, str(e))


@dataclass
class TokenCheck:
    """Qué sabe Hugging Face de tu token (sin mostrar nunca el token)."""
    ok: bool                       # ¿el token es válido?
    kind: str = ""                 # fineGrained | write | read | ""
    inference: Optional[bool] = None   # ¿trae permiso para llamar a Inference Providers? (None = no se pudo saber)
    detail: str = ""
    advice: list[str] = field(default_factory=list)


def check_token(api_key: str, base_url: str = "https://huggingface.co", timeout: float = 15.0,
                transport: Optional[httpx.BaseTransport] = None) -> TokenCheck:
    """Pregunta a Hugging Face (whoami-v2) qué tipo de token es y qué permisos tiene, y lo traduce a consejos concretos."""
    key = clean_key(api_key)
    if not key:
        return TokenCheck(False, detail="No hay clave escrita.", advice=["Pega tu token (empieza con hf_) en el campo de la barra lateral."])
    try:
        with httpx.Client(timeout=timeout, transport=transport) as cli:
            r = cli.get(base_url.rstrip("/") + "/api/whoami-v2", headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as e:
        return TokenCheck(False, detail=f"No pude conectar con huggingface.co ({type(e).__name__}).", advice=["Revisa tu conexión, proxy o firewall."])
    if r.status_code in (401, 403):
        return TokenCheck(False, detail=f"HTTP {r.status_code}: Hugging Face no reconoce este token.",
                          advice=["Copia el token completo otra vez (solo se muestra al crearlo) o crea uno nuevo en huggingface.co/settings/tokens.",
                                  "Verifica que no se pegó con espacios, comillas o la palabra «Bearer» (el agente ya los quita)."])
    if r.status_code >= 400:
        return TokenCheck(False, detail=f"HTTP {r.status_code} al consultar el token.", advice=["Reintenta en unos minutos."])
    try:
        tok = (r.json().get("auth") or {}).get("accessToken") or {}
    except ValueError:
        tok = {}
    kind = str(tok.get("role") or "")
    perms: list[str] = []
    fg = tok.get("fineGrained") or {}
    for blk in [fg.get("global") or []] + [(x or {}).get("permissions") or [] for x in (fg.get("scoped") or [])]:
        perms += [str(x) for x in blk]
    inference: Optional[bool] = None
    advice: list[str] = []
    if kind == "fineGrained":
        inference = any(p.startswith("inference.serverless") or p.startswith("inference.endpoints.infer") for p in perms)
        if not inference:
            advice.append("Tu token es de permisos finos y NO tiene «Make calls to Inference Providers». En huggingface.co/settings/tokens "
                          "edita el token (o crea uno nuevo) y marca esa casilla, en la sección «Inference».")
    elif kind == "read":
        advice.append("Un token «read» puede ser rechazado por Inference Providers. Crea un token de permisos finos con «Make calls to "
                      "Inference Providers» (recomendado) o uno «write».")
    elif kind == "write":
        inference = None
        advice.append("Un token «write» normalmente sirve. Si aun así falla, crea uno de permisos finos marcando «Make calls to Inference Providers».")
    else:
        advice.append("No pude leer el tipo del token; si falla la llamada a los modelos, crea uno de permisos finos con «Make calls to Inference Providers».")
    return TokenCheck(True, kind, inference, f"Token válido, tipo «{kind or 'desconocido'}».", advice)


def clean_key(raw: str) -> str:
    """Quita lo que suele pegarse junto al token: espacios, saltos de línea, comillas y el prefijo «Bearer»."""
    k = (raw or "").strip().strip("\"'`").strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return re.sub(r"\s+", "", k)


def hf_catalog(base_url: str, timeout: float = 15.0, transport: Optional[httpx.BaseTransport] = None) -> Optional[dict[str, dict]]:
    """Catálogo público de modelos del router de Hugging Face (no necesita clave): id → proveedores y precios. None si no hay red."""
    try:
        with httpx.Client(timeout=timeout, transport=transport) as cli:
            r = cli.get(base_url.rstrip("/") + "/models")
        r.raise_for_status()
        out: dict[str, dict] = {}
        for m in r.json().get("data", []):
            provs = m.get("providers") or []
            live = [p for p in provs if p.get("status", "live") == "live"]
            pr = next((p.get("pricing") for p in live if p.get("pricing")), None) or {}
            out[m["id"]] = {"providers": [p.get("provider") for p in live], "price_in": pr.get("input"), "price_out": pr.get("output")}
        return out
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def build_client(llm: "LLMConfig | LLMEndpoint", model: Optional[str] = None) -> LLMClient:
    """Cliente según el proveedor configurado. Con Hugging Face exige la API key (si falta, LLMError con el motivo)."""
    if isinstance(llm, LLMEndpoint):                # compatibilidad: build_client(endpoint) = Ollama
        return OllamaClient(llm)
    if llm.provider == "ollama":
        ep = llm.primary.model_copy(update={"model": model}) if model else llm.primary
        return OllamaClient(ep)
    if not llm.cloud.resolve_key():
        raise LLMError(f"Falta la API key de Hugging Face: escríbela en la barra lateral o define la variable {llm.cloud.token_env}.")
    return CloudClient(llm.cloud, model)


# ------------------------------------------------------------------------------------ consultas a Ollama
def installed_models(base_url: str = "http://localhost:11434", timeout: float = 3.0) -> list[str] | None:
    """Modelos instalados en Ollama, o None si el servicio no responde."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def local_model_list(configured: list[str], installed: Optional[list[str]]) -> list[str]:
    """Modelos locales para elegir: los del conf (en su orden) y TODOS los que hay descargados en Ollama (menos los de embeddings,
    que no sirven para chatear). «llama3» y «llama3:latest» cuentan como el mismo."""
    def norm(n: str) -> str:
        return n[:-7] if n.endswith(":latest") else n
    out: list[str] = []
    seen: set[str] = set()
    for n in list(configured) + [m for m in (installed or []) if "embed" not in m.lower()]:
        if norm(n) not in seen:
            seen.add(norm(n))
            out.append(n)
    return out


def gpu_memory_gb(model: str, base_url: str = "http://localhost:11434", timeout: float = 3.0) -> float | None:
    """GB de VRAM que ocupa el modelo ahora mismo (solo si está cargado)."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/ps", timeout=timeout)
        r.raise_for_status()
        for m in r.json().get("models", []):
            if m.get("name") == model or m.get("model") == model:
                return float(m.get("size_vram") or 0) / 1e9
    except (httpx.HTTPError, ValueError, KeyError):
        pass
    return None
