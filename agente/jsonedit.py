"""Edición TEXTUAL de un valor dentro de un JSON (conserva sangría, orden, comentarios de formato y saltos de línea).

`set_value_textual(text, "ganancia.activo", False)` cambia solo ese valor escalar. Devuelve None si no puede hacerlo con seguridad
(clave ausente, valor que no es escalar, JSON compacto raro): el llamador decide si reescribe todo el archivo.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_SCALAR = re.compile(r'"(?:[^"\\]|\\.)*"|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')


def _skip_string(text: str, i: int) -> int:
    """`i` apunta a la comilla de apertura; devuelve el índice justo después de la de cierre."""
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return i


def _object_span(text: str, start: int) -> Optional[tuple[int, int]]:
    """`start` apunta a una «{»; devuelve (start, índice de su «}» de cierre)."""
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == '"':
            i = _skip_string(text, i)
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return start, i
        i += 1
    return None


def _find_key(text: str, lo: int, hi: int, key: str) -> Optional[int]:
    """Posición justo después de «"key":» de una clave que esté directamente dentro del objeto text[lo:hi] (profundidad 1)."""
    depth, i = 0, lo
    want = json.dumps(key, ensure_ascii=False)
    while i < hi:
        c = text[i]
        if c == '"':
            end = _skip_string(text, i)
            if depth == 1 and text[i:end] == want:
                m = re.compile(r"\s*:\s*").match(text, end)
                if m:
                    return m.end()
            i = end
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    return None


def set_value_textual(text: str, dotted_key: str, value: Any) -> Optional[str]:
    keys = dotted_key.split(".")
    lo, hi = 0, len(text)
    for n, key in enumerate(keys):
        if n == 0:                                            # la raíz: saltar hasta su «{»
            j = text.find("{")
            if j < 0:
                return None
            span = _object_span(text, j)
            if span is None:
                return None
            lo, hi = span[0], span[1] + 1
        pos = _find_key(text, lo, hi, key)
        if pos is None:
            return None
        if n == len(keys) - 1:
            m = _SCALAR.match(text, pos)
            if m is None:
                return None
            return text[:m.start()] + json.dumps(value, ensure_ascii=False) + text[m.end():]
        if pos >= len(text) or text[pos] != "{":
            return None
        span = _object_span(text, pos)
        if span is None:
            return None
        lo, hi = span[0], span[1] + 1
    return None
