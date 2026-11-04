@echo off
rem 1 yillik indirme bitince Kasim gunlerini (2-3 Kas 2026) ekler, sonra gercekci backtest'i calistirir.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
py -m backtest.veri_genis --start 2025-11-03 --end 2026-11-03
py -m backtest.yuksek_bt --veri data_genis
