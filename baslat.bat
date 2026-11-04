@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ilk kurulum: Python ortami hazirlaniyor...
    python -m venv .venv || (echo Python bulunamadi. python.org'dan Python 3.11+ kur. & pause & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo Paket kurulumu basarisiz. & pause & exit /b 1)
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo .env olusturuldu. Alpaca ve Telegram anahtarlarini yaz, kaydet, sonra baslat.bat'i tekrar calistir.
    notepad ".env"
    exit /b 0
)

echo Bugra Gunluk Trade Sistemi basliyor... (durdurmak icin Ctrl+C)
".venv\Scripts\python.exe" -m sistem.main
pause
