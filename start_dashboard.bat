@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=D:\miniconda3\envs\openai\python.exe"

if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%SCRIPT_DIR%run_dashboard.py" --env-file "%SCRIPT_DIR%.env"
