@echo off
rem Interfaz web local del agente (Streamlit).
rem Intérprete de Python, en este orden: python_local.txt (una línea con la ruta a python.exe; no se sube a Git) ->
rem .venv\Scripts\python.exe de esta carpeta -> variable AGENTE_PYTHON -> "python" del PATH.
setlocal
pushd "%~dp0"
set "PY=python"
if defined AGENTE_PYTHON set "PY=%AGENTE_PYTHON%"
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if exist "python_local.txt" set /p PY=<"python_local.txt"
"%PY%" -m agente ui
popd
endlocal
