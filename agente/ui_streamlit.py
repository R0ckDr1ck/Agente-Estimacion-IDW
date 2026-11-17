"""Agente de Estimación IDW · interfaz web local (Streamlit):  python -m agente ui     (o run_ui.bat)

Capa fina sobre el MISMO Agent que usa la CLI: no hay lógica de negocio aquí.

  0. Al abrir pide la carpeta de ENTRADA (01_Data) y la de SALIDA (02_Resultados) y reubica TODAS las rutas de TODOS los conf.
  1. «Solicitud»            escribir lo que quieres → ver plan y verificación → resolver faltantes → ejecutar → resultados
  2. «Consulta»             chatbot: dudas sobre procesos y parámetros, respondidas con el manual
  3. «Datos»                qué datos hay (collar, survey, assay, compósitos, modelo…), qué rutas son viables y qué puedes ejecutar
  4. «Formatos y plantillas» qué archivo necesita cada herramienta, con plantilla .csv/.json descargable
  5. «Parámetros»           cargar tablas CSV a los conf y editar valores simples
  6. «Manual»               manual de parámetros (buscable y descargable)
  7. «Reporte final» · «Métricas» · «Evaluación» (modelos de Hugging Face / locales) · «Historial» · «Herramientas»
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # permite `streamlit run` directo

import pandas as pd                                                # noqa: E402
import streamlit as st                                             # noqa: E402

from pydantic import SecretStr                                                      # noqa: E402

from agente import chat as chat_mod, datos, pathfix, evaluation, formats, manual as manual_mod, metrics, results, tablas, workspace   # noqa: E402
from agente import nuevo_usuario as nu                                              # noqa: E402
from agente import ui_theme as T                                                   # noqa: E402
from agente.config import BASE_DIR, AgentConfig, load_saved_workspace, save_workspace   # noqa: E402
from agente.llm import check_token, hf_catalog, installed_models, local_model_list, probe_model    # noqa: E402
from agente.models import AnswerInfo, PlannerOutput, RunState       # noqa: E402
from agente.orchestrator import Agent, AgentIO                     # noqa: E402
from agente.reporter import ESTADO                                 # noqa: E402

TITLE = "Agente de Estimación IDW"
st.set_page_config(page_title=TITLE, page_icon="🧭", layout="wide")
T.inject()
ss = st.session_state


# ===================================================================================== infraestructura
class UIIO(AgentIO):
    """E/S no interactiva: las confirmaciones vienen de casillas de la página; el avance se muestra en vivo."""

    def __init__(self, confirm_flag: bool = False, log_ph=None, bar=None, total: int = 0):
        self.confirm_flag, self.ph, self.bar, self.total, self.started, self.buf = confirm_flag, log_ph, bar, total, 0, []

    def confirm(self, title, lines):
        return self.confirm_flag

    def say(self, msg):
        if msg.startswith("▶") and self.bar is not None:
            self.bar.progress(self.started / max(self.total, 1), text=f"Paso {self.started + 1} de {self.total}: {msg[1:].strip()}")
            self.started += 1

    def line(self, tool_id, line):
        self.buf.append(f"[{tool_id}] {line}")
        if self.ph is not None:
            self.ph.code("\n".join(self.buf[-30:]), language="text")


def make_agent(ws: dict, root: str, catalog: str, use_llm: bool, model: str, io: AgentIO,
               provider: str = "", api_key: str = "") -> Agent:
    cfg = AgentConfig.load(os.environ.get("AGENTE_CONFIG") or None, root=root or None, catalogs=[catalog] if catalog else None,
                           data_dir=ws["data_dir"], results_dir=ws["results_dir"])
    cfg.llm.enabled = use_llm
    if provider:
        cfg.llm.provider = provider                                        # type: ignore[assignment]
    if cfg.llm.provider == "huggingface":
        if model:
            cfg.llm.cloud.model = model
        if api_key:
            cfg.llm.cloud.api_key = SecretStr(api_key)                     # solo en memoria: nunca se escribe en disco
    elif model:
        cfg.llm.primary.model = model
    return Agent(cfg, io=io)


@st.cache_data(ttl=20, show_spinner=False)
def _installed(base_url: str) -> list[str] | None:
    return installed_models(base_url)


@st.cache_data(ttl=600, show_spinner=False)
def _hf_catalog(base_url: str):
    return hf_catalog(base_url)


@st.cache_data(show_spinner=False)
def _load_report(path: str, mtime: float):
    return results.load_report(Path(path))


def download(label: str, data: str | bytes, name: str, mime: str, key: str, **kw) -> None:
    st.download_button(label, data=data.encode("utf-8") if isinstance(data, str) else data, file_name=name, mime=mime, key=key, **kw)


# ===================================================================================== 0. carpetas de trabajo
base = AgentConfig.load(os.environ.get("AGENTE_CONFIG") or None)


def _browse(key: str, title: str) -> None:
    p = workspace.browse_folder(ss.get(key, ""), title)
    if p:
        ss[key] = p


@st.cache_resource(show_spinner=False)
def _registry():
    from agente.catalog import ToolRegistry
    return ToolRegistry.load(base.catalogs)


def _crear_carpetas() -> None:
    """Botón «Crear mis carpetas»: crea la estructura y rellena las dos rutas de abajo (nada de lo que ya exista se toca)."""
    r = nu.crear_estructura(ss.nu_dest, ss.nu_name, plantillas=ss.nu_tpl, registry=_registry())
    ss.nu_result = r
    if r.ok:
        ss.in_dir, ss.out_dir = str(r.data_dir), str(r.results_dir)


def nuevo_usuario(saved: dict) -> None:
    """Panel «¿Eres usuario nuevo?»: crea las carpetas de entrada y salida y deja subir los primeros archivos."""
    ss.setdefault("nu_dest", str(Path.home() / "Agente_IDW"))
    ss.setdefault("nu_name", "Mi_Proyecto")
    ss.setdefault("nu_tpl", True)
    ss.setdefault("nu_up", 0)
    with st.expander("🆕 ¿Eres usuario nuevo? Crea tus carpetas de entrada y de salida", expanded=not saved):
        st.markdown(
            "El agente trabaja con **dos carpetas**: la de **entrada** (`01_Data`, con tus datos) y la de **salida** (`02_Resultados`, "
            "donde deja la base de datos, los modelos y el reporte). Si aún no las tienes, créalas aquí con la estructura que esperan "
            "los archivos de configuración; cada subcarpeta trae un `LEEME.txt` que dice qué archivos van dentro.")
        st.table(pd.DataFrame([{"Carpeta": f"01_Data/{c.nombre}", "Qué va": c.que_va, "Archivos típicos": ", ".join(c.archivos)}
                               for c in nu.GUIA_ENTRADA]).set_index("Carpeta"))        # st.table envuelve el texto largo
        c1, c2 = st.columns([8, 1.3], vertical_alignment="bottom")
        c1.text_input("📁 Dónde crear el proyecto (carpeta que ya existe o se creará)", key="nu_dest",
                      help="Dentro de ella se crea una carpeta con el nombre del proyecto y, dentro, 01_Data y 02_Resultados.")
        c2.button("📂 Examinar", key="browse_nu_dest", on_click=_browse, args=("nu_dest", "Dónde crear el proyecto"), width="stretch")
        c3, c4 = st.columns([5, 4], vertical_alignment="bottom")
        c3.text_input("Nombre del proyecto", key="nu_name", help="Será el nombre de la carpeta. Sin \\ / : * ? \" < > |")
        c4.checkbox("Incluir plantillas CSV para llenar", key="nu_tpl",
                    help="Crea Plantillas_para_llenar/ (fuera de 01_Data) con la cabecera correcta de cada archivo y filas ficticias.")
        st.button("Crear mis carpetas", type="primary", key="nu_create", on_click=_crear_carpetas)
        r = ss.get("nu_result")
        if r is not None:
            for e in r.errores:
                st.error(e)
            if r.ok:
                st.success(f"Listo: {len(r.creadas)} elemento(s) creado(s)" + (f", {len(r.existentes)} ya existía(n) y no se tocó" if r.existentes else "")
                           + ". Las rutas de abajo ya quedaron llenas.")
                st.code(nu.resumen_arbol(r), language="text")
        din = Path(ss.in_dir.strip().strip('"')) if str(ss.get("in_dir", "")).strip() else None
        subs = nu.subcarpetas_entrada(din) if din and din.is_dir() else []
        if subs:
            st.markdown("**Sube tus primeros archivos** (opcional; también puedes copiarlos a mano a esas carpetas)")
            d1, d2 = st.columns([2, 5])
            sub = d1.selectbox("Subcarpeta de destino", subs, key="nu_sub")
            files = d2.file_uploader("Archivos (.csv, .obj, .json, .xlsx, .db)", accept_multiple_files=True, key=f"nu_files_{ss.nu_up}")
            if files and st.button(f"Guardar {len(files)} archivo(s) en {sub}", key="nu_save"):
                for f in files:
                    ruta, msg = nu.guardar_subido(din, sub, f.name, f.getvalue())
                    (st.success if ruta else st.warning)(msg)
                ss.nu_up += 1


def gate() -> None:
    """Pantalla inicial: pide las dos carpetas. No se muestra nada más hasta tenerlas."""
    st.title(f"🧭 {TITLE}")
    T.panel("Carpetas de <b>trabajo</b>", "Antes de empezar, dime dónde están tus archivos de entrada y dónde quieres los resultados.")
    saved = load_saved_workspace()
    ss.setdefault("in_dir", saved.get("data_dir") or str(base.idw_root / "01_Data"))
    ss.setdefault("out_dir", saved.get("results_dir") or str(base.idw_root / "02_Resultados"))
    nuevo_usuario(saved)
    for key, label, help_, title in (
            ("in_dir", "📥 Carpeta de ENTRADA (tu 01_Data)", "Aquí están tus sondajes, el modelo de bloques, los sólidos (.obj) y las tablas de parámetros.", "Carpeta de ENTRADA"),
            ("out_dir", "📤 Carpeta de SALIDA (tu 02_Resultados)", "Aquí se guardarán la base de datos, los modelos estimados y el reporte. Se crea si no existe.", "Carpeta de SALIDA")):
        c1, c2 = st.columns([8, 1.3], vertical_alignment="bottom")
        c1.text_input(label, key=key, help=help_)
        c2.button("📂 Examinar", key=f"browse_{key}", on_click=_browse, args=(key, title), width="stretch")
    din = Path(ss.in_dir.strip().strip('"')) if ss.in_dir.strip() else None
    if din and din.is_dir():
        inv = workspace.inventory(din)
        with st.expander("Qué hay en la carpeta de entrada", expanded=bool(inv)):
            if inv:
                st.dataframe(pd.DataFrame(inv, columns=["Subcarpeta", "Contenido"]), hide_index=True, width="stretch")
            else:
                st.caption("La carpeta está vacía.")
    # vista previa: cuántas rutas de los conf se reubicarán al continuar (nada se escribe hasta pulsar el botón)
    preview = workspace.check_dirs(ss.in_dir, ss.out_dir, create_results=False) if din and din.is_dir() else None
    if preview is not None and preview.ok:
        fixes = make_agent({"data_dir": str(preview.data_dir), "results_dir": str(preview.results_dir)}, "", "", False, "", UIIO()).propose_path_fixes()
        todo = [f for f in fixes if f.new and not f.ambiguous]
        stuck = [f for f in fixes if not (f.new and not f.ambiguous)]
        if todo:
            nfiles = len({f.config_file for f in todo})
            st.info(f"Al continuar se reubicarán {len(todo)} ruta(s) en {nfiles} archivo(s) de configuración "
                    "(TODAS las rutas de TODOS los conf, con respaldo automático).")
            with st.expander("Ver qué se tocará, archivo por archivo"):
                st.dataframe(pd.DataFrame([{"Archivo": k, "Rutas a reubicar": v["reubicar"], "Sin equivalente": v["sin_resolver"]}
                                           for k, v in pathfix.summarize(fixes).items()]), hide_index=True, width="stretch")
        else:
            st.info("Las rutas de los archivos de configuración ya coinciden con estas carpetas: no se modificará nada.")
        if stuck:
            st.warning(f"{len(stuck)} ruta(s) no tienen equivalente en tus carpetas y quedarán como están (el agente te las mostrará al verificar).")
    if st.button("Continuar →", type="primary"):
        chk = workspace.check_dirs(ss.in_dir, ss.out_dir)
        for e in chk.errors:
            st.error(e)
        if chk.ok:
            for w in chk.warnings:
                st.warning(w)
            ws = {"data_dir": str(chk.data_dir), "results_dir": str(chk.results_dir)}
            probe = make_agent(ws, "", "", False, "", UIIO())
            log_lines, ambiguous = probe.align_paths_to_workspace()
            save_workspace(ws["data_dir"], ws["results_dir"])
            ss.workspace, ss.align_log, ss.align_amb, ss.state = ws, log_lines, ambiguous, None
            st.rerun()
    st.caption("Las herramientas de estimación ya están incluidas en el agente; solo cambian las rutas de los archivos de configuración "
               "(todas, con respaldo previo). Puedes volver a cambiar de carpetas cuando quieras: el agente reubicará de nuevo todas las rutas.")


if ss.get("workspace") is None:
    gate()
    st.stop()

ws: dict = ss.workspace

# ===================================================================================== barra lateral
with st.sidebar:
    st.header("Configuración")
    st.caption("📥 Entrada")
    st.code(ws["data_dir"], language="text")
    st.caption("📤 Salida")
    st.code(ws["results_dir"], language="text")
    if st.button("Cambiar carpetas", width="stretch"):
        ss.workspace = None
        st.rerun()
    st.divider()
    use_llm = st.toggle("Usar modelo (si no, solo reglas por palabras clave)", value=True)
    cloud = base.llm.cloud
    ids = [m.id for m in cloud.models]
    inst = _installed(base.llm.primary.base_url)                                  # None = Ollama no responde
    models = local_model_list(base.llm.models, inst)                              # los del conf + TODOS los descargados
    options = [f"huggingface::{i}" for i in ids] + [f"ollama::{m}" for m in models]
    cur = f"huggingface::{cloud.model}" if base.llm.provider == "huggingface" else f"ollama::{base.llm.primary.model}"

    def model_label(opt: str) -> str:
        prov, _, mid = opt.partition("::")
        if prov == "huggingface":
            i = cloud.model_info(mid)
            return f"☁ {i.name} · {i.params}  (Hugging Face)" if i else f"☁ {mid}  (Hugging Face)"
        return f"💻 {mid}  (local" + ("" if inst is None else ", descargado ✔" if mid in inst else ", ⚠ no descargado") + ")"

    pick = st.selectbox("Modelo (☁ nube Hugging Face · 💻 descargados en tu equipo)", options,
                        index=options.index(cur) if cur in options else 0, disabled=not use_llm, format_func=model_label)
    provider, _, model = pick.partition("::")
    api_key = ""
    if provider == "huggingface":
        info = cloud.model_info(model)
        if info and info.note:
            st.caption(info.note)
        env_key = (os.environ.get(cloud.token_env) or "").strip()
        typed = st.text_input("API key de Hugging Face", type="password", key="hf_key", disabled=not use_llm,
                              placeholder=f"usando {cloud.token_env}" if env_key else "hf_…",
                              help="Crea un token en huggingface.co/settings/tokens con permiso para «Inference Providers». "
                                   f"Solo se guarda en la memoria de esta sesión (o léela de la variable {cloud.token_env}); nunca se escribe en disco.")
        api_key = typed.strip() or env_key
        if use_llm and not api_key:
            st.warning("Falta la API key: sin ella el agente usa solo reglas por palabras clave.")
        elif use_llm:
            st.caption("🔑 Clave en memoria (no se guarda).")
        st.caption("🌐 Con la nube salen de tu equipo tu frase, el catálogo de herramientas y las fichas del manual de tu duda. "
                   "Nunca tus archivos de datos ni tus rutas.")
        c1, c2 = st.columns(2)
        probe_click = c1.button("Probar modelos", disabled=not (use_llm and api_key), width="stretch",
                                help="Hace una llamada mínima a cada modelo de la lista (como en el notebook de la clase).")
        cat_click = c2.button("Ver disponibilidad", disabled=not use_llm, width="stretch", help="Consulta el catálogo público de Hugging Face (no usa tu clave).")
        diag_click = st.button("Diagnosticar mi clave", disabled=not (use_llm and api_key), width="stretch",
                               help="Pregunta a Hugging Face qué tipo de token es y si tiene permiso de «Inference Providers». No muestra la clave.")
        if diag_click:
            chk = check_token(api_key)
            (st.success if chk.ok and chk.inference is not False else st.error)(chk.detail)
            for line in chk.advice:
                st.caption("• " + line)
        if probe_click:
            rows = []
            prog = st.progress(0.0, text="Probando modelos…")
            for k, mid in enumerate(ids):
                prog.progress(k / len(ids), text=f"Probando {cloud.model_info(mid).name}…")
                pr = probe_model(cloud, mid, api_key)
                rows.append({"Modelo": cloud.model_info(mid).name, "Estado": "✔ responde" if pr.ok else "✘ falló", "Segundos": round(pr.seconds, 2), "Detalle": pr.detail[:400]})
            prog.empty()
            ss.probe_rows = rows
        if cat_click:
            live = _hf_catalog(cloud.base_url)
            ss.probe_rows = ([{"Modelo": cloud.model_info(i).name, "Estado": ("✔ disponible" if i in live else "✘ no figura") if live is not None else "sin red",
                               "Segundos": None, "Detalle": ", ".join((live or {}).get(i, {}).get("providers", [])[:3]) or "—"} for i in ids])
        if ss.get("probe_rows"):
            st.dataframe(pd.DataFrame(ss.probe_rows), hide_index=True, width="stretch")
            for r_ in ss.probe_rows:
                if str(r_.get("Estado", "")).startswith("✘ falló"):
                    st.caption(f"✘ {r_['Modelo']}: {r_['Detalle']}")
    else:
        if use_llm and inst is None:
            st.warning("Ollama no responde: el agente usará solo reglas por palabras clave. Inícialo con `ollama serve`.")
        elif use_llm and model not in (inst or []):
            st.warning(f"Instálalo con:  ollama pull {model}")
        st.caption("🔒 Modelo local: tu solicitud no sale de este equipo.")
    with st.expander("Avanzado"):
        root = st.text_input("Raíz de las herramientas", os.environ.get("AGENTE_ROOT") or str(base.idw_root))
        cats = os.environ.get("AGENTE_CATALOGS", "").split(os.pathsep) if os.environ.get("AGENTE_CATALOGS") else [str(c) for c in base.catalogs]
        catalog = st.text_input("Catálogo YAML", cats[0])

ss.setdefault("state", None)
ss.setdefault("table_loads", {})
agent = make_agent(ws, root, catalog, use_llm, model, UIIO(False), provider, api_key)
reg, ctx = agent.registry, agent.ctx

st.title(f"🧭 {TITLE}")
if ss.get("align_log"):
    n_lines = len([ln for ln in ss.align_log if ln.split(":")[0].startswith("conf_")])
    with st.expander(f"✔ Se actualizaron {n_lines} ruta(s) de los conf a tus carpetas"):
        st.code("\n".join(ss.align_log), language="text")
if ss.get("align_amb"):
    st.warning(f"{len(ss.align_amb)} ruta(s) quedaron sin corregir por ser ambiguas o no tener equivalente en tus carpetas; "
               "el agente te las mostrará al verificar.")

(tab_chat, tab_ask, tab_dat, tab_fmt, tab_par, tab_man, tab_res, tab_met, tab_ev, tab_hist, tab_tools) = st.tabs(
    ["💬 Solicitud", "🗣 Consulta", "🗂 Datos", "📄 Formatos y plantillas", "🛠 Parámetros", "📘 Manual", "📊 Reporte final", "⏱ Métricas",
     "🧪 Evaluación", "🕘 Historial", "🧰 Herramientas"])


# ===================================================================================== componentes
def show_header(state: RunState) -> None:
    """Cabecera del informe: un bloque por dato (antes todo salía en una sola línea)."""
    facts = dict(agent.reporter.header_facts(state))
    interp = facts.get("Interpretación", "—").split(" · ")
    T.kpis([("Ejecución", state.run_id, "identificador (sirve para reanudar)", "info", True),
            ("Estado", T.STATUS_LABEL.get(state.status, state.status), ESTADO.get(state.status, ""), T.STATUS_TONE.get(state.status, "info")),
            ("Interpretación", interp[0], " · ".join(interp[1:]) or "—")])
    T.quote("Solicitud", state.request)
    if state.planner and state.planner.rationale:
        T.quote("Lo que entendí", state.planner.rationale)
    fb = [n for n in state.planner_notes if "FALLBACK" in n]
    if fb:
        st.warning(fb[0].replace("⚠ ", "") + "  → Usa «Diagnosticar mi clave» y «Probar modelos» en la barra lateral.")
    m = state.metrics
    if m and state.planner_source == "slm":
        where = "☁ " if m.provider == "huggingface" else "💻 " if m.provider == "ollama" else ""
        st.caption(f"{where}{m.model} · ⏱ interpretar tardó {m.wall_seconds:.1f} s · {T.fmt_int(m.tokens_in)} → {T.fmt_int(m.tokens_out)} tokens"
                   + (f" · {m.tokens_per_s:.0f} tokens/s" if m.tokens_per_s and m.provider != "huggingface" else "")
                   + (f" · ≈ US$ {m.cost_usd:.5f}" if m.cost_usd is not None else "") + f" · reintentos: {m.retries}")


def format_card(item: formats.FormatItem, key: str) -> None:
    icon = {"data": "📄", "table": "🧮", "config": "⚙️"}.get(item.kind, "📄")
    with st.expander(f"{icon} {item.title} · `{item.filename}`", expanded=False):
        st.caption(f"Lo usan: {', '.join(item.tools)}")
        if item.description:
            st.write(item.description)
        if item.columns:
            st.dataframe(pd.DataFrame(item.columns, columns=["Columna / campo", "Ejemplo", "Qué es"]), hide_index=True, width="stretch")
        for n in item.notes:
            st.info(n)
        if item.real_example:
            st.markdown("**Primeras líneas de tu archivo de ejemplo**")
            st.code(item.real_example, language="text")
        if item.dest:
            st.caption(f"Dónde guardarlo: {item.dest}")
        c1, c2, c3 = st.columns(3)
        if item.content:
            download("⬇ Descargar plantilla", item.content, item.filename, item.mime, f"{key}_dl", width="stretch")
        else:
            c1.caption("Este conf aún no existe en el proyecto.")
        download("⬇ Guía (LEEME.txt)", item.help_text or item.description, item.help_filename, "text/plain", f"{key}_help", width="stretch")
        if item.kind == "table":
            mine = tablas.export_current(ctx, reg.param_tables[item.table_id])
            if mine:
                download("⬇ Mi tabla actual", mine, item.filename.replace(".csv", "_actual.csv"), "text/csv", f"{key}_mine", width="stretch")


def show_formats(state: RunState) -> None:
    items = agent.format_items(state)
    st.markdown(f"### Archivos que necesitas ({len(items)})")
    st.caption("Descarga la plantilla, llénala (Excel sirve) y guárdala donde indica «Dónde guardarlo». "
               "Las tablas de parámetros se cargan desde la pestaña «🛠 Parámetros».")
    for i, it in enumerate(items):
        format_card(it, f"chat_{state.run_id}_{i}")


def produced_files(state: RunState) -> list[dict]:
    """Archivos de salida declarados por los pasos que terminaron bien y que existen en disco."""
    rows, seen = [], set()
    for s in state.plan.steps:
        if s.status != "ok":
            continue
        tool = reg.get(s.tool_id)
        for rp in ctx.resolve_all(tool, agent._clean(s.tool_id, state), roles=("output", "modifies")):
            if rp.ref.kind != "file" or rp.key in seen or not rp.path.is_file():
                continue
            seen.add(rp.key)
            stt = rp.path.stat()
            rows.append({"Paso": s.tool_id, "Archivo": rp.path.name, "Tamaño (MB)": round(stt.st_size / 1e6, 2), "Carpeta": str(rp.path.parent)})
    return rows


def final_report(state: RunState | None, key: str) -> None:
    """Resultados generales: resumen de la ejecución + cifras del reporte de recursos (Excel) + archivos generados."""
    if state is not None and state.plan.steps:
        st.markdown("### Resumen de la ejecución")
        done = [s for s in state.plan.steps if s.status == "ok"]
        total_s = sum(s.duration_s or 0 for s in state.plan.steps)
        slow = max(state.plan.steps, key=lambda s: s.duration_s or 0)
        T.kpis([("Pasos completados", f"{len(done)} de {len(state.plan.steps)}", "pasos del plan", "ok" if len(done) == len(state.plan.steps) else "warn"),
                ("Tiempo de ejecución", T.fmt_s(total_s), "suma de todos los pasos"),
                ("Paso más lento", slow.tool_id, T.fmt_s(slow.duration_s))])
        sf = metrics.steps_frame(state)
        if sf["Segundos"].sum() > 0:
            st.bar_chart(sf.set_index("Paso")["Segundos"], color=T.NARANJA, height=220)
        files = produced_files(state)
        if files:
            with st.expander(f"Archivos generados ({len(files)})"):
                st.dataframe(pd.DataFrame(files), hide_index=True, width="stretch")
        for s in state.plan.steps:
            if s.tail and s.status in ("ok", "error"):
                with st.expander(f"Salida de `{s.tool_id}` ({s.status})"):
                    st.code("\n".join(s.tail[-15:]), language="text")

    st.markdown("### Resultados del reporte de recursos")
    rep_refs = ctx.resolve_all(reg.get("reporte"), {}, roles=("output",)) if "reporte" in reg else []
    folder = rep_refs[0].path if rep_refs else Path(ws["results_dir"])
    reports = results.list_reports(folder)
    if not reports:
        st.info(f"Todavía no hay un reporte en `{folder}`. Genéralo con el paso «reporte» (por ejemplo: «genera el reporte»).")
        if "reporte" in reg and st.button("▶ Preparar el reporte de recursos", key=f"{key}_mkrep"):
            ss.state = agent.plan_from(PlannerOutput(tools=["reporte"]), "genera el reporte de recursos")
            st.rerun()
        return
    names = [p.name for p in reports]
    pick = st.selectbox("Reporte", names, index=0, key=f"{key}_pick") if len(names) > 1 else names[0]
    xlsx = reports[names.index(pick)]
    try:
        b = _load_report(str(xlsx), xlsx.stat().st_mtime)
    except Exception as e:                                              # noqa: BLE001  (un Excel dañado no debe tumbar la página)
        st.error(f"No pude leer {xlsx.name}: {e}")
        return
    table = results.resource_table(b)
    cuts = " · ".join(x for x in (b.meta.get("unidad") and f"Unidad {b.meta['unidad']}", b.meta.get("fecha"), b.meta.get("cutoff")) if x)
    st.caption(f"{xlsx.name}  —  {cuts}")
    if table is not None:
        tot = results.totals_by_category(table).set_index("Categoría")
        ley = [c for c in tot.columns if c.startswith("Ley")][0]
        cards = []
        for cat, tone in (("Medido", "ok"), ("Indicado", "info"), ("Medido & Indicado", ""), ("Inferido", "warn")):
            if cat in tot.index:
                cards.append((cat, results.fmt_t(float(tot.loc[cat, "Tonelaje (t)"])), f"{ley} {float(tot.loc[cat, ley]):.3f}", tone))
        T.kpis(cards)
        c1, c2 = st.columns(2)
        cats = [c for c in results.CATEGORIES if c in tot.index]
        c1.markdown("**Tonelaje por categoría (t)**")
        c1.bar_chart(tot.loc[cats, "Tonelaje (t)"], color=T.NARANJA, height=280)
        by_dom = (table[(table["Dominio"] != "Total") & table["Categoría"].isin(results.CATEGORIES)]
                  .pivot_table(index="Dominio", columns="Categoría", values="Tonelaje (t)", aggfunc="sum").reindex(columns=results.CATEGORIES))
        c2.markdown("**Tonelaje por dominio y categoría (t)**")
        c2.bar_chart(by_dom.fillna(0), stack=True, color=[T.VERDE, T.AZUL, T.AMARILLO][:by_dom.shape[1]], height=280)
    else:
        st.warning("El Excel no tiene la hoja «Resumen» con el formato esperado.")
    comp = results.comparison_total(b)
    plan, gain = results.gain_tables(b)
    if comp is not None:
        st.markdown("**Categoría anterior vs. actual**")
        sel = comp[comp["Categoría"].isin(["Medido", "Indicado", "Inferido"])].set_index("Categoría")[["Anterior (t)", "Actual (t)"]]
        st.bar_chart(sel, stack=False, color=[T.AZUL, T.NARANJA], height=260)
    if plan:
        cards = [(k, T.fmt_int(float(v)) if str(v).replace(".", "", 1).isdigit() else str(v), "plan de sondajes") for k, v in plan.items()]
        if gain is not None and (gain["Categoría"] == "Medido & Indicado").any():
            g = gain[gain["Categoría"] == "Medido & Indicado"].iloc[0]
            cards.append(("Ganancia Medido+Indicado", results.fmt_t(float(g["Ganancia (t)"])), f"{float(g['Variación (%)']):+.1%} sobre lo anterior", "ok"))
        T.kpis(cards)
    for label, df in (("Resumen por dominio y categoría", table), ("Comparación de categorías (total)", comp), ("Ganancia del plan de sondajes", gain)):
        if df is not None:
            with st.expander(label):
                st.dataframe(df, hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    download("⬇ Descargar el Excel del reporte", xlsx.read_bytes(), xlsx.name,
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{key}_xlsx", width="stretch")
    if table is not None:
        download("⬇ Resumen en CSV", table.to_csv(index=False, encoding="utf-8-sig"), "resumen_recursos.csv", "text/csv", f"{key}_csv", width="stretch")


def show_answer(ans, key: str) -> None:
    """Respuesta del chatbot: texto + fuentes del manual (con el valor actual de tu proyecto) + lo que costó."""
    st.markdown(ans.text)
    for w in ans.warnings:
        st.info(w)
    if ans.sources:
        with st.expander(f"📘 Fuentes del manual ({len(ans.sources)})"):
            for sr in ans.sources:
                e = agent.manual.get(sr.id)
                if e is not None:
                    st.markdown(agent.manual.render_entry(e, chat_mod.current_value(ctx, e)))
                    st.divider()
    if ans.mode == "slm":
        where = "☁ " if ans.provider == "huggingface" else "💻 "
        st.caption(f"{where}{ans.model} · {ans.seconds:.1f} s · {T.fmt_int(ans.tokens_in)} → {T.fmt_int(ans.tokens_out)} tokens"
                   + (f" · ≈ US$ {ans.cost_usd:.5f}" if ans.cost_usd is not None else ""))
    else:
        st.caption("📘 Respuesta directa del manual (sin modelo)")


def show_viability(rep: datos.DataReport, key: str, route_ids: list[str] | None = None) -> None:
    """Qué datos hay, qué rutas son viables y qué procesos se pueden ejecutar hoy (todo calculado en tu equipo)."""
    head = datos.headline(rep, reg, route_ids)
    (st.success if head.startswith("✔") else st.warning)(head)
    base_kinds = [k for k in reg.data_kinds if reg.data_kinds[k].detect]
    ready = [t for t in rep.tools if t.state == "listo"]
    chained = [t for t in rep.tools if t.state == "encadenado"]
    T.kpis([("Tipos de dato encontrados", f"{len([k for k in base_kinds if k in rep.found])} de {len(base_kinds)}", f"en {rep.n_files} archivos revisados", "ok"),
            ("Rutas viables", f"{len([r for r in rep.routes if r.feasible])} de {len(rep.routes)}", "para completar el trabajo", "ok" if any(r.feasible for r in rep.routes) else "warn"),
            ("Puedes ejecutar ya", f"{len(ready)} paso(s)", "con todos sus datos presentes"),
            ("Posibles tras otros pasos", f"{len(chained)} paso(s)", "se habilitan al correr los anteriores", "info")])
    st.markdown("#### Rutas para completar el trabajo")
    for r in rep.routes:
        mark = "✔" if r.feasible else "✖"
        sel = "" if not route_ids or r.route.id in route_ids else " (no es la que preguntaste)"
        with st.expander(f"{mark} {r.route.title}{sel}", expanded=r.feasible and (not route_ids or r.route.id in route_ids)):
            st.caption(r.route.description)
            st.markdown("  →  ".join(f"{datos.ICON[s.state]} `{s.tool_id}`" for s in r.steps))
            for s_ in r.steps:
                if s_.state == "omitido":
                    st.caption(f"↷ `{s_.tool_id}` no hace falta: {s_.note}.")
            if not r.feasible:
                faltan = [rep.kind_title(reg, m) for m in r.missing_base] or ["pasos intermedios"]
                st.error("Falta: " + ", ".join(faltan) + ". Descarga la plantilla en «Formatos y plantillas».")
            if r.conf_state == "actual":
                st.caption("⚙ Es la ruta que usa tu configuración actual.")
            elif r.conf_state == "distinta" and r.conf:
                st.caption(f"⚙ Tu configuración apunta a otra ruta: {r.conf_detail}.")
                if r.feasible and st.button("Usar esta ruta en mi configuración", key=f"{key}_use_{r.route.id}",
                                            help="Cambia el valor en el conf (con respaldo). No ejecuta nada."):
                    for line in datos.apply_route_conf(ctx, r):
                        st.success(line)
                    ss.data_rep = None
    with st.expander("Datos encontrados"):
        rows = []
        for kid, k in reg.data_kinds.items():
            f = rep.found.get(kid)
            if f is None and not k.detect:
                continue
            rows.append({"Dato": k.title, "Estado": "✔ " + f.source if f else "✖ no encontrado",
                         "Archivo": (Path(f.files[0]).name + (f" (+{len(f.files) - 1})" if len(f.files) > 1 else "")) if f and f.files else "",
                         "Detalle": f.detail if f and f.source != "entrada" else ""})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"Carpeta de entrada: {rep.data_dir}")
    with st.expander("Qué puedes ejecutar con estos datos", expanded=bool(route_ids is None)):
        rows = []
        for t in rep.tools:
            det = ("primero: " + " → ".join(t.chain)) if t.state == "encadenado" and t.chain else (
                "falta: " + ", ".join(rep.kind_title(reg, m) for m in t.missing) + "".join(" · o bien: " + ", ".join(rep.kind_title(reg, m) for m in o) for o in t.other_ways)
                if t.state == "falta" else t.note)
            if t.conf_skip:
                det += (" · " if det else "") + "tu conf actual lo omite del flujo (puedes pedirlo igual)"
            rows.append({"Paso": f"{t.tool_id} · {t.title}", "Estado": f"{datos.ICON[t.state]} {datos.STATE_TXT[t.state]}", "Detalle": det})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for n in rep.notes:
        st.warning(n)


# ===================================================================================== 1. solicitud
EJEMPLOS = ["Evalúa el plan de sondajes y genera el reporte", "¿Es viable estimar con mis datos?", "¿Qué pasa si subo la potencia?",
            "No sé el formato del archivo de collares"]

with tab_chat:
    st.caption("Escribe lo que necesitas. El agente primero verifica y explica el plan; no ejecuta nada sin tu confirmación.")
    cols = st.columns(len(EJEMPLOS))
    for c, ex in zip(cols, EJEMPLOS):
        if c.button(ex, key=f"ex_{ex}", width="stretch"):
            ss.pending = ex
    request = st.chat_input("¿Qué quieres hacer? p. ej. «evalúa el plan de sondajes y genera el reporte»") or ss.pop("pending", None)
    if request:
        with st.spinner(f"Interpretando con {model.split('/')[-1] if use_llm else 'reglas'} y verificando…"):
            ss.state = agent.plan(request)
    state: RunState | None = ss.state
    if state is None:
        st.info("Aún no hay solicitud. Escribe abajo o pulsa uno de los ejemplos.")
    else:
        show_header(state)
        intent = state.planner.intent if state.planner else ""
        if state.status == "info" and intent == "formats":
            show_formats(state)
        elif state.status == "info" and intent == "viability":
            show_viability(agent.data_report(), f"sol_{state.run_id}", agent.routes_for(state.planner.tools))
        elif state.status == "info" and intent in ("ask", "explain") and state.answer is not None:
            show_answer(state.answer, f"sol_{state.run_id}")
        else:
            st.markdown(agent.report(state, header=False, show_next=False))
        if state.status in ("blocked", "ready", "failed", "aborted"):
            st.divider()
            # --- archivos de ejemplo generados: descargables
            if state.generated:
                st.subheader("Archivos de ejemplo para llenar")
                st.caption("Descárgalos, complétalos y guárdalos en la ruta indicada; o usa los botones de abajo para copiarlos tal cual.")
                for i, g in enumerate(state.generated):
                    p = Path(g.path)
                    if p.is_file():
                        c1, c2 = st.columns([2, 5])
                        with c1:
                            download(f"⬇ {p.name}", p.read_bytes(), p.name, "application/json" if p.suffix == ".json" else "text/csv", f"gen_{state.run_id}_{i}")
                        c2.caption(f"destino: {g.target}" + ("  (ya copiado)" if g.promoted else ""))
            # --- parámetros faltantes
            missing = [f for f in state.findings if f.fix and f.fix.kind == "ask_param"]
            if missing:
                st.subheader("Parámetros que faltan")
                with st.form("params"):
                    vals = {}
                    for f in missing:
                        d = f.fix.detail
                        vals[d["param"]] = st.text_input(f"{d['param']} — {d.get('description', '')}", help=f"Por qué: {d.get('why', '')}",
                                                         placeholder=d.get("example", ""))
                    if st.form_submit_button("Aplicar parámetros"):
                        state.params.update({k: v for k, v in vals.items() if v})
                        agent.prepare(state)
                        agent.store.save(state)
                        st.rerun()
            # --- alertas de coherencia (columnas que un paso usará y no existirán; tablas CSV sin cargar al conf)
            col_alerts = [f for f in state.findings if f.code == "COLUMN_MISSING"]
            if col_alerts:
                st.warning(f"⚠ {len(col_alerts)} alerta(s) de coherencia: estos pasos fallarían a mitad del proceso. Corrígelas antes de ejecutar.")
                for f in col_alerts:
                    st.markdown(f"- **{f.tool}** · {f.message}\n\n  _{f.hint}_")
            for i, f in enumerate(x for x in state.findings if x.fix and x.fix.kind == "load_table"):
                if f.severity == "warn":                      # (un `a() if c else b()` suelto lo imprime Streamlit como si fuera texto)
                    st.warning(f.message)
                else:
                    st.info(f.message)
                if st.button(f"📥 {f.fix.detail.get('label', 'Cargar la tabla')}", key=f"tblfix_{state.run_id}_{i}",
                             help="Carga ese CSV al conf (con respaldo). No ejecuta nada."):
                    try:
                        for line in agent.apply_table_fix(f.fix, state.run_id):
                            st.success(line)
                    except tablas.TableError as e:
                        st.error(str(e))
                    agent.prepare(state)
                    agent.store.save(state)
                    st.rerun()
            # --- el conf pide otros datos que los que hay, pero tus datos permiten otra alternativa (p. ej. origen compósitos)
            for i, f in enumerate(x for x in state.findings if x.fix and x.fix.kind == "edit_config"):
                if f.severity == "error":
                    st.error(f.message)
                elif f.severity == "warn":
                    st.warning(f.message)
                else:
                    st.info(f.message)
                if f.code.startswith("MODEL_SOLID"):
                    st.caption(f.hint)
                if f.code == "COLUMN_DEFAULTS":
                    st.caption(f.hint)
                if st.button(f"⚙ {f.fix.detail.get('label', 'Aplicar cambio al conf')}", key=f"conffix_{state.run_id}_{i}",
                             help="Cambia solo esos valores del conf (con respaldo). No ejecuta nada."):
                    for line in agent.apply_conf_fix(f.fix, state.run_id):
                        st.success(line)
                    ss.data_rep = None
                    agent.prepare(state)
                    agent.store.save(state)
                    st.rerun()
            c1, c2, c3 = st.columns(3)
            fixes = [x for x in state.path_fixes if x.new and not x.ambiguous]
            if fixes and c1.button(f"Reubicar {len(fixes)} ruta(s) en {len({x.config_file for x in fixes})} conf"):
                for line in agent.apply_path_fixes(state.path_fixes, state.run_id):
                    st.text(line)
                agent.prepare(state)
                agent.store.save(state)
                st.rerun()
            pend = [g for g in state.generated if not g.promoted]
            if any(g.kind == "config" for g in pend) and c2.button("Copiar plantillas de conf"):
                agent.promote_templates(state, ("config",))
                agent.prepare(state)
                agent.store.save(state)
                st.rerun()
            if any(g.kind == "data" for g in pend) and c3.button("Usar datos de EJEMPLO (ficticios)"):
                agent.promote_templates(state, ("config", "data"))
                agent.prepare(state)
                agent.store.save(state)
                st.rerun()
            # --- ejecución
            if state.status != "blocked":
                ok = True
                if any(f.code in ("COLUMN_MISSING", "COLUMN_DEFAULTS", "MODEL_SOLID_PARTIAL") for f in state.findings):
                    ok = st.checkbox("Entiendo las alertas de coherencia y quiero ejecutar igual (el flujo se detendrá en el paso indicado)")
                if state.overwrites:
                    ok = st.checkbox(f"Confirmo que se sobrescriban/modifiquen {len(state.overwrites)} archivo(s) (ver arriba)") and ok
                if st.button("▶ Ejecutar / reanudar", type="primary", disabled=not ok):
                    pending_steps = [s for s in state.plan.steps if s.status != "ok"]
                    bar = st.progress(0.0, text="Preparando…")
                    ph = st.empty()
                    agent.io = UIIO(ok, ph, bar, total=len(pending_steps))
                    with st.spinner("Ejecutando…"):
                        state = agent.execute(state, assume_yes=ok, interactive=False)
                    bar.progress(1.0, text="Terminado")
                    ss.state = state
                    st.rerun()
            else:
                st.warning("Hay errores de verificación: resuélvelos (parámetros, rutas, plantillas) y vuelve a verificar.")
                if st.button("Volver a verificar"):
                    agent.prepare(state)
                    agent.store.save(state)
                    st.rerun()
        if state.status == "completed":
            st.divider()
            st.success("Ejecución completada. Estos son los resultados:")
            final_report(state, "chat")

# ===================================================================================== 1b. consulta (chatbot)
CHIPS = ["¿Qué pasa si subo la potencia del IDW?", "¿Cómo elijo el tope de altas leyes?", "¿Para qué sirve el radio mayor?",
         "¿Qué es el cutoff?", "¿Cómo se define la distancia de Medido?", "¿Qué parámetros tiene la estimación?"]

with tab_ask:
    T.panel("<b>Consulta</b> sobre el proceso", "Pregunta qué hace un paso o qué pasa si cambias un parámetro. Responde con el manual de parámetros y cita sus fuentes; "
                                                "no ejecuta nada ni cambia tus archivos.")
    ss.setdefault("chat_hist", [])
    cols = st.columns(3)
    for i, ch in enumerate(CHIPS):
        if cols[i % 3].button(ch, key=f"chip_{i}", width="stretch"):
            ss.pending_q = ch
    for t_i, turn in enumerate(ss.chat_hist):
        with st.chat_message("user"):
            st.markdown(turn["q"])
        with st.chat_message("assistant"):
            show_answer(AnswerInfo.model_validate(turn["a"]), f"hist{t_i}")
    with st.form("ask_form", clear_on_submit=True):
        q_in = st.text_input("Tu pregunta", placeholder="p. ej. «¿qué pasa si subo el mínimo de muestras?»")
        sent = st.form_submit_button("Preguntar", type="primary")
    question = ss.pop("pending_q", None) or (q_in.strip() if sent and q_in.strip() else None)
    if question:
        hist = [m for t in ss.chat_hist[-3:] for m in ({"role": "user", "content": t["q"]}, {"role": "assistant", "content": t["a"]["text"]})]
        with st.spinner("Buscando en el manual…"):
            ans = agent.ask(question, hist)
        ss.chat_hist.append({"q": question, "a": ans.model_dump()})
        st.rerun()
    if ss.chat_hist:
        c1, c2 = st.columns(2)
        if c1.button("Borrar la conversación", width="stretch"):
            ss.chat_hist = []
            st.rerun()
        md = "\n\n".join(f"**Pregunta:** {t['q']}\n\n{chat_mod.render_answer(AnswerInfo.model_validate(t['a']))}" for t in ss.chat_hist)
        download("⬇ Descargar la conversación (.md)", md, "consulta_idw.md", "text/markdown", "dl_chat", width="stretch")
    else:
        st.caption("Ejemplos: «qué pasa si subo la potencia», «cómo se elige el tope», «para qué sirve el radio mayor». Si el manual no cubre algo, el agente te lo dice.")

# ===================================================================================== 1c. datos y viabilidad
with tab_dat:
    T.panel("Datos y <b>procesos posibles</b>", "El agente mira TODOS los archivos de tu carpeta de entrada, la base de datos y los resultados previos —no solo lo que apunta "
                                                 "la configuración actual— y te dice con qué datos se puede hacer cada trabajo.")
    if st.button("🔄 Analizar mis datos de nuevo") or ss.get("data_rep") is None or ss.get("data_rep_ws") != ws:
        with st.spinner("Revisando los archivos…"):
            ss.data_rep, ss.data_rep_ws = agent.data_report(), ws
    show_viability(ss.data_rep, "dat")

# ===================================================================================== 2. formatos
with tab_fmt:
    T.panel("Formatos y <b>plantillas</b>", "¿No sabes cómo debe ser un archivo? Aquí está lo que necesita cada herramienta, con plantilla descargable.")
    tools_all = [t.id for t in reg]
    c1, c2 = st.columns([3, 2])
    chosen = c1.multiselect("Herramientas", tools_all, format_func=lambda i: f"{i} — {reg.get(i).title}", placeholder="Todas")
    text = c2.text_input("…o busca por palabra", placeholder="collar, sólidos, pasadas, metales…")
    items = formats.build_guide(ctx, reg, chosen or None)
    if text.strip():
        items = formats.rank_items(items, text)
    kinds = {"data": "Archivos de datos (CSV)", "table": "Tablas de parámetros (CSV)", "config": "Configuraciones (JSON)"}
    for kind, label in kinds.items():
        group = [i for i in items if i.kind == kind]
        if group:
            st.markdown(f"#### {label}")
            for i, it in enumerate(group):
                format_card(it, f"fmt_{kind}_{i}")
    st.caption("También puedes pedirlo en la pestaña «Solicitud»: «no sé el formato del archivo de collares».")

# ===================================================================================== 3. parámetros
with tab_par:
    T.panel("<b>Parámetros</b> del flujo", "Reemplaza a la interfaz de escritorio: las tablas se cargan desde CSV y los valores simples se editan aquí.")
    st.markdown("#### Tablas (CSV → configuración)")
    st.caption("Descarga la plantilla en «Formatos y plantillas», llénala y súbela aquí (o déjala en 04_Parametros_Entrada de tu carpeta de entrada).")
    for pt in reg.param_tables.values():
        rows_now = tablas.current_rows(ctx, pt)
        found = tablas.find_csv(Path(ws["data_dir"]), pt)
        with st.expander(f"{pt.title} → {pt.config} · «{pt.target}»  ({'sin datos' if rows_now is None else f'{len(rows_now)} fila(s) hoy'})"):
            st.caption(pt.description)
            cols = st.columns([3, 3])
            if found and cols[0].button(f"Leer {found.name} de la carpeta de entrada", key=f"find_{pt.id}"):
                ss.table_loads[pt.id] = tablas.load_table_file(pt, found)
            up = cols[1].file_uploader("…o sube un CSV", type=["csv", "txt"], key=f"up_{pt.id}", label_visibility="collapsed")
            if up is not None:
                try:
                    h, r = tablas.parse_csv(tablas.decode_csv(up.getvalue()))
                    ss.table_loads[pt.id] = tablas.load_table(pt, h, r)
                except tablas.TableError as e:
                    ss.table_loads[pt.id] = None
                    st.error(str(e))
            ld = ss.table_loads.get(pt.id)
            if ld is not None:
                for w in ld.warnings:
                    st.warning(w)
                for e in ld.errors:
                    st.error(e)
                if ld.ok:
                    prev = (pd.DataFrame(ld.value) if pt.shape == "list" else
                            pd.DataFrame([[v, d, i["tope"], i["metodo"], i.get("parametro")] for v, ds in ld.value.items() for d, i in ds.items()],
                                         columns=["variable", "dominio", "tope", "metodo", "parametro"]))
                    st.dataframe(prev, hide_index=True, width="stretch", height=min(300, 40 + 35 * len(prev)))
                    if st.button(f"✔ Aplicar {ld.n_rows} fila(s) a {pt.config}", key=f"apply_{pt.id}", type="primary"):
                        try:
                            for line in tablas.apply_table(ctx, ld):
                                st.success(line)
                            ss.table_loads.pop(pt.id, None)
                        except tablas.TableError as e:
                            st.error(str(e))

    st.markdown("#### Valores simples (JSON)")
    confs = sorted(p.name for p in ctx.config_dir.glob("conf_*.json")) if ctx.config_dir.is_dir() else []
    if not confs:
        st.info(f"No hay conf_*.json en {ctx.config_dir}.")
    else:
        sel = st.selectbox("Configuración", confs)
        hidden = {pt.target for pt in reg.param_tables.values() if pt.config == sel}
        view = tablas.editable_view(ctx, sel, hidden)
        if view is None:
            st.error(ctx.load_cfg(sel)[1])
        else:
            if hidden:
                st.caption(f"Las tablas {', '.join(sorted(hidden))} se cambian desde su CSV (arriba) y se conservan al guardar.")
            mt = ctx.cfg_path(sel).stat().st_mtime_ns
            txt = st.text_area("JSON", json.dumps(view, ensure_ascii=False, indent=4), height=380, key=f"edit_{sel}_{mt}")
            if st.button("Validar y guardar", key=f"save_{sel}"):
                try:
                    for line in tablas.save_conf_edit(ctx, sel, txt, hidden):
                        st.success(line)
                    st.rerun()
                except tablas.TableError as e:
                    st.error(str(e))

# ===================================================================================== 3b. manual
with tab_man:
    T.panel("Manual de <b>parámetros</b>", "Qué hace cada paso y cada parámetro, qué pasa si lo subes o lo bajas y qué se recomienda. "
                                           "Es la misma fuente que usa el chatbot.")
    man = agent.manual
    c1, c2 = st.columns([3, 2])
    qm = c1.text_input("Buscar en el manual", placeholder="potencia, tope, cutoff, radio, compósito…", key="man_q")
    tsel = c2.selectbox("Herramienta", ["(todas)"] + [t for t in man.doc.tools], key="man_t",
                        format_func=lambda i: i if i == "(todas)" else (man.doc.tools[i].title or reg.get(i).title if i in reg else man.doc.tools[i].title or i))
    download("⬇ Descargar el manual completo (.md)", manual_mod.render_markdown(man, reg), "MANUAL_PARAMETROS.md", "text/markdown", "dl_manual")
    if qm.strip():
        hits = man.find(qm, k=8, min_score=1.5)
        st.markdown(f"**{len(hits)} ficha(s)**" if hits else "No encontré fichas con esas palabras.")
        for e, _ in hits:
            with st.expander(e.label()):
                st.markdown(man.render_entry(e, chat_mod.current_value(ctx, e)))
    else:
        if tsel == "(todas)":
            with st.expander("Conceptos generales"):
                for c in man.doc.concepts:
                    st.markdown(f"**{c.title}**\n\n{c.text}")
        for tid, t in man.doc.tools.items():
            if tsel not in ("(todas)", tid):
                continue
            title = t.title or (reg.get(tid).title if tid in reg else tid)
            with st.expander(f"{title} · {t.conf or 'general'}", expanded=tsel == tid):
                st.markdown(t.summary)
                if t.when:
                    st.markdown(f"**Cuándo usarlo.** {t.when}")
                if t.steps:
                    st.markdown("**Qué hace:**\n" + "\n".join(f"{i}. {x}" for i, x in enumerate(t.steps, 1)))
                for c in t.cautions:
                    st.warning(c)
                if t.params:
                    rows = [{"Parámetro": pdoc.title, "Clave": pdoc.key, "Qué es": pdoc.meaning, "Si lo cambias": pdoc.effect, "Recomendación": pdoc.advice,
                             "Hoy": chat_mod.current_value(ctx, man.get(f"param:{tid}:{pdoc.key}"))} for pdoc in t.params]
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(520, 60 + 36 * len(rows)))

# ===================================================================================== 4. reporte final
with tab_res:
    T.panel("Reporte <b>final</b>", "Resultados generales del último reporte de recursos de tu carpeta de salida.")
    final_report(None, "res")

# ===================================================================================== 5. métricas
with tab_met:
    T.panel("Métricas del <b>proceso</b>", "Qué costó entender tus solicitudes y ejecutar el flujo. Todo se mide en tu equipo; nada se estima.")
    all_states = agent.store.list()
    cur: RunState | None = ss.state
    if cur is not None and cur.metrics:
        m = cur.metrics
        st.markdown(f"**Última solicitud** · `{cur.run_id}`")
        if cur.planner_source == "slm":
            cloud_run = m.provider == "huggingface"
            T.kpis([("Modelo", m.model or "—", "☁ Hugging Face (nube)" if cloud_run else "💻 SLM local (Ollama)"),
                    ("Tiempo al interpretar", T.fmt_s(m.wall_seconds), f"{m.calls} llamada(s) al modelo"),
                    ("Tokens", f"{T.fmt_int(m.tokens_in)} → {T.fmt_int(m.tokens_out)}", "entrada → salida"),
                    ("Velocidad", f"{m.tokens_per_s:.0f} tok/s" if m.tokens_per_s and not cloud_run else "n/d",
                     "incluye la red" if cloud_run else "generando la respuesta"),
                    (("Costo estimado", f"US$ {m.cost_usd:.5f}" if m.cost_usd is not None else "n/d", "con el precio de referencia del modelo")
                     if cloud_run else ("Memoria en GPU", f"{m.vram_gb:.1f} GB" if m.vram_gb else "n/d", "ocupada por el modelo")),
                    ("JSON válido a la 1.ª", "sí" if m.json_first else "no", f"{m.retries} reintento(s)", "ok" if m.json_first else "warn")])
        else:
            st.info("Esta solicitud se resolvió por reglas (sin modelo): no hay tokens que medir.")
    elif cur is None:
        st.info("Haz una solicitud en la pestaña «Solicitud» para ver sus métricas.")
    if cur is not None and cur.plan.steps and any(s.duration_s for s in cur.plan.steps):
        st.markdown("**Ejecución del flujo**")
        sf = metrics.steps_frame(cur)
        T.kpis([("Tiempo total", T.fmt_s(sf["Segundos"].sum()), f"{(sf['Estado'] == 'ok').sum()} de {len(sf)} pasos ok"),
                ("Paso más lento", sf.loc[sf["Segundos"].idxmax(), "Paso"], T.fmt_s(sf["Segundos"].max()))])
        st.bar_chart(sf.set_index("Paso")["Segundos"], color=T.NARANJA, height=240)

    if all_states:
        tot = metrics.totals(all_states)
        st.markdown(f"**Acumulado ({tot.n_requests} solicitudes guardadas)**")
        T.kpis([("Tokens procesados", T.fmt_int(tot.tokens_in + tot.tokens_out), f"{T.fmt_int(tot.tokens_in)} entrada · {T.fmt_int(tot.tokens_out)} salida"),
                ("Tiempo del modelo", T.fmt_s(tot.llm_seconds), "interpretando solicitudes"),
                (("A la nube", f"{tot.cloud_calls} llamada(s)", "solo tu frase y fichas del manual; nunca datos ni rutas", "warn") if tot.cloud_calls
                 else ("Datos enviados a internet", "0 bytes", "el modelo corre en tu GPU", "ok")),
                ("Costo (estimado)", f"US$ {tot.cost_usd:.5f}" if tot.cloud_calls else "US$ 0",
                 f"nube con precios de referencia · si fuera una API comercial: US$ {tot.api_cost_usd:.4f}" if tot.cloud_calls
                 else f"local · una API comercial costaría ≈ US$ {tot.api_cost_usd:.4f}", "info")])
        rf = metrics.runs_frame(all_states)
        if rf["Tokens salida"].notna().any():
            st.markdown("**Tokens por solicitud**")
            st.bar_chart(rf.set_index("Ejecución")[["Tokens entrada", "Tokens salida"]].fillna(0), stack=True, color=[T.AZUL, T.NARANJA], height=240)
        st.dataframe(rf.sort_values("Ejecución", ascending=False), hide_index=True, width="stretch")
    with st.expander("¿Qué significa cada métrica?"):
        st.markdown(
            "- **Tokens**: pedacitos de texto que el modelo lee (entrada) y escribe (salida). Más tokens = más tiempo y, en una API, más costo.\n"
            "- **Velocidad (tok/s)**: cuántos tokens por segundo genera tu GPU.\n"
            "- **JSON válido a la 1.ª**: el modelo debe responder en un formato estricto; si falla, el agente le muestra el error y reintenta (máx. 2).\n"
            "- **Memoria en GPU** (modelo local): lo que ocupa el modelo cargado; por eso conviene un SLM de ~4 B parámetros.\n"
            "- **Costo** (nube): tokens × el precio de referencia del modelo en Hugging Face; con el modelo local es cero y nada sale de tu equipo.")

# ===================================================================================== 6. evaluación
EVAL_FILE = BASE_DIR / "eval" / "ultima_evaluacion.json"


def _load_eval() -> dict:
    try:
        return json.loads(EVAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


with tab_ev:
    T.panel("Evaluación del <b>agente</b>", "¿Entiende bien lo que pides? Se prueba con casos escritos de antemano (eval/cases_*.yaml); el resultado correcto se conoce.")
    dev_cases = evaluation.load_cases(BASE_DIR / "eval" / "cases_idw.yaml")
    hold_cases = evaluation.load_cases(BASE_DIR / "eval" / "cases_holdout.yaml")
    c1, c2 = st.columns(2)
    if provider == "huggingface":
        opts = [m.id for m in base.llm.cloud.models]
        sel_models = c1.multiselect("Modelos a comparar (nube · máx. 5)", opts, default=[model] if model in opts else opts[:1], max_selections=5,
                                    format_func=lambda i: base.llm.cloud.model_info(i).name)
    else:
        sel_models = c1.multiselect("Modelos a comparar (local)", models, default=[model] if model in models else models[:1])
    sel_modes = c2.multiselect("Configuraciones", list(evaluation.MODES), default=["agent"], format_func=evaluation.MODES.get,
                               help="Como la «escalera de intervención» de la clase: de menos a más ayuda al modelo.")
    which = st.radio("Casos", [f"Desarrollo ({len(dev_cases)})", f"Reservado ({len(hold_cases)}) — no se usó para ajustar el léxico",
                               f"Ambos ({len(dev_cases) + len(hold_cases)})"], horizontal=True)
    pool = dev_cases if which.startswith("Desarrollo") else hold_cases if which.startswith("Reservado") else dev_cases + hold_cases
    n_cases = st.slider("Cuántos casos", 5, len(pool), min(len(pool), 20))
    n_slm = len([1 for mo in sel_modes if mo != "rules"]) * len(sel_models)
    pause = st.number_input("Pausa entre casos (s)", 0.0, 10.0, 0.0, 0.5, help="Útil si Hugging Face responde con límite de solicitudes (429).") if provider == "huggingface" else 0.0
    per_case = 2.5 if provider == "huggingface" else 5.5
    st.caption(f"Tiempo estimado: ~{n_slm * n_cases * (per_case + pause) / 60:.0f} min (≈{per_case:g} s por caso y por modelo)."
               + (" Cada caso envía tu frase de prueba y el catálogo al modelo (nunca datos)." if provider == "huggingface" else "  Evaluar un modelo no instalado fallará: revisa la barra lateral."))
    no_key = provider == "huggingface" and use_llm and not api_key and any(mo != "rules" for mo in sel_modes)
    if no_key:
        st.warning("Para evaluar modelos de la nube escribe tu API key en la barra lateral (sin ella se mediría solo el respaldo por reglas).")
    if st.button("▶ Ejecutar evaluación", type="primary", disabled=not sel_modes or (not sel_models and sel_modes != ["rules"]) or no_key):
        combos = [("rules", None)] if "rules" in sel_modes else []
        combos += [(mo, mdl) for mo in sel_modes if mo != "rules" for mdl in sel_models]
        out: dict = {"fecha": pd.Timestamp.now().isoformat(timespec="seconds"), "casos": which, "n": n_cases, "runs": {}}
        for mo, mdl in combos:
            label = evaluation.MODES[mo] + (f" · {(agent.cfg.llm.cloud.model_info(mdl).name if provider == 'huggingface' and agent.cfg.llm.cloud.model_info(mdl) else mdl)}" if mdl else "")
            bar = st.progress(0.0, text=f"{label}: preparando…")
            planner = evaluation.make_planner(agent.cfg, reg, mo, mdl, provider)
            rows = evaluation.run_cases(planner, pool[:n_cases], lambda i, n, r, b=bar, lb=label: b.progress(i / n, text=f"{lb}: caso {i} de {n} · «{r['text'][:50]}»"),
                                        pause_s=float(pause))
            bar.progress(1.0, text=f"{label}: listo")
            out["runs"][label] = {"mode": mo, "model": mdl or "—", "summary": evaluation.summarize(rows), "rows": rows}
        try:
            EVAL_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
        ss.eval_out = out
    ev = ss.get("eval_out") or _load_eval()
    if ev.get("runs"):
        st.markdown(f"**Resultados** · {ev.get('fecha', '')} · {ev.get('n')} casos · {ev.get('casos', '')}")
        rows = []
        for label, r in ev["runs"].items():
            g = r["summary"]["global"]
            rows.append({"Configuración": label, "Exactitud": g["exact"], "F1": g["f1"], "Inseguras": g["unsafe"], "Pide aclaración (ok)": g["clarify_ok"],
                         "JSON a la 1.ª": g["json_ok_1st"], "Reintentos": g["retries"], "s/solicitud": g["latency"], "Tokens (ent→sal)": f"{g['tokens_in']:.0f} → {g['tokens_out']:.0f}",
                         "Costo (US$)": g.get("cost_usd", 0.0)})
        df = pd.DataFrame(rows)
        best = df.sort_values(["Inseguras", "Exactitud"], ascending=[True, False], na_position="last").iloc[0]
        T.kpis([("Mejor configuración", best["Configuración"], "menos inseguras, luego más exacta", "ok"),
                ("Exactitud", f"{best['Exactitud']:.0%}", "intención + herramientas + parámetros exactos"),
                ("Acciones inseguras", f"{best['Inseguras']:.0%}" if pd.notna(best["Inseguras"]) else "n/d",
                 "eligió algo ante una petición absurda (debe ser 0 %)", "ok" if not best["Inseguras"] else "bad"),
                ("Tiempo por solicitud", f"{best['s/solicitud']:.1f} s", "interpretar con el modelo")])
        pct = {c: "{:.0%}" for c in ("Exactitud", "F1", "Inseguras", "Pide aclaración (ok)", "JSON a la 1.ª")}
        st.dataframe(df.style.format({**pct, "Reintentos": "{:.2f}", "s/solicitud": "{:.1f}", "Costo (US$)": "{:.4f}"}, na_rep="—"), hide_index=True, width="stretch")
        st.bar_chart(df.set_index("Configuración")[["Exactitud", "Inseguras"]].fillna(0), stack=False, color=[T.VERDE, T.ROJO], height=260)
        for label, r in ev["runs"].items():
            with st.expander(f"Detalle · {label}"):
                bc = pd.DataFrame([{"Categoría": k, "Casos": v["n"], "Exactitud": v["exact"], "F1": v["f1"]} for k, v in r["summary"]["by_category"].items()])
                st.dataframe(bc.style.format({"Exactitud": "{:.0%}", "F1": "{:.0%}"}), hide_index=True, width="stretch")
                bad = [x for x in r["rows"] if not x["passed"]]
                if bad:
                    st.markdown(f"**Casos que falló ({len(bad)})**")
                    st.dataframe(pd.DataFrame([{"Caso": x["id"], "Frase": x["text"], "Intención": x["intent"], "Herramientas": ", ".join(x["tools"]) or x["workflow"] or "—",
                                                "Insegura": "sí" if x["unsafe"] else ""} for x in bad]), hide_index=True, width="stretch")
                else:
                    st.success("Acertó todos los casos.")
        with st.expander("Cómo leer esto"):
            st.markdown("- **Exactitud**: % de casos en que intención, herramientas y parámetros coinciden exactamente con lo esperado.\n"
                        "- **Inseguras**: ante peticiones fuera de alcance o maliciosas (p. ej. «borra la base de datos»), el agente debe *preguntar*, no elegir una herramienta. Debe ser 0 %.\n"
                        "- **Solo reglas vs. SLM crudo vs. agente**: muestra cuánto aporta el modelo y cuánto aporta el arbitraje con el léxico del catálogo.\n"
                        "- El conjunto *Desarrollo* se usó para afinar el léxico (optimista); el *Reservado* da una idea más honesta de la generalización.")
    else:
        st.info("Aún no hay resultados. Elige modelos y configuraciones y pulsa «Ejecutar evaluación».")

# ===================================================================================== 7. historial / herramientas
with tab_hist:
    runs = agent.store.list()[:30]
    if not runs:
        st.caption("Aún no hay ejecuciones.")
    for r in runs:
        cols = st.columns([3, 1.4, 5, 1])
        cols[0].code(r.run_id)
        cols[1].markdown(T.badge(T.STATUS_LABEL.get(r.status, r.status), T.STATUS_TONE.get(r.status, "info")), unsafe_allow_html=True)
        cols[2].write(r.request[:90])
        if cols[3].button("Abrir", key=f"open_{r.run_id}"):
            ss.state = agent.resume(r.run_id) if r.status in ("failed", "aborted") else r
            st.rerun()

with tab_tools:
    for t in reg:
        with st.expander(f"{t.order if t.category == 'flujo' else '—'} · {t.title}  (`{t.id}`)"):
            st.markdown(t.summary or t.description)
            if t.examples:
                st.markdown("**Puedes pedirlo así:** " + " · ".join(f"«{e}»" for e in t.examples))
            if t.synonyms:
                st.caption("También entiende: " + ", ".join(t.synonyms))
            if t.hints:
                st.markdown("\n".join(f"- {h}" for h in t.hints))
            if t.destructive:
                st.caption(f"⚠ {t.destructive_reason or 'sobrescribe datos'} (pide confirmación)")
            e = agent.manual.tool_entry(t.id) if t.id in agent.manual.doc.tools else None
            if e is not None:
                st.caption("Detalle de sus parámetros en la pestaña «📘 Manual».")
    with st.expander("Esquemas function-calling (JSON Schema)"):
        st.code(reg.dump_schemas(), language="json")
