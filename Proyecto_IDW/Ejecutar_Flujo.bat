@echo off
rem Ejecuta el flujo de estimacion completo segun Configuraciones. Argumentos opcionales: --pasos a,b --desde x --hasta y
rem Python: variable AGENTE_PYTHON o, si no existe, "python" del PATH (usa el entorno donde instalaste requirements-flujo.txt).
setlocal
set "PY=python"
if defined AGENTE_PYTHON set "PY=%AGENTE_PYTHON%"
"%PY%" "%~dp0Flujo_Estimacion\EjecutarFlujo.py" --config-dir "%~dp0Configuraciones" %*
pause
endlocal
