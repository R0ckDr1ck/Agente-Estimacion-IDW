"""CLI del agente:  python -m agente <comando>

  chat                     REPL conversacional
  plan  "solicitud"        interpreta y VERIFICA; no ejecuta (modo simulación)
  run   "solicitud"        plan + confirmación + ejecución
  exec  [run_id]           ejecuta o reanuda los pasos pendientes de una ejecución guardada
  check [herramientas]     solo verificación de prerrequisitos (sin LLM)
  fix-paths [--apply]      detecta (y corrige con respaldo) rutas obsoletas en los conf
  promote <run_id>         copia las plantillas generadas a su destino (nunca sobrescribe)
  init <destino>           usuario nuevo: crea las carpetas de entrada (01_Data) y salida (02_Resultados)
  tools | runs | show | schema | doctor | ui
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .catalog import CatalogError, ToolRegistry
from .config import BASE_DIR, AgentConfig
from .models import ParamSpec, PlannerOutput, RunState, ToolSpec
from .orchestrator import Agent, AgentIO

EXIT = {"completed": 0, "ready": 0, "info": 0, "failed": 1, "blocked": 2, "needs_input": 2, "aborted": 3}


# ============================================================================ E/S de consola
class CLIIO(AgentIO):
    def __init__(self, assume_yes: bool = False, stream: bool = True, interactive: bool = True):
        self.assume_yes, self.stream, self.interactive = assume_yes, stream, interactive

    def say(self, msg: str) -> None:
        print(msg, flush=True)

    def line(self, tool_id: str, line: str) -> None:
        if self.stream:
            print(f"    │ {line}", flush=True)

    def confirm(self, title: str, lines: list[str]) -> bool:
        print(f"\n⚠ {title}:")
        for ln in lines:
            print(f"   - {ln}")
        if self.assume_yes:
            print("   (--yes) confirmado.")
            return True
        if not self.interactive:
            return False
        return input("¿Confirmas? [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes")

    def ask(self, spec: ParamSpec, tool: ToolSpec) -> Optional[str]:
        if not self.interactive:
            return None
        print(f"\nPara '{tool.id}' necesito el parámetro '{spec.name}' ({spec.type}).")
        if spec.description:
            print(f"  Qué es : {spec.description}")
        if spec.why:
            print(f"  Por qué: {spec.why}")
        if spec.example:
            print(f"  Ejemplo: {spec.example}")
        val = input(f"  {spec.name} (Enter = omitir): ").strip().strip('"')
        return val or None


# ============================================================================ construcción
def build_agent(args: argparse.Namespace, io: Optional[AgentIO] = None) -> Agent:
    cfg = AgentConfig.load(getattr(args, "config", None), root=getattr(args, "root", None),
                           catalogs=getattr(args, "catalog", None) or None,
                           data_dir=getattr(args, "data_dir", None), results_dir=getattr(args, "results_dir", None))
    if getattr(args, "no_llm", False):
        cfg.llm.enabled = False
    if getattr(args, "provider", None):
        cfg.llm.provider = args.provider
    if getattr(args, "model", None):
        if cfg.llm.provider == "huggingface":
            cfg.llm.cloud.model = args.model               # la API key sale de la variable HF_TOKEN (nunca de un argumento)
        else:
            cfg.llm.primary.model = args.model
    if getattr(args, "llm_report", False):
        cfg.llm.report_with_llm = True
    return Agent(cfg, io=io or CLIIO(assume_yes=getattr(args, "yes", False),
                                     stream=not getattr(args, "quiet", False)))


def _parse_params(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--param espera NOMBRE=VALOR (recibí '{it}')")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def _selection(args: argparse.Namespace) -> Optional[PlannerOutput]:
    tools = [t.strip() for t in (getattr(args, "tools", None) or "").split(",") if t.strip()]
    wf, d, h = getattr(args, "workflow", None), getattr(args, "desde", None), getattr(args, "hasta", None)
    if tools or wf or d or h:
        return PlannerOutput(intent="run", tools=tools, workflow=wf, from_tool=d, to_tool=h, rationale="selección explícita (CLI)")
    return None


def _validate_selection(agent: Agent, out: PlannerOutput) -> None:
    from .planner import validate_against_catalog
    problems = validate_against_catalog(out, agent.registry)
    if problems:
        raise SystemExit("Selección inválida: " + "; ".join(problems))


# ============================================================================ comandos
def _show(agent: Agent, state: RunState) -> None:
    print("\n" + agent.report(state))


def cmd_plan(args) -> int:
    agent = build_agent(args)
    sel = _selection(args)
    params = _parse_params(args.param)
    if sel:
        _validate_selection(agent, sel)
        state = agent.plan_from(sel, args.request or "", params)
    else:
        if not args.request:
            raise SystemExit("Indica la solicitud entre comillas, o usa --tools/--workflow/--desde/--hasta.")
        state = agent.plan(args.request, params)
    _show(agent, state)
    print(f"(estado guardado en {agent.store.dir / state.run_id})")
    return EXIT.get(state.status, 0)


def cmd_run(args) -> int:
    agent = build_agent(args)
    sel = _selection(args)
    params = _parse_params(args.param)
    if sel:
        _validate_selection(agent, sel)
        state = agent.plan_from(sel, args.request or "", params)
    else:
        if not args.request:
            raise SystemExit("Indica la solicitud entre comillas, o usa --tools/--workflow/--desde/--hasta.")
        state = agent.plan(args.request, params)
    return _plan_then_execute(agent, state, args)


def _plan_then_execute(agent: Agent, state: RunState, args) -> int:
    _show(agent, state)
    if state.status in ("info", "needs_input"):
        return EXIT[state.status]
    if state.status == "ready" and not args.dry_run and not args.yes:
        if not isinstance(agent.io, CLIIO) or not agent.io.interactive:
            return EXIT["ready"]
        if input("\n¿Ejecutar este plan? [s/N] ").strip().lower() not in ("s", "si", "sí", "y", "yes"):
            print("No se ejecutó nada (el plan quedó guardado).")
            return 0
    state = agent.execute(state, assume_yes=args.yes, dry_run=args.dry_run,
                          accept_templates=args.accept_templates, apply_path_fixes=args.apply_path_fixes)
    _show(agent, state)
    return EXIT.get(state.status, 0)


def cmd_exec(args) -> int:
    agent = build_agent(args)
    state = agent.resume(args.run_id)
    state = agent.execute(state, params=_parse_params(args.param), assume_yes=args.yes, dry_run=args.dry_run,
                          accept_templates=args.accept_templates, apply_path_fixes=args.apply_path_fixes)
    _show(agent, state)
    return EXIT.get(state.status, 0)


def cmd_check(args) -> int:
    args.no_llm = True
    agent = build_agent(args)
    tools = list(args.tools_pos or [])
    flow = [t.id for t in agent.registry.flow()]
    out = PlannerOutput(intent="status", tools=tools, from_tool=None if tools else flow[0],
                        to_tool=None if tools else flow[-1], rationale="verificación solicitada por CLI")
    _validate_selection(agent, out)
    state = agent.plan_from(out, "check " + " ".join(tools), _parse_params(args.param))
    _show(agent, state)
    return EXIT.get(state.status, 0)


def cmd_fix_paths(args) -> int:
    args.no_llm = True
    agent = build_agent(args)
    fixes = agent.propose_path_fixes(_parse_params(args.param))
    if not fixes:
        print("No se detectaron rutas obsoletas en los conf (para las herramientas con configuración activa).")
        return 0
    print(f"Rutas a corregir ({len(fixes)}):")
    for f in fixes:
        if f.new:
            print(f"\n- {f.config_file} → {f.jpath}\n    - {f.old}\n    + {f.new}\n    ({f.reason})")
        else:
            print(f"\n- {f.config_file} → {f.jpath}  [AMBIGUO, no se tocará]\n    {f.old}\n    " + "\n    ".join(f.ambiguous))
    todo = [f for f in fixes if f.new and not f.ambiguous]
    if not args.apply:
        print(f"\nModo simulación. Para aplicar {len(todo)} corrección(es) con respaldo:  python -m agente fix-paths --apply")
        return 0
    if todo:
        io = agent.io
        if not io.confirm(f"Se modificarán {len({f.config_file for f in todo})} archivo(s) conf (con respaldo previo)",
                          sorted({f.config_file for f in todo})):
            print("Cancelado.")
            return 3
        for line in agent.apply_path_fixes(todo):
            print(line)
    return 0


def cmd_promote(args) -> int:
    agent = build_agent(args)
    state = agent.store.load(args.run_id) if args.run_id else agent.store.latest()
    if state is None:
        raise SystemExit("No hay ejecuciones.")
    kinds = ("config", "data") if args.kind == "all" else ("config",)
    done = agent.promote_templates(state, kinds)
    agent.store.save(state)
    print("\n".join(f"Copiado: {d}" for d in done) or "No había plantillas pendientes (o el destino ya existe: no se sobrescribe).")
    return 0


def cmd_tools(args) -> int:
    from .reporter import render_tools
    args.no_llm = True
    agent = build_agent(args)
    print(agent.registry.dump_schemas() if args.json else render_tools(agent.registry))
    return 0


def cmd_runs(args) -> int:
    agent = build_agent(args)
    for st in agent.store.list()[: args.n]:
        print(f"{st.run_id}  {st.status:11s}  {len(st.plan.steps)} paso(s)  {st.request[:70]}")
    return 0


def cmd_show(args) -> int:
    agent = build_agent(args)
    state = agent.store.load(args.run_id) if args.run_id else agent.store.latest()
    if state is None:
        raise SystemExit("No hay ejecuciones.")
    _show(agent, state)
    return 0


def cmd_schema(args) -> int:
    from .schemas import config_json_schema
    args.no_llm = True
    agent = build_agent(args)
    if args.tool:
        sch = config_json_schema(agent.registry.get(args.tool))
        print(json.dumps(sch, ensure_ascii=False, indent=2) if sch else "(esa herramienta no declara config_template.fields)")
    else:
        print(agent.registry.dump_schemas())
    return 0


def cmd_ask(args) -> int:
    """Duda sobre un proceso o parámetro: responde con el manual (y el modelo, si hay)."""
    from . import chat
    agent = build_agent(args)
    ans = agent.ask(" ".join(args.question))
    print(chat.render_answer(ans))
    return 0


def cmd_datos(args) -> int:
    """Qué datos hay y qué se puede hacer con ellos (todas las rutas, no solo la del conf actual)."""
    from . import datos
    agent = build_agent(args)
    print(datos.render_markdown(agent.data_report(), agent.registry))
    return 0


def cmd_init(args) -> int:
    """Usuario nuevo: crea <destino>/<nombre>/{01_Data, 02_Resultados} con sus subcarpetas, LEEME y (opcional) plantillas."""
    from . import nuevo_usuario as nu
    from .config import save_workspace
    registry = None
    if args.plantillas:
        registry = ToolRegistry.load(AgentConfig.load(args.config, root=args.root, catalogs=args.catalog or None).catalogs)
    r = nu.crear_estructura(args.destino, args.nombre, plantillas=args.plantillas, registry=registry)
    if not r.ok:
        for e in r.errores:
            print(f"[ERR] {e}")
        return 1
    print(nu.resumen_arbol(r))
    print(f"\nCreadas: {len(r.creadas)} · ya existían (sin tocar): {len(r.existentes)}" + (f" · plantillas: {len(r.plantillas)}" if args.plantillas else ""))
    print(f"ENTRADA : {r.data_dir}\nSALIDA  : {r.results_dir}")
    if args.usar:
        save_workspace(r.data_dir, r.results_dir)
        print("Quedaron recordadas: la interfaz las mostrará al abrir (pulsa «Continuar»).")
    print("\nSiguiente: copia tus archivos a 01_Data (cada subcarpeta trae un LEEME.txt) y abre la interfaz (run_ui.bat).")
    return 0


def cmd_manual(args) -> int:
    """Escribe el manual de parámetros en Markdown (por defecto docs/MANUAL_PARAMETROS.md)."""
    from . import manual as M
    agent = build_agent(args)
    out = Path(args.out) if args.out else BASE_DIR / "docs" / "MANUAL_PARAMETROS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(M.render_markdown(agent.manual, agent.registry), encoding="utf-8")
    print(f"Manual escrito en {out}")
    return 0


def cmd_doctor(args) -> int:
    import httpx
    ok = True
    def line(good: bool, msg: str) -> None:
        nonlocal ok
        ok &= good
        print(f"[{'OK ' if good else 'XX '}] {msg}")
    try:
        cfg = AgentConfig.load(args.config, root=args.root, catalogs=args.catalog or None)
    except Exception as e:
        print(f"[XX ] config/agent.yaml inválido: {e}")
        return 1
    line(Path(cfg.python).exists(), f"Intérprete de las herramientas: {cfg.python}")
    line(cfg.idw_root.is_dir(), f"Raíz del proyecto: {cfg.idw_root}")
    line(cfg.config_path.is_dir(), f"Carpeta de configuraciones: {cfg.config_path}")
    try:
        reg = ToolRegistry.load(cfg.catalogs)
        line(True, f"Catálogo válido: {len(reg)} herramientas ({', '.join(reg.sources)})")
    except CatalogError as e:
        line(False, str(e))
    ep = cfg.llm.primary
    if cfg.llm.enabled and cfg.llm.provider == "huggingface":
        c = cfg.llm.cloud
        line(bool(c.resolve_key()), f"API key de Hugging Face en la variable {c.token_env}" + ("" if c.resolve_key() else "  → créala en huggingface.co/settings/tokens"))
        line(1 <= len(c.models) <= c.max_models, f"Modelos en la nube: {len(c.models)} (máx. {c.max_models}); por defecto {c.model}")
    elif cfg.llm.enabled:
        try:
            r = httpx.get(f"{ep.base_url.rstrip('/')}/api/tags", timeout=5)
            names = [m["name"] for m in r.json().get("models", [])]
            line(True, f"Ollama responde en {ep.base_url}; modelos: {', '.join(names) or '(ninguno)'}")
            line(ep.model in names, f"Modelo del planner '{ep.model}' instalado" + ("" if ep.model in names else f"  → ollama pull {ep.model}"))
        except Exception as e:
            line(False, f"Ollama no responde en {ep.base_url} ({type(e).__name__}). Sin él el agente usa solo reglas por palabras clave.")
    else:
        print("[-- ] LLM desactivado (llm.enabled = false): solo reglas por palabras clave")
    return 0 if ok else 1


def cmd_ui(args) -> int:
    env = {**os.environ}
    if args.config:
        env["AGENTE_CONFIG"] = str(args.config)
    if args.root:
        env["AGENTE_ROOT"] = str(args.root)
    if args.catalog:
        env["AGENTE_CATALOGS"] = os.pathsep.join(args.catalog)
    app = Path(__file__).with_name("ui_streamlit.py")
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "false"],
                           env=env, cwd=str(BASE_DIR))


# ============================================================================ REPL
HELP = """Comandos del chat:
  <texto>        pide algo en lenguaje natural (se interpreta y verifica; no ejecuta hasta que confirmes)
  /plan          muestra de nuevo el último plan        /run [--yes]   ejecuta el último plan
  /status        recalcula la verificación              /fix           corrige rutas obsoletas (con respaldo)
  /templates     copia las plantillas de conf generadas /resume        reanuda la última ejecución fallida
  /datos         qué datos hay y qué se puede hacer     (también: «¿qué pasa si subo la potencia?» responde con el manual)
  /tools         lista las herramientas                 /runs          historial
  /help          esta ayuda                             /quit          salir"""


def cmd_chat(args) -> int:
    agent = build_agent(args)
    cl = agent.cfg.llm
    shown = "solo reglas" if not cl.enabled else (f"☁ {cl.cloud.model}" if cl.provider == "huggingface" else f"💻 {cl.primary.model}")
    print(f"Agente de Estimación IDW · raíz: {agent.cfg.idw_root} · modelo: {shown}\n{HELP}\n")
    state: Optional[RunState] = None
    while True:
        try:
            text = input("agente> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        try:
            if text in ("/quit", "/exit", "/q"):
                return 0
            elif text == "/help":
                print(HELP)
            elif text == "/datos":
                cmd_datos(args)
            elif text == "/tools":
                from .reporter import render_tools
                print(render_tools(agent.registry))
            elif text == "/runs":
                for st in agent.store.list()[:10]:
                    print(f"{st.run_id}  {st.status:11s}  {st.request[:70]}")
            elif text == "/plan" and state:
                _show(agent, state)
            elif text == "/status" and state:
                agent.prepare(state)
                agent.store.save(state)
                _show(agent, state)
            elif text.startswith("/run") and state:
                state = agent.execute(state, assume_yes="--yes" in text)
                _show(agent, state)
            elif text == "/fix":
                args.apply = True
                args.param = []
                cmd_fix_paths(args)
                if state:
                    agent.prepare(state)
            elif text == "/templates" and state:
                for d in agent.promote_templates(state):
                    print(f"Copiado: {d}")
                agent.prepare(state)
            elif text == "/resume":
                state = agent.resume()
                state = agent.execute(state)
                _show(agent, state)
            elif text.startswith("/"):
                print("Comando no reconocido o sin plan activo. /help")
            else:
                req = f"{state.request}. {text}" if state and state.status == "needs_input" else text
                state = agent.plan(req)
                _show(agent, state)
                if state.status == "ready" and state.planner and state.planner.intent == "run" and isinstance(agent.io, CLIIO):
                    if input("¿Ejecutar ahora? [s/N] ").strip().lower() in ("s", "si", "sí", "y", "yes"):
                        state = agent.execute(state)
                        _show(agent, state)
        except KeyboardInterrupt:
            print("\n(interrumpido)")
        except Exception as e:                                 # el REPL no debe morir por un error de un comando
            logging.getLogger("agente").exception("error en el chat")
            print(f"✘ Error: {type(e).__name__}: {e}")


# ============================================================================ parser
def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agente", description="Agente de Estimación IDW",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--config", type=Path, help="config/agent.yaml alternativo")
    p.add_argument("--root", type=Path, help="raíz del proyecto (sobrescribe idw_root)")
    p.add_argument("--catalog", action="append", help="catálogo YAML (repetible; sobrescribe agent.yaml)")
    p.add_argument("--no-llm", action="store_true", help="solo reglas por palabras clave")
    p.add_argument("--provider", choices=["huggingface", "ollama"], help="dónde corre el modelo (por defecto el de agent.yaml: huggingface)")
    p.add_argument("--model", help="modelo: id de Hugging Face (p. ej. google/gemma-3-4b-it) o nombre de Ollama (p. ej. gemma3:4b)")
    p.add_argument("--data-dir", type=Path, help="carpeta de ENTRADA (equivale a 01_Data); por defecto la de Proyecto_IDW")
    p.add_argument("--results-dir", type=Path, help="carpeta de SALIDA (equivale a 02_Resultados); por defecto la de Proyecto_IDW")
    p.add_argument("--llm-report", action="store_true", help="resumen en prosa del SLM en el informe")
    p.add_argument("--quiet", action="store_true", help="no mostrar la salida de las herramientas en vivo")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def selector(sp):
        sp.add_argument("--tools", help="ids separados por coma (sin LLM)")
        sp.add_argument("--workflow", help="id de workflow (sin LLM)")
        sp.add_argument("--desde", help="primer paso (sin LLM)")
        sp.add_argument("--hasta", help="último paso (sin LLM)")
        sp.add_argument("--param", action="append", metavar="NOMBRE=VALOR")

    def run_opts(sp):
        sp.add_argument("--yes", action="store_true", help="confirma TODO (incluidos los pasos destructivos)")
        sp.add_argument("--dry-run", action="store_true", help="verifica y no ejecuta")
        sp.add_argument("--accept-templates", nargs="?", const="config", choices=["config", "all"],
                        help="copia las plantillas generadas a su destino (config = solo conf; all = también datos de ejemplo)")
        sp.add_argument("--apply-path-fixes", action="store_true", help="corrige antes las rutas obsoletas (con respaldo)")

    s = sub.add_parser("chat"); s.set_defaults(fn=cmd_chat, yes=False)
    s = sub.add_parser("plan"); s.add_argument("request", nargs="?"); selector(s); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("run"); s.add_argument("request", nargs="?"); selector(s); run_opts(s); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("exec", aliases=["resume"]); s.add_argument("run_id", nargs="?")
    s.add_argument("--param", action="append", metavar="NOMBRE=VALOR"); run_opts(s); s.set_defaults(fn=cmd_exec)
    s = sub.add_parser("check"); s.add_argument("tools_pos", nargs="*", metavar="herramienta")
    s.add_argument("--param", action="append"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("fix-paths"); s.add_argument("--apply", action="store_true"); s.add_argument("--yes", action="store_true")
    s.add_argument("--param", action="append"); s.set_defaults(fn=cmd_fix_paths)
    s = sub.add_parser("promote"); s.add_argument("run_id", nargs="?"); s.add_argument("--kind", choices=["config", "all"], default="config")
    s.set_defaults(fn=cmd_promote)
    s = sub.add_parser("tools"); s.add_argument("--json", action="store_true", help="esquemas function-calling"); s.set_defaults(fn=cmd_tools)
    s = sub.add_parser("runs"); s.add_argument("-n", type=int, default=15); s.set_defaults(fn=cmd_runs)
    s = sub.add_parser("show"); s.add_argument("run_id", nargs="?"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("schema"); s.add_argument("--tool"); s.set_defaults(fn=cmd_schema)
    s = sub.add_parser("ask"); s.add_argument("question", nargs="+", help="tu duda entre comillas"); s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("datos"); s.set_defaults(fn=cmd_datos)
    s = sub.add_parser("manual"); s.add_argument("--out", help="archivo .md de salida"); s.set_defaults(fn=cmd_manual)
    s = sub.add_parser("init", help="usuario nuevo: crea las carpetas de entrada y salida")
    s.add_argument("destino", help="carpeta (ruta completa) donde se creará el proyecto")
    s.add_argument("--nombre", default="Mi_Proyecto", help="nombre de la carpeta del proyecto (por defecto Mi_Proyecto)")
    s.add_argument("--plantillas", action="store_true", help="deja plantillas CSV para llenar en Plantillas_para_llenar/")
    s.add_argument("--usar", action="store_true", help="recuerda estas carpetas para que la interfaz las proponga al abrir")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("doctor"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("ui"); s.set_defaults(fn=cmd_ui)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = make_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except CatalogError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
