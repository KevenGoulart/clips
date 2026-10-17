@echo off
cd /d "%~dp0"

rem Se o app ja estiver rodando, so abre o navegador
netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul && (
    echo Clips IA ja esta aberto. Abrindo o navegador...
    start "" http://127.0.0.1:8000
    exit /b
)

tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "" /B "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve
start "" http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000
pause
