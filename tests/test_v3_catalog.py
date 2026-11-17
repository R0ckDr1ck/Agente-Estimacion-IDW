"""v3 · catálogo enriquecido: cada herramienta trae resumen, sinónimos y ejemplos, y los ejemplos son una especificación ejecutable."""

from pathlib import Path

import pytest

from agente import rules
from agente.catalog import ToolRegistry

CAT = Path(__file__).resolve().parents[1] / "catalog" / "tools_idw.yaml"
REG = ToolRegistry.load([CAT])


def test_every_tool_has_summary_synonyms_and_several_examples():
    for t in REG:
        assert len(t.summary) > 60, t.id
        assert len(t.synonyms) >= 5, t.id
        assert len(t.examples) >= 5 and len(set(t.examples)) == len(t.examples), t.id


@pytest.mark.parametrize("tool,example", [(t.id, e) for t in REG for e in t.examples])
def test_every_catalog_example_is_understood_as_its_own_tool(tool, example):
    """Los ejemplos del catálogo son la especificación: si una frase de ejemplo no lleva a su herramienta, el léxico está incompleto."""
    out = rules.suggest(example, REG)
    assert out.intent == "run" and set(out.tools) == {tool}, (example, out.intent, out.tools)


def test_synonyms_do_not_steal_requests_from_other_tools():
    """Frases de una herramienta no deben disparar otra (los sinónimos amplían, no confunden)."""
    for t in REG:
        mine = rules.score_tools(rules.normalize(" ".join(t.examples)), REG)
        assert t.id in mine
    assert rules.suggest("estima las leyes y genera el reporte", REG).tools == ["estimar", "reporte"]
    assert rules.suggest("importa la base y luego compósita", REG).tools == ["importar", "compositar"]


def test_tool_listing_and_explanation_use_the_plain_language_summary():
    from agente.reporter import render_explain, render_tools
    assert REG.get("acotar").summary in render_explain(REG, ["acotar"])
    assert "Puedes pedirlo así" in render_explain(REG, ["acotar"])
    assert "Controla el efecto de las altas leyes" in render_tools(REG)
