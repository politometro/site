@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title Politometro - Recolha integral de inteligencia politica
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto python_ready

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERRO] Nao foi encontrado Python no sistema.
    echo Instale o Python 3.11 ou superior e tente novamente.
    echo.
    pause
    exit /b 1
)
set "PYTHON_EXE=python"

:python_ready
if not exist "%~dp0scripts\political_intelligence.py" (
    echo.
    echo [ERRO] Nao foi encontrado o script em scripts\political_intelligence.py.
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -u scripts\political_intelligence.py all --since-days 0 --all-history --force-assembly --max-detail-pages all
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [SUCESSO] Todo o processo de recolha e exportacao terminou corretamente.
) else if "%EXIT_CODE%"=="130" (
    echo [AVISO] A execucao foi interrompida pelo utilizador.
) else (
    echo [AVISO] O processo terminou com o codigo %EXIT_CODE%.
    echo Os checkpoints gravados permitem retomar a execucao a qualquer momento.
)
echo.
pause
exit /b %EXIT_CODE%
