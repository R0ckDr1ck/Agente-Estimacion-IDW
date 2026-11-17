@echo off
rem Agente orquestador IDW. Sin argumentos abre el chat; con argumentos ejecuta ese comando (init, plan, run, check, fix-paths, doctor...).
rem Intérprete de Python: python_local.txt -> .venv\Scripts\python.exe -> variable AGENTE_PYTHON -> "python" del PATH.
setlocal
pushd "%~dp0"
set "PY=python"
if defined AGENTE_PYTHON set "PY=%AGENTE_PYTHON%"
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if exist "python_local.txt" set /p PY=<"python_local.txt"
if "%~1"=="" ( "%PY%" -m agente chat ) else ( "%PY%" -m agente %* )
popd
endlocal
