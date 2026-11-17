"""Executor: corre una herramienta del catálogo como subproceso, con timeout y captura completa de salida.

Garantías:
  - Nunca usa shell: argv es una lista armada solo con placeholders del catálogo.
  - Salida combinada (stdout+stderr) a un .log por paso, en tiempo real, y a un callback opcional.
  - Timeout: mata el árbol de procesos completo.
  - Un fallo se traduce a un diagnóstico accionable (módulo faltante, archivo bloqueado, clave ausente...).
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


@dataclass
class StepResult:
    returncode: Optional[int]
    duration_s: float
    timed_out: bool
    tail: list[str] = field(default_factory=list)
    diagnosis: str = ""
    log_file: str = ""


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        proc.kill()


def run_command(argv: list[str], cwd: Path, timeout_s: int, log_path: Path,
                on_line: Callable[[str], None] | None = None, python: str = "") -> StepResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    t0 = time.perf_counter()
    tail: collections.deque[str] = collections.deque(maxlen=80)
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **kwargs)
    except OSError as e:
        msg = f"No se pudo lanzar el proceso: {e}"
        log_path.write_text(msg + "\n", encoding="utf-8")
        return StepResult(None, 0.0, False, [msg], msg, str(log_path))

    with open(log_path, "w", encoding="utf-8", newline="\n") as log:
        def pump() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                log.write(line + "\n")
                log.flush()
                tail.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception:
                        pass

        th = threading.Thread(target=pump, daemon=True)
        th.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            proc.wait()
        th.join(timeout=10)
        if timed_out:
            log.write(f"\n[agente] TIMEOUT: se detuvo el proceso tras {timeout_s} s\n")

    lines = [ln for ln in tail if ln.strip()]
    rc = proc.returncode
    diag = ""
    if timed_out:
        diag = (f"La herramienta superó el límite de {timeout_s} s y se detuvo. Si es normal que tarde más, "
                f"sube 'runner.timeout_s' de esta herramienta en el catálogo y reanuda la ejecución.")
    elif rc != 0:
        diag = diagnose("\n".join(lines), python)
    return StepResult(rc, time.perf_counter() - t0, timed_out, lines[-25:], diag, str(log_path))


_PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str], str], str]]] = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     lambda m, py: f"Falta el paquete Python '{m.group(1)}'. Instálalo con:  \"{py or 'python'}\" -m pip install {m.group(1).split('.')[0]}"),
    (re.compile(r"FileNotFoundError:.*?['\"]([^'\"]+)['\"]"),
     lambda m, py: f"No se encontró el archivo o carpeta: {m.group(1)}. Revisa la ruta en el conf correspondiente."),
    (re.compile(r"FileNotFoundError: (.+)"),
     lambda m, py: f"No se encontró un archivo: {m.group(1).strip()}"),
    (re.compile(r"PermissionError:.*?['\"]([^'\"]+)['\"]"),
     lambda m, py: f"Permiso denegado sobre {m.group(1)}. ¿Está abierto en Excel u otro programa? Ciérralo y reanuda."),
    (re.compile(r"sqlite3\.OperationalError: (.+)"),
     lambda m, py: f"Error de la base SQLite: {m.group(1).strip()}. Si dice 'locked', cierra los programas que usan Database.db."),
    (re.compile(r"MemoryError"),
     lambda m, py: "Memoria insuficiente. Cierra otros programas o reduce el tamaño del modelo/estimación."),
    (re.compile(r"KeyError: ['\"]?([^'\"\n]+)['\"]?"),
     lambda m, py: f"Falta la clave o columna '{m.group(1)}' (en el conf o en el CSV de entrada)."),
    (re.compile(r"UnicodeDecodeError: (.+)"),
     lambda m, py: f"Problema de codificación al leer un archivo ({m.group(1).strip()}). Guárdalo como UTF-8."),
    (re.compile(r"JSONDecodeError: (.+)"),
     lambda m, py: f"Un JSON está mal formado: {m.group(1).strip()}"),
]


def diagnose(text: str, python: str = "") -> str:
    for pat, fn in _PATTERNS:
        m = pat.search(text)
        if m:
            return fn(m, python)
    last = [ln for ln in text.splitlines() if ln.strip()]
    return f"La herramienta terminó con error. Última línea: {last[-1].strip()}" if last else "La herramienta terminó con error sin mensaje."


def launch_detached(argv: list[str], cwd: Path) -> int:
    """Para herramientas gráficas (kind: gui): se lanzan y no se espera su fin."""
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    p = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
    return p.pid


def backup_files(paths: list[str], max_mb: int) -> list[str]:
    """Copia cada archivo existente a <archivo>.bak_<fecha>. Omite carpetas y archivos > max_mb."""
    done = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for s in paths:
        p = Path(s)
        if p.is_file() and p.stat().st_size <= max_mb * 1024 * 1024:
            dst = p.with_name(p.name + f".bak_{stamp}")
            shutil.copy2(p, dst)
            done.append(str(dst))
    return done
