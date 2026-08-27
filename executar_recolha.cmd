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

echo [1/4] A extrair programas e orcamentos para os corpora temporarios...
"%PYTHON_EXE%" -u scripts\extract_text.py
if errorlevel 1 goto process_failed
"%PYTHON_EXE%" -u scripts\extract_eu_budget.py --force
if errorlevel 1 goto process_failed

echo [2/4] A recolher noticias, promessas, iniciativas e votacoes...
"%PYTHON_EXE%" -u scripts\political_intelligence.py all --since-days 0 --all-history --force-assembly --max-detail-pages all --checkpoint-interval-seconds 300
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" goto process_finished

if "%PINECONE_API_KEY%"=="" (
    :: Try to load from .env file if it exists
    if exist "%~dp0.env" (
        for /f "tokens=*" %%a in ('findstr /r / "^PINECONE_API_KEY=" "%~dp0.env"') do (
            set "PINECONE_API_KEY=%%a"
            set "PINECONE_API_KEY=%PINECONE_API_KEY:PINECONE_API_KEY="
        )
    )
    if "%PINECONE_API_KEY%"=="" (
        echo.
        echo [AVISO] PINECONE_API_KEY nao esta definida; a ingestao foi ignorada.
        echo Configure a chave e execute novamente para enviar apenas os chunks novos ou alterados.
        echo.
        goto process_finished
    )
)

echo [3/4] A enviar apenas chunks novos ou alterados para o Pinecone...
"%PYTHON_EXE%" -u scripts\upload_pinecone.py --embedding-mode local
set "EXIT_CODE=%ERRORLEVEL%"

:process_finished

echo.
if "%EXIT_CODE%"=="0" (
    echo [SUCESSO] Recolha, exportacao e ingestao incremental terminadas corretamente.
) else if "%EXIT_CODE%"=="130" (
    echo [AVISO] A execucao foi interrompida pelo utilizador.
) else (
    echo [AVISO] O processo terminou com o codigo %EXIT_CODE%.
    echo Os checkpoints gravados permitem retomar a execucao a qualquer momento.
)
echo.
pause
exit /b %EXIT_CODE%

:process_failed
set "EXIT_CODE=%ERRORLEVEL%"
goto process_finished
