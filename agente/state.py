"""State Store (JSON atómico por ejecución) y traza estructurada (JSON Lines).

runs/<run_id>/state.json   estado completo: solicitud, plan, hallazgos, estado de cada paso -> permite reanudar
runs/<run_id>/trace.jsonl  un evento por línea (planner, preflight, paso iniciado/terminado...)
runs/<run_id>/<paso>.log   salida cruda de cada herramienta
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .models import RunState, now_iso

log = logging.getLogger("agente")


class RunStore:
    def __init__(self, runs_dir: Path):
        self.dir = Path(runs_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"

    def run_dir(self, run_id: str) -> Path:
        d = self.dir / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, state: RunState) -> Path:
        """Escritura atómica: un corte a mitad de guardado no deja un state.json corrupto."""
        state.touch()
        d = self.run_dir(state.run_id)
        fd, tmp = tempfile.mkstemp(dir=d, prefix="state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(state.model_dump_json(indent=2))
            os.replace(tmp, d / "state.json")
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return d / "state.json"

    def load(self, run_id: str) -> RunState:
        p = self.dir / run_id / "state.json"
        if not p.is_file():
            raise FileNotFoundError(f"No existe la ejecución '{run_id}' ({p})")
        return RunState.model_validate_json(p.read_text(encoding="utf-8"))

    def list(self) -> list[RunState]:
        out = []
        for d in sorted(self.dir.iterdir(), reverse=True):
            if (d / "state.json").is_file():
                try:
                    out.append(self.load(d.name))
                except Exception:
                    continue
        return out

    def latest(self, resumable_only: bool = False) -> Optional[RunState]:
        for st in self.list():
            if not resumable_only or st.status in ("failed", "blocked", "aborted", "ready", "running"):
                if st.plan.steps:
                    return st
        return None


class Trace:
    """Eventos JSON por línea. Cada evento: {ts, run_id, event, ...campos}."""

    def __init__(self, store: RunStore, run_id: str):
        self.path = store.run_dir(run_id) / "trace.jsonl"
        self.run_id = run_id

    def emit(self, event: str, **fields: Any) -> None:
        rec = {"ts": now_iso(), "run_id": self.run_id, "event": event, **fields}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError as e:                                  # la traza nunca debe romper la ejecución
            log.warning("no se pudo escribir la traza: %s", e)
        log.debug("%s %s", event, fields)
