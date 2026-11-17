"""Chatbot de consulta (v3): responde dudas sobre los procesos de estimación y sobre qué hace cada parámetro.

No es un modelo «que sabe de todo»: es recuperación + redacción.
  1. Se buscan en el manual (catalog/manual_idw.yaml) las fichas que corresponden a la pregunta.
  2. Si hay modelo (nube o local), redacta la respuesta usando SOLO esas fichas (y el valor actual de tu proyecto si es un
     número o un texto corto; nunca rutas ni datos). Si no hay modelo o falla, el agente responde con las fichas tal cual.
  3. La respuesta siempre cita las fichas usadas, para que se pueda comprobar.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from . import rules
from .context import ProjectContext
from .llm import CloudClient, LLMClient, LLMError, OllamaClient
from .manual import Entry, Manual, values_for
from .models import AnswerInfo, AnswerSource
from .pathfix import is_abs_path_str
from .textutil import normalize

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
MAX_HISTORY = 6
INDEX_HINT = ("Puedes preguntar, por ejemplo: «¿qué pasa si subo la potencia?», «¿cómo elijo el tope de altas leyes?», "
              "«¿para qué sirve el radio mayor?», «¿qué es el cutoff?» o «¿qué parámetros tiene la estimación?».")


# ---------------------------------------------------------------------------------------------- valores actuales
def current_value(ctx: Optional[ProjectContext], e: Entry, limit: int = 6) -> str:
    """Valor que hoy tiene el conf del usuario para esta ficha (vacío si no existe, es una ruta o es una tabla larga)."""
    if ctx is None or e.kind != "param" or not e.conf:
        return ""
    data, _ = ctx.load_cfg(e.conf)
    if data is None:
        return ""
    vals = values_for(e.key, data)
    if not vals:
        return ""
    texts: list[str] = []
    for v in vals:
        if isinstance(v, str) and (is_abs_path_str(v) or len(v) > 60):
            return ""                                               # rutas y textos largos no se muestran ni se envían
        t = "sí" if v is True else "no" if v is False else (str(int(v)) if isinstance(v, float) and v.is_integer() else str(v))
        texts.append(t if t != "" else "(vacío)")
    uniq = list(dict.fromkeys(texts))
    if len(uniq) == 1:
        return uniq[0] + (f" (en {len(texts)} fila(s))" if len(texts) > 1 else "")
    shown = ", ".join(uniq[:limit]) + ("…" if len(uniq) > limit else "")
    return f"{len(uniq)} valores distintos en {len(texts)} fila(s): {shown}"


# ---------------------------------------------------------------------------------------------- respuesta
def _sources(hits: list[tuple[Entry, float]]) -> list[AnswerSource]:
    return [AnswerSource(id=e.id, title=e.title, kind=e.kind, conf=e.conf, key=e.key) for e, _ in hits]


def render_fichas(manual: Manual, hits: list[tuple[Entry, float]], ctx: Optional[ProjectContext]) -> str:
    return "\n\n".join(manual.render_entry(e, current_value(ctx, e)) for e, _ in hits)


def _clean(text: str) -> str:
    return _THINK.sub("", text).strip()


def render_answer(ans: AnswerInfo) -> str:
    """Respuesta lista para mostrar (Markdown) con las fuentes citadas."""
    L = [ans.text.strip()]
    if ans.sources:
        L += ["", "**Fuentes del manual:** " + " · ".join(f"{s.title}" + (f" (`{s.key}`)" if s.kind == "param" else "") for s in ans.sources)]
    for w in ans.warnings:
        L += ["", f"> {w}"]
    return "\n".join(L) + "\n"


def answer(question: str, manual: Manual, ctx: Optional[ProjectContext] = None, client: Optional[LLMClient] = None,
           history: Optional[list[dict[str, str]]] = None, tools: Optional[list[str]] = None, prompts_dir: Optional[Path] = None,
           k: int = 4) -> AnswerInfo:
    """Responde `question` con el manual. `history` = turnos previos [{'role': 'user'|'assistant', 'content': ...}]."""
    reg = ctx.registry if ctx is not None else None
    hint_tools = list(tools or [])
    if reg is not None:
        sc = rules.score_tools(normalize(question), reg)
        hint_tools += [t for t in sc if t not in hint_tools]
    hits = manual.find(question, hint_tools, k=k)
    ans = AnswerInfo(question=question, sources=_sources(hits))

    if not hits:
        ans.text = ("No encontré en el manual nada que responda esa duda. " + INDEX_HINT)
        ans.warnings.append("Sin fichas relacionadas: no se consultó al modelo para evitar respuestas inventadas.")
        return ans

    fichas = render_fichas(manual, hits, ctx)
    if client is None:
        ans.text = "Esto es lo que dice el manual sobre tu pregunta:\n\n" + fichas
        return ans

    system = "Eres el asistente del Agente de Estimación IDW. Responde en español usando SOLO las FICHAS DEL MANUAL."
    if prompts_dir is not None:
        try:
            system = re.sub(r"<!--.*?-->\s*", "", (Path(prompts_dir) / "chat_system.md").read_text(encoding="utf-8"), flags=re.S).strip()
        except OSError:
            pass
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in (history or [])[-MAX_HISTORY:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:700]})
    messages.append({"role": "user", "content": f"FICHAS DEL MANUAL:\n{fichas}\n\nPREGUNTA: {question}"})

    mark = client.usage.mark() if hasattr(client, "usage") else 0
    try:
        text = _clean(client.chat(messages))
        if not text:
            raise LLMError("respuesta vacía")
    except LLMError as e:
        ans.text = "Esto es lo que dice el manual sobre tu pregunta:\n\n" + fichas
        ans.warnings.append(f"El modelo no pudo redactar la respuesta ({e}); se muestran las fichas del manual.")
        return ans
    ans.mode, ans.text = "slm", text
    calls = client.usage.since(mark) if hasattr(client, "usage") else []
    ans.model = getattr(client, "name", "").split(":", 1)[-1]
    ans.provider = "huggingface" if isinstance(client, CloudClient) else "ollama" if isinstance(client, OllamaClient) else ""
    ans.tokens_in, ans.tokens_out = sum(c.tokens_in for c in calls), sum(c.tokens_out for c in calls)
    ans.seconds = sum(c.seconds for c in calls)
    price = getattr(client, "price", None)
    if price:
        ans.cost_usd = (ans.tokens_in * price[0] + ans.tokens_out * price[1]) / 1e6
    return ans
