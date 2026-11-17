"""Mini 'JSON path' para leer y escribir claves anidadas de los conf_*.json.

Sintaxis:  a.b          clave anidada
           a.lista[2]   elemento por índice
           a.lista[*]   todos los elementos (solo al leer)
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\*|\d+)\]")


def parse(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for m in _TOKEN.finditer(path):
        if m.group(1) is not None:
            tokens.append(m.group(1))
        else:
            tokens.append("*" if m.group(2) == "*" else int(m.group(2)))
    return tokens


def _fmt(tokens: list[str | int]) -> str:
    out = ""
    for t in tokens:
        if isinstance(t, int):
            out += f"[{t}]"
        else:
            out += ("." if out else "") + t
    return out


def get_all(data: Any, path: str) -> list[tuple[str, Any]]:
    """Devuelve [(ruta_concreta, valor)]. Lista vacía si la clave no existe."""
    results: list[tuple[list[str | int], Any]] = [([], data)]
    for tok in parse(path):
        nxt: list[tuple[list[str | int], Any]] = []
        for trail, node in results:
            if tok == "*":
                if isinstance(node, list):
                    nxt.extend((trail + [i], v) for i, v in enumerate(node))
            elif isinstance(tok, int):
                if isinstance(node, list) and 0 <= tok < len(node):
                    nxt.append((trail + [tok], node[tok]))
            elif isinstance(node, dict) and tok in node:
                nxt.append((trail + [tok], node[tok]))
        results = nxt
    return [(_fmt(t), v) for t, v in results]


def get(data: Any, path: str, default: Any = None) -> Any:
    found = get_all(data, path)
    return found[0][1] if found else default


def exists(data: Any, path: str) -> bool:
    return bool(get_all(data, path))


def set_value(data: Any, concrete_path: str, value: Any) -> None:
    tokens = parse(concrete_path)
    node = data
    for tok in tokens[:-1]:
        node = node[tok]
    node[tokens[-1]] = value
