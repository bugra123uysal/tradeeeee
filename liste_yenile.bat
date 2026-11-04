@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem bugraaa_premarket ve bugraaa_trade listelerinin ikisini de 30 saniyede bir yeniler.
rem TradingView Desktop acik olmali. NY 16:00'da (TR kis saatiyle 00:00) kendiliginden kapanir.
rem Takipci + site (sadece TradingView "yuksek" alarmlari): http://127.0.0.1:8765
set PYTHONIOENCODING=utf-8
start "devam_takip" /b ".venv\Scripts\python.exe" -m sistem.devam_takip 2>> "filtreler\devam_takip_cmd.log"
rem (3 Kas 2026) gunluk_takip kapatildi: sitede sadece TradingView yuksek alarmlari gosteriliyor
".venv\Scripts\python.exe" -m sistem.liste_yenile --interval 30 --interval-market 30 >> "filtreler\liste_yenile_cmd.log" 2>&1
