@echo off
setlocal EnableExtensions

cd /d "%~dp0"
title Politometro - Upload local incremental para Pinecone
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if exist "%PYTHON_EXE%" goto python_ready
where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Nao foi encontrado Python.
    exit /b 1
)
set "PYTHON_EXE=python"

:python_ready
if "%PINECONE_API_KEY%"=="" (
    :: Try to load from .env file if it exists
    if exist "%~dp0.env" (
        for /f "tokens=*" %%a in ('findstr /r / "^PINECONE_API_KEY=" "%~dp0.env"') do (
            set "PINECONE_API_KEY=%%a"
            set "PINECONE_API_KEY=%PINECONE_API_KEY:PINECONE_API_KEY="
        )
    )
    if "%PINECONE_API_KEY%"=="" (
        echo [ERRO] Defina PINECONE_API_KEY antes de iniciar o upload.
        echo.
        echo Configure a variavel de ambiente PINECONE_API_KEY com a sua chave do Pinecone.
        echo Ou adicione PINECONE_API_KEY no ficheiro .env da pasta do script.
        pause
        exit /b 1
    )
)

set "LIMIT_ARG="
if not "%~1"=="" set "LIMIT_ARG=--limit %~1"
echo A enviar localmente apenas chunks novos ou alterados para o Pinecone...
echo Para um backfill faseado, passe o limite como primeiro argumento, por exemplo: executar_upload_pinecone_local.cmd 5000
"%PYTHON_EXE%" -u scripts\upload_pinecone.py --embedding-mode local %LIMIT_ARG%
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo [SUCESSO] Upload local incremental terminado.
exit /b %EXIT_CODE%
