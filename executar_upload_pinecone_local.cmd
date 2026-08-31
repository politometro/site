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
    echo.
    echo [ERRO] Nao foi encontrado Python no sistema.
    echo Instale o Python 3.11 ou superior e tente novamente.
    echo.
    pause
    exit /b 1
)
set "PYTHON_EXE=python"

:python_ready
if not exist "%~dp0scripts\upload_pinecone.py" (
    echo.
    echo [ERRO] Nao foi encontrado o script em scripts\upload_pinecone.py.
    echo.
    pause
    exit /b 1
)

set "LIMIT_ARG="
if not "%~1"=="" set "LIMIT_ARG=--limit %~1"
echo A enviar localmente apenas chunks novos ou alterados para o Pinecone...
echo A chave e lida do ficheiro .env ou da variavel de ambiente PINECONE_API_KEY.
echo Para um backfill faseado, passe o limite como primeiro argumento, por exemplo: executar_upload_pinecone_local.cmd 5000
"%PYTHON_EXE%" -u scripts\upload_pinecone.py --embedding-mode local %LIMIT_ARG%
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo [SUCESSO] Upload local incremental terminado.
) else (
    echo [AVISO] O processo terminou com o codigo %EXIT_CODE%.
)
echo.
pause
exit /b %EXIT_CODE%