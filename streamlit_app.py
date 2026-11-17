"""Punto de entrada para Streamlit (local y Streamlit Community Cloud).

    streamlit run streamlit_app.py

Solo delega en agente/ui_streamlit.py, que es la interfaz real (también se abre con `python -m agente ui` o run_ui.bat).
"""

import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().parent / "agente" / "ui_streamlit.py"), run_name="__main__")
