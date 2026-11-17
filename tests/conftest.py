"""Fixtures compartidas. Ninguna prueba toca el proyecto real salvo test_real_project.py (solo lectura)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from agente.config import AgentConfig            # noqa: E402
from agente.orchestrator import Agent, AgentIO   # noqa: E402

DEMO = BASE / "examples" / "demo_project"
DEMO_CATALOG = BASE / "catalog" / "tools_demo.yaml"
IDW_CATALOG = BASE / "catalog" / "tools_idw.yaml"


class ScriptedIO(AgentIO):
    """E/S de prueba: respuestas predefinidas y registro de lo preguntado."""

    def __init__(self, confirm: bool = False, answers: dict | None = None):
        self.confirm_answer, self.answers = confirm, answers or {}
        self.confirmations: list[tuple[str, list[str]]] = []
        self.asked: list[str] = []
        self.lines: list[str] = []

    def confirm(self, title, lines):
        self.confirmations.append((title, list(lines)))
        return self.confirm_answer

    def ask(self, spec, tool):
        self.asked.append(spec.name)
        return self.answers.get(spec.name)

    def line(self, tool_id, line):
        self.lines.append(line)


def make_cfg(tmp: Path, root: Path, catalog: Path) -> AgentConfig:
    cfg = AgentConfig.load(root=root, catalogs=[str(catalog)])
    cfg.runs_dir = tmp / "runs"
    cfg.templates_dir = tmp / "plantillas"
    cfg.llm.enabled = False
    cfg.python = sys.executable
    return cfg


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(DEMO, root)
    return root


@pytest.fixture
def demo_cfg(tmp_path: Path, demo_root: Path) -> AgentConfig:
    return make_cfg(tmp_path, demo_root, DEMO_CATALOG)


@pytest.fixture
def demo_agent(demo_cfg: AgentConfig) -> Agent:
    return Agent(demo_cfg, io=ScriptedIO())


def write_catalog(tmp: Path, text: str) -> Path:
    p = tmp / "cat.yaml"
    p.write_text(text, encoding="utf-8")
    return p
