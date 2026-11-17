@echo off
rem Abre la interfaz de parametros de escritorio (edita los JSON de Configuraciones). Requiere PySide6.
rem Python: variable AGENTE_PYTHONW (pythonw.exe) o, si no existe, "pythonw" del PATH.
setlocal
set "PYW=pythonw"
if defined AGENTE_PYTHONW set "PYW=%AGENTE_PYTHONW%"
start "" "%PYW%" "%~dp0Flujo_Estimacion\InterfazParametros.py" "%~dp0Configuraciones"
endlocal
