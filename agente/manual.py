"""Manual de consulta del flujo de estimación IDW (v3).

El manual vive en `catalog/manual_idw.yaml`: un apartado por herramienta con qué hace, cuándo usarla y una ficha por cada
parámetro de su conf_*.json (qué significa, qué pasa si lo subes o lo bajas, qué recomendar). De ahí salen tres cosas:

  · `render_markdown()`  → docs/MANUAL_PARAMETROS.md, el manual descargable;
  · `Manual.find()`      → búsqueda de las fichas relevantes para una duda (la usa el chatbot);
  · `coverage()`         → comprueba que CADA clave de los conf reales esté documentada (una prueba lo exige).

No hay modelo aquí: la búsqueda es léxica y determinista, así el chatbot responde con fichas reales y no inventa.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .textutil import normalize

DEFAULT_MANUAL = Path(__file__).resolve().parents[1] / "catalog" / "manual_idw.yaml"


class ManualError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- esquema YAML
class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParamDoc(_S):
    key: str                                  # clave del conf; admite [*] (lista) y * (cualquier nombre): 'pasadas[*].Potencia', 'topes.*.*.tope'
    title: str
    meaning: str
    effect: str = ""                          # qué pasa si lo subes / lo bajas / lo cambias
    advice: str = ""                          # recomendación práctica (orientación general, no una regla fija)
    values: str = ""                          # valores admitidos o unidad
    aliases: list[str] = Field(default_factory=list)   # otras formas de nombrarlo
    conf: str = ""                            # si es distinto del conf de la herramienta


class ToolDoc(_S):
    conf: str = ""
    title: str = ""
    summary: str
    when: str = ""
    steps: list[str] = Field(default_factory=list)
    reads: str = ""
    writes: str = ""
    cautions: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    params: list[ParamDoc] = Field(default_factory=list)


class Concept(_S):
    id: str
    title: str
    text: str
    aliases: list[str] = Field(default_factory=list)


class ManualDoc(_S):
    version: int = 1
    title: str = "Manual de parámetros"
    intro: str = ""
    concepts: list[Concept] = Field(default_factory=list)
    tools: dict[str, ToolDoc]


# ---------------------------------------------------------------------------------------------- entradas buscables
@dataclass
class Entry:
    id: str
    kind: str                                 # param | tool | concept
    tool: str
    conf: str
    key: str
    title: str
    meaning: str = ""
    effect: str = ""
    advice: str = ""
    values: str = ""
    aliases: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def label(self) -> str:
        if self.kind == "param":
            return f"{self.title} ({self.conf} · {self.key})"
        return self.title


_STOP = set("""de la el los las un una unos unas y o u en que por para con sin sobre al del se su sus es son como cual cuales cuando mi mis
lo le les si no mas muy ya pero entre hay puedo puede debo quiero quiere tengo tiene tienen esta este esto estos estas ese esa eso hace hacer
cambiar cambio cambia cambiando modificar modifico modifica subir subo sube bajar bajo baja aumentar aumento reducir reduzco ajustar ajusto
valor valores parametro parametros paso pasos proceso procesos explica explicame dime dame sirve usar uso uso usa
pasa ocurre sucede efecto pasaria tal vez""".split())


def _stem(w: str) -> str:
    w = re.sub(r"(es|s)$", "", w) if len(w) > 4 else w
    return w[:6] if len(w) > 6 else w


def tokens(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", normalize(text)) if len(w) >= 3 and w not in _STOP]


def _key_regex(pattern: str) -> re.Pattern[str]:
    """'pasadas[*].Potencia' / 'topes.*.*.tope' → regex que casa la clave aplanada de un conf real."""
    parts = re.split(r"(\[\*\]|\*)", pattern)
    rx = "".join(".+" if p == "*" else re.escape(p) for p in parts)
    return re.compile(rx + r"$")


def flatten_items(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """(clave, valor) de cada hoja de un conf, con las listas como [*] y los nombres de dominio, rol y tipo como '*'."""
    if isinstance(data, dict):
        if prefix in ("topes", "topes.*"):                       # topes → {variable: {dominio: {tope, metodo, parametro}}}
            for v in data.values():
                yield from flatten_items(v, prefix + ".*")
        elif prefix.endswith((".roles", ".tipos")) or prefix in ("roles", "tipos"):
            yield prefix + ".*", None                            # los nombres de columna son datos, no parámetros
        else:
            for k, v in data.items():
                yield from flatten_items(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(data, list):
        if not data:
            yield prefix + "[*]", None
        for x in data:
            yield from flatten_items(x, prefix + "[*]")
    else:
        yield prefix, data


def flatten_keys(data: Any, prefix: str = "") -> Iterator[str]:
    for k, _ in flatten_items(data, prefix):
        yield k


def values_for(entry_key: str, data: Any) -> list[Any]:
    """Valores que hoy tiene un conf para la clave (con comodines) de una ficha."""
    rx = _key_regex(entry_key)
    return [v for k, v in flatten_items(data) if rx.match(k) and v is not None]


# ---------------------------------------------------------------------------------------------- manual
class Manual:
    def __init__(self, doc: ManualDoc, tool_titles: Optional[dict[str, str]] = None):
        self.doc = doc
        self.tool_titles = tool_titles or {}
        self.entries: list[Entry] = []
        self._build()

    # ------------------------------------------------------------------ carga
    @classmethod
    def load(cls, path: Path | str | None = None, tool_titles: Optional[dict[str, str]] = None) -> "Manual":
        p = Path(path) if path else DEFAULT_MANUAL
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            doc = ManualDoc.model_validate(raw)
        except FileNotFoundError:
            raise ManualError(f"No existe el manual: {p}") from None
        except (yaml.YAMLError, ValidationError) as e:
            raise ManualError(f"Manual inválido ({p}):\n{e}") from e
        return cls(doc, tool_titles)

    def _build(self) -> None:
        for c in self.doc.concepts:
            self.entries.append(Entry(f"concepto:{c.id}", "concept", "", "", c.id, c.title, meaning=c.text, aliases=list(c.aliases)))
        for tid, t in self.doc.tools.items():
            title = t.title or self.tool_titles.get(tid, tid)
            self.entries.append(Entry(f"herramienta:{tid}", "tool", tid, t.conf, tid, title, meaning=t.summary, effect=t.when,
                                      aliases=list(t.aliases) + [tid], extra={"steps": t.steps, "reads": t.reads, "writes": t.writes,
                                                                             "cautions": t.cautions}))
            for p in t.params:
                conf = p.conf or t.conf
                self.entries.append(Entry(f"param:{tid}:{p.key}", "param", tid, conf, p.key, p.title, p.meaning, p.effect, p.advice,
                                          p.values, list(p.aliases)))
        # índice para la búsqueda
        self._fields: list[tuple[dict[str, float], str]] = []
        df: dict[str, int] = {}
        for e in self.entries:
            w: dict[str, float] = {}
            for text, weight in ((e.title, 3.0), (" ".join(e.aliases), 3.0), (e.key.replace("[*]", " ").replace("_", " "), 3.0),
                                 (e.meaning, 1.0), (e.effect + " " + e.advice + " " + e.values, 0.5),
                                 (self.tool_titles.get(e.tool, e.tool), 0.7)):
                for t in tokens(text):
                    w[t] = max(w.get(t, 0), weight)
            self._fields.append((w, e.id))
            for t in w:
                df[t] = df.get(t, 0) + 1
        n = len(self.entries)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    # ------------------------------------------------------------------ búsqueda
    def find(self, question: str, tools: Optional[list[str]] = None, k: int = 4, min_score: float = 2.5) -> list[tuple[Entry, float]]:
        q = list(dict.fromkeys(tokens(question)))
        if not q:
            return []
        tools = list(tools or [])
        scored: list[tuple[float, Entry]] = []
        for (w, _), e in zip(self._fields, self.entries):
            s = sum(self._idf.get(t, 0.0) * w[t] for t in q if t in w)
            if s <= 0:
                continue
            if e.tool and e.tool in tools:
                s *= 1.5
            if e.kind == "param":
                s *= 1.1                      # una duda suele ser sobre un parámetro concreto
            scored.append((s, e))
        scored.sort(key=lambda x: -x[0])
        out = [(e, s) for s, e in scored[:k] if s >= min_score]
        return out

    def get(self, entry_id: str) -> Optional[Entry]:
        return next((e for e in self.entries if e.id == entry_id), None)

    def tool_entry(self, tool_id: str) -> Optional[Entry]:
        return self.get(f"herramienta:{tool_id}")

    def params_of(self, tool_id: str) -> list[Entry]:
        return [e for e in self.entries if e.kind == "param" and e.tool == tool_id]

    # ------------------------------------------------------------------ cobertura
    def coverage(self, conf: str, data: Any) -> list[str]:
        """Claves del conf `data` que NINGUNA ficha documenta (lista vacía = todo documentado)."""
        rxs = [_key_regex(e.key) for e in self.entries if e.kind == "param" and e.conf == conf]
        missing = []
        for k in dict.fromkeys(flatten_keys(data)):
            if k.startswith("__"):
                continue
            if not any(rx.match(k) for rx in rxs):
                missing.append(k)
        return missing

    def documented_confs(self) -> list[str]:
        return sorted({e.conf for e in self.entries if e.kind == "param" and e.conf})

    # ------------------------------------------------------------------ texto
    def render_entry(self, e: Entry, current: str = "") -> str:
        """Ficha en Markdown (párrafo + viñetas: así se ve bien en la interfaz y en texto plano)."""
        if e.kind == "concept":
            return f"**{e.title}**\n\n{e.meaning}"
        if e.kind == "tool":
            L = [f"**{e.title}** (`{e.tool}`)", "", e.meaning, ""]
            if e.effect:
                L.append(f"- *Cuándo usarlo:* {e.effect}")
            if e.extra.get("steps"):
                L.append("- *Qué hace:* " + " → ".join(e.extra["steps"]))
            if e.conf:
                L.append(f"- *Configuración:* `{e.conf}`")
            L += [f"- ⚠ {c}" for c in e.extra.get("cautions", [])]
            return "\n".join(L).rstrip()
        L = [f"**{e.title}** — `{e.conf}` · `{e.key}`", "", e.meaning, ""]
        if e.values:
            L.append(f"- *Valores:* {e.values}")
        if e.effect:
            L.append(f"- *Si lo cambias:* {e.effect}")
        if e.advice:
            L.append(f"- *Recomendación:* {e.advice}")
        if current:
            L.append(f"- *Hoy en tu proyecto:* {current}")
        return "\n".join(L).rstrip()


# ---------------------------------------------------------------------------------------------- manual completo en Markdown
def render_markdown(manual: Manual, reg=None) -> str:
    d = manual.doc
    L = [f"# {d.title}", ""]
    if d.intro:
        L += [d.intro.strip(), ""]
    L += ["## Índice", ""]
    if d.concepts:
        L.append("- [Conceptos generales](#conceptos-generales)")
    for tid, t in d.tools.items():
        title = t.title or manual.tool_titles.get(tid, tid)
        L.append(f"- [{title}](#{re.sub(r'[^a-z0-9]+', '-', normalize(title)).strip('-')}) — `{t.conf or 'sin configuración'}`")
    if d.concepts:
        L += ["", "## Conceptos generales", ""]
        for c in d.concepts:
            L += [f"### {c.title}", "", c.text.strip(), ""]
    for tid, t in d.tools.items():
        title = t.title or manual.tool_titles.get(tid, tid)
        L += ["", f"## {title}", ""]
        L.append(f"`{tid}`" + (f" · configuración: `{t.conf}`" if t.conf else ""))
        L += ["", t.summary.strip(), ""]
        if t.when:
            L += [f"**Cuándo usarlo.** {t.when.strip()}", ""]
        if t.steps:
            L += ["**Qué hace, paso a paso**", ""] + [f"{i}. {s}" for i, s in enumerate(t.steps, 1)] + [""]
        if t.reads or t.writes:
            L += [f"**Lee:** {t.reads or '—'}  ", f"**Genera:** {t.writes or '—'}", ""]
        for c in t.cautions:
            L.append(f"> ⚠ {c}")
        if t.cautions:
            L.append("")
        if t.params:
            L += ["### Parámetros", ""]
            for p in t.params:
                L += [f"#### {p.title}", "", f"`{p.conf or t.conf}` → `{p.key}`" + (f" · valores: {p.values}" if p.values else ""), "",
                      p.meaning.strip(), ""]
                if p.effect:
                    L += [f"- **Si lo cambias:** {p.effect.strip()}"]
                if p.advice:
                    L += [f"- **Recomendación:** {p.advice.strip()}"]
                L.append("")
    return "\n".join(L).rstrip() + "\n"
