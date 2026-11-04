@echo off
rem 3 Kas 2026 filtre denemesinden ONCEKI hali geri yukler (filtreler/*.json, sistem/*.py, sistem/web, liste_yenile.bat).
rem Calistirmadan once takipciyi ve liste yenileyiciyi kapat, sonra liste_yenile.bat ile yeniden baslat.
chcp 65001 >nul
cd /d "%~dp0.."
xcopy /y /q "yedek\2026-11-03_filtre_oncesi\filtreler\*.json" "filtreler\"
xcopy /y /q "yedek\2026-11-03_filtre_oncesi\sistem\*.py" "sistem\"
xcopy /y /q /e "yedek\2026-11-03_filtre_oncesi\sistem\web" "sistem\web\"
copy /y "yedek\2026-11-03_filtre_oncesi\liste_yenile.bat" "liste_yenile.bat" >nul
echo Eski hal geri yuklendi.
pause
