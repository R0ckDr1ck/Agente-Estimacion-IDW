"""v3 · modelos en la nube (Hugging Face): cliente, errores, clave que nunca se filtra y plan con el modelo remoto.

Ninguna prueba sale a internet: se usa httpx.MockTransport.
"""

import json
import time
from pathlib import Path

import httpx
import pytest

from agente import llm
from agente.catalog import ToolRegistry
from agente.config import AgentConfig, CloudConfig, CloudModel
from agente.llm import CloudClient, LLMError, OllamaClient, build_client, hf_catalog, probe_model
from agente.planner import Planner

BASE = Path(__file__).resolve().parents[1]
CAT = BASE / "catalog" / "tools_idw.yaml"
KEY = "hf_SECRETO_DE_PRUEBA_123"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


def cloud_cfg(**kw) -> CloudConfig:
    base = dict(model="org/modelo-a", models=[CloudModel(id="org/modelo-a", price_in=0.1, price_out=0.4)], retries=2)
    base.update(kw)
    return CloudConfig(**base)


def transport(handler):
    return httpx.MockTransport(handler)


def ok_body(text="hola", pin=12, pout=3):
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}}


def test_success_records_tokens_latency_and_sends_the_bearer_key():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"], seen["url"], seen["body"] = req.headers["authorization"], str(req.url), json.loads(req.content)
        return httpx.Response(200, json=ok_body("respuesta", 100, 20))

    c = CloudClient(cloud_cfg(), api_key=KEY, transport=transport(handler))
    assert c.chat([{"role": "user", "content": "hola"}]) == "respuesta"
    assert seen["auth"] == f"Bearer {KEY}" and seen["url"].endswith("/chat/completions")
    assert seen["body"]["model"] == "org/modelo-a" and seen["body"]["temperature"] == 0.0 and seen["body"]["stream"] is False
    call = c.usage.calls[0]
    assert (call.tokens_in, call.tokens_out) == (100, 20) and call.seconds > 0 and call.model == "org/modelo-a"
    assert c.price == (0.1, 0.4)


@pytest.mark.parametrize("code,needle", [(401, "API key"), (403, "API key"), (404, "no está servido"), (410, "retirado"),
                                         (402, "crédito"), (422, "rechazó")])
def test_http_errors_are_explained_in_spanish_and_never_leak_the_key(code, needle):
    c = CloudClient(cloud_cfg(), api_key=KEY, transport=transport(lambda r: httpx.Response(code, text=f"error con {KEY}"[:0] + "detalle")))
    with pytest.raises(LLMError) as e:
        c.chat([{"role": "user", "content": "x"}])
    assert needle in str(e.value) and KEY not in str(e.value)


def test_rate_limit_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, text="slow down", headers={"retry-after": "1"}) if calls["n"] < 3 else httpx.Response(200, json=ok_body())

    c = CloudClient(cloud_cfg(retries=2), api_key=KEY, transport=transport(handler))
    assert c.chat([{"role": "user", "content": "x"}]) == "hola" and calls["n"] == 3


def test_rate_limit_gives_up_after_the_retries():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(503, text="down")

    c = CloudClient(cloud_cfg(retries=1), api_key=KEY, transport=transport(handler))
    with pytest.raises(LLMError, match="503"):
        c.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 2


def test_empty_answer_and_malformed_answer_are_errors():
    for body in ({"choices": [{"message": {"content": None}}]}, {"choices": []}, {"x": 1}):
        c = CloudClient(cloud_cfg(), api_key=KEY, transport=transport(lambda r, b=body: httpx.Response(200, json=b)))
        with pytest.raises(LLMError):
            c.chat([{"role": "user", "content": "x"}])


