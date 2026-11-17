"""Carpetas de trabajo: dónde están los archivos de ENTRADA (01_Data) y dónde se dejan los de SALIDA (02_Resultados).

La interfaz las pide al abrir. Las herramientas (Proyecto_IDW/Flujo_Estimacion) viven dentro del agente y no se mueven;
lo que cambia son las rutas de los conf_*.json, que se reubican con `Agent.align_paths_to_workspace()` (con respaldo).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TEXT_EXT = {".csv", ".obj", ".json", ".db", ".xlsx", ".txt"}


@dataclass
class WorkspaceCheck:
    data_dir: Optional[Path] = None
    results_dir: Optional[Path] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_dirs(data: str, results: str, *, create_results: bool = True) -> WorkspaceCheck:
    """Valida las dos carpetas. La de salida se crea si no existe; ambas deben ser distintas y escribibles (la de salida)."""
    r = WorkspaceCheck()
    data, results = (data or "").strip().strip('"'), (results or "").strip().strip('"')
    if not data:
        r.errors.append("Indica la carpeta de ENTRADA (donde está 01_Data).")
    if not results:
        r.errors.append("Indica la carpeta de SALIDA (donde se guardará 02_Resultados).")
    if r.errors:
        return r
    d, o = Path(data).expanduser(), Path(results).expanduser()
    if not d.is_absolute() or not o.is_absolute():
        r.errors.append("Escribe rutas completas (p. ej. D:\\MiProyecto\\01_Data).")
        return r
    d, o = d.resolve(), o.resolve()
    if not d.is_dir():
        r.errors.append(f"La carpeta de entrada no existe: {d}")
    if d == o:
        r.errors.append("La carpeta de entrada y la de salida deben ser distintas (las salidas no deben mezclarse con tus datos).")
    if not r.errors and not o.is_dir():
        if create_results:
            try:
                o.mkdir(parents=True, exist_ok=True)
                r.created.append(str(o))
            except OSError as e:
                r.errors.append(f"No pude crear la carpeta de salida {o}: {e}")
        else:
            r.errors.append(f"La carpeta de salida no existe: {o}")
    if not r.errors:
        probe = o / f".agente_prueba_{uuid.uuid4().hex[:8]}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            r.errors.append(f"No tengo permiso de escritura en la carpeta de salida {o}: {e}")
    if not r.errors:
        try:
            o.relative_to(d)
            r.warnings.append("La carpeta de salida está dentro de la de entrada: los resultados quedarán mezclados con tus datos.")
        except ValueError:
            pass
        if not any(d.iterdir()):
            r.warnings.append("La carpeta de entrada está vacía.")
        r.data_dir, r.results_dir = d, o
    return r


def inventory(data_dir: Path, depth: int = 2, limit: int = 40) -> list[tuple[str, str]]:
    """(subcarpeta, resumen) de lo que hay en la carpeta de entrada: sirve para confirmar que es la correcta."""
    out: list[tuple[str, str]] = []
    try:
        subs = sorted(p for p in Path(data_dir).iterdir() if p.is_dir())
    except OSError:
        return out
    for sub in subs[:limit]:
        counts: Counter[str] = Counter()
        try:
            for p in sub.rglob("*"):
                if p.is_file():
                    counts[p.suffix.lower() or "(sin ext.)"] += 1
                if sum(counts.values()) > 5000:
                    break
        except OSError:
            continue
        out.append((sub.name, ", ".join(f"{n} {ext}" for ext, n in counts.most_common(4)) or "vacía"))
    loose = Counter(p.suffix.lower() for p in Path(data_dir).iterdir() if p.is_file())
    if loose:
        out.append(("(archivos sueltos)", ", ".join(f"{n} {ext}" for ext, n in loose.most_common(4))))
    return out


def browse_folder(initial: str = "", title: str = "Elige una carpeta") -> Optional[str]:
    """Cuadro de diálogo del sistema para elegir carpeta. Corre en un proceso aparte (Tk no convive bien con Streamlit).
    Devuelve None si se cancela o no hay entorno gráfico."""
    code = ("import tkinter as tk\nfrom tkinter import filedialog\n"
            "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
            "print(filedialog.askdirectory(initialdir=%r, title=%r, mustexist=True) or '')\n") % (initial or str(Path.home()), title)
    try:
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600,
                             env={**os.environ, "PYTHONIOENCODING": "utf-8"}, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    path = (res.stdout or "").strip().splitlines()[-1].strip() if (res.stdout or "").strip() else ""
    return os.path.normpath(path) if path else None
