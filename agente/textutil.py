"""Utilidades de texto compartidas."""

from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados. Base de toda comparación por palabras clave."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip()


def tail_lines(text: str, n: int = 25) -> list[str]:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-n:]