def test_timeout_and_network_errors_are_llmerror():
    def boom(req):
        raise httpx.ConnectTimeout("t")

    c = CloudClient(cloud_cfg(), api_key=KEY, transport=transport(boom))
    with pytest.raises(LLMError, match="no respondió"):
        c.chat([{"role": "user", "content": "x"}])
    c2 = CloudClient(cloud_cfg(), api_key=KEY, transport=transport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    with pytest.raises(LLMError, match="internet"):
        c2.chat([{"role": "user", "content": "x"}])


def test_missing_key_is_a_clear_error_and_never_calls_the_network(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    called = []
    c = CloudClient(cloud_cfg(), transport=transport(lambda r: called.append(1) or httpx.Response(200, json=ok_body())))
    with pytest.raises(LLMError, match="HF_TOKEN"):
        c.chat([{"role": "user", "content": "x"}])
    assert called == []


def test_key_never_appears_in_repr_dump_or_config_serialisation(monkeypatch):
    from pydantic import SecretStr
    cfg = AgentConfig.load()
    cfg.llm.cloud.api_key = SecretStr(KEY)
    assert KEY not in repr(cfg) and KEY not in repr(cfg.llm.cloud) and KEY not in cfg.model_dump_json() and KEY not in str(cfg.llm.cloud.model_dump())
    assert KEY not in repr(CloudClient(cfg.llm.cloud, api_key=KEY))
    assert cfg.llm.cloud.resolve_key() == KEY
    monkeypatch.setenv("HF_TOKEN", "hf_otra")
    cfg.llm.cloud.api_key = None
    assert cfg.llm.cloud.resolve_key() == "hf_otra"                                  # sin clave escrita, usa la variable de entorno


def test_build_client_picks_the_provider(monkeypatch):
    cfg = AgentConfig.load()
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(LLMError, match="API key"):
        build_client(cfg.llm)
    monkeypatch.setenv("HF_TOKEN", KEY)
    c = build_client(cfg.llm, "Qwen/Qwen3-VL-30B-A3B-Instruct")
    assert isinstance(c, CloudClient) and c.model == "Qwen/Qwen3-VL-30B-A3B-Instruct" and c.price == (0.15, 0.60)
    cfg.llm.provider = "ollama"
    assert isinstance(build_client(cfg.llm, "qwen3-vl:4b-instruct"), OllamaClient)
    assert build_client(cfg.llm.primary).name.startswith("ollama:")                 # compatibilidad con v2


def test_configured_model_list_is_capped_at_five_and_has_the_equivalents_of_the_installed_ones():
    cfg = AgentConfig.load()
    ids = [m.id for m in cfg.llm.cloud.models]
    assert len(ids) == len(set(ids)) <= 5 and cfg.llm.cloud.model in ids
    assert "google/gemma-3-4b-it" in ids and any("qwen3-vl" in i.lower() for i in ids)     # gemma3:4b y qwen3-vl que ya tenías
    assert all(m.price_in is not None and m.price_out is not None for m in cfg.llm.cloud.models)
    with pytest.raises(ValueError):
        CloudConfig(models=[CloudModel(id=f"o/m{i}") for i in range(6)])


def test_probe_model_reports_ok_and_failure_like_the_class_notebook():
    good = probe_model(cloud_cfg(), "org/modelo-a", KEY, transport(lambda r: httpx.Response(200, json=ok_body("listo"))))
    assert good.ok and good.detail == "listo" and good.seconds >= 0
    bad = probe_model(cloud_cfg(), "org/modelo-a", KEY, transport(lambda r: httpx.Response(404, text="no")))
    assert not bad.ok and "404" in bad.detail


def test_public_catalog_lists_live_providers_and_prices():
    body = {"data": [{"id": "google/gemma-3-4b-it", "providers": [{"provider": "deepinfra", "status": "live", "pricing": {"input": 0.05, "output": 0.1}},
                                                                  {"provider": "x", "status": "offline"}]}]}
    cat = hf_catalog("https://router.example/v1", transport=transport(lambda r: httpx.Response(200, json=body)))
    assert cat["google/gemma-3-4b-it"] == {"providers": ["deepinfra"], "price_in": 0.05, "price_out": 0.1}
    assert hf_catalog("https://router.example/v1", transport=transport(lambda r: httpx.Response(500))) is None


# ------------------------------------------------------------------------------------------------ el planner con el modelo remoto
def planner_json(tool="estimar", intent="run"):
    return json.dumps({"intent": intent, "tools": [tool], "workflow": None, "from_tool": None, "to_tool": None, "params": {},
                       "confidence": 0.9, "needs_clarification": None, "rationale": "pide estimar"})


def test_planner_works_with_a_cloud_model_and_measures_tokens_and_cost(tmp_path):
    reg = ToolRegistry.load([CAT])
    cfg = AgentConfig.load()
    cfg.llm.cloud.models = [CloudModel(id="org/modelo-a", price_in=0.5, price_out=1.0)]
    client = CloudClient(cfg.llm.cloud, "org/modelo-a", KEY,
                         transport(lambda r: httpx.Response(200, json=ok_body(planner_json(), 2000, 100))))
    res = Planner(cfg, reg, client).plan("corre la estimación IDW")
    assert res.source == "slm" and res.output.tools == ["estimar"]
    m = res.metrics
    assert m.provider == "huggingface" and m.model == "org/modelo-a" and (m.tokens_in, m.tokens_out) == (2000, 100)
    assert m.cost_usd == pytest.approx((2000 * 0.5 + 100 * 1.0) / 1e6) and m.vram_gb is None


def test_planner_without_key_falls_back_to_rules_and_says_why(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    reg = ToolRegistry.load([CAT])
    cfg = AgentConfig.load()
    p = Planner(cfg, reg)
    res = p.plan("corre la estimación IDW")
    assert res.source == "rules" and res.output.tools == ["estimar"]
    assert any("API key" in w for w in res.warnings)


def test_planner_falls_back_to_rules_when_the_cloud_fails():
    reg = ToolRegistry.load([CAT])
    cfg = AgentConfig.load()
    client = CloudClient(cfg.llm.cloud, None, KEY, transport(lambda r: httpx.Response(401, text="no")))
    res = Planner(cfg, reg, client).plan("corre la estimación IDW")
    assert res.source == "rules" and any("API key" in w for w in res.warnings)


# ------------------------------------------------------------------------------------------------ diagnóstico de la clave (v3.1)
def whoami(role, perms=None):
    body = {"auth": {"type": "access_token", "accessToken": {"role": role}}}
    if role == "fineGrained":
        body["auth"]["accessToken"]["fineGrained"] = {"global": perms or [], "scoped": []}
    return transport(lambda r: httpx.Response(200, json=body))


def test_token_check_explains_a_fine_grained_token_without_inference_permission():
    chk = llm.check_token(KEY, transport=whoami("fineGrained", ["repo.content.read"]))
    assert chk.ok and chk.kind == "fineGrained" and chk.inference is False
    assert any("Make calls to Inference Providers" in a for a in chk.advice)
    assert KEY not in repr(chk)


def test_token_check_accepts_fine_grained_with_inference_and_write_tokens():
    assert llm.check_token(KEY, transport=whoami("fineGrained", ["inference.serverless.write"])).inference is True
    chk = llm.check_token(KEY, transport=whoami("write"))
    assert chk.ok and chk.kind == "write" and chk.inference is None
    assert llm.check_token(KEY, transport=whoami("read")).advice


def test_token_check_401_and_no_network():
    chk = llm.check_token(KEY, transport=transport(lambda r: httpx.Response(401, json={"error": "Invalid credentials"})))
    assert not chk.ok and "401" in chk.detail and KEY not in chk.detail + " ".join(chk.advice)

    def boom(req):
        raise httpx.ConnectError("sin red")

    assert "conectar" in llm.check_token(KEY, transport=transport(boom)).detail


def test_key_is_cleaned_of_quotes_spaces_and_bearer_prefix():
    assert llm.clean_key('  "Bearer hf_abc\n def"  ') == "hf_abcdef"
    cfg = cloud_cfg(api_key="  hf_zzz ")
    assert cfg.resolve_key() == "hf_zzz"


def test_403_for_missing_inference_permission_says_exactly_what_to_tick():
    msg = CloudClient.explain_status(403, "This authentication method does not have sufficient permissions to call Inference Providers on behalf of user")
    assert "Make calls to Inference Providers" in msg and "token es válido" in msg
    assert "API key" in CloudClient.explain_status(401)


def test_local_model_list_merges_configured_and_all_downloaded_models():
    from agente.llm import local_model_list
    got = local_model_list(["gemma3:4b", "llama3:latest"], ["llama3", "qwen3-vl:4b-instruct", "nomic-embed-text:latest", "gemma3:4b"])
    assert got == ["gemma3:4b", "llama3:latest", "qwen3-vl:4b-instruct"]              # sin duplicados ni modelos de embeddings
    assert local_model_list(["gemma3:4b"], None) == ["gemma3:4b"]                      # Ollama apagado: solo los del conf
