@echo off
title YouCook — Обновление Dashboard
cd /d "%~dp0"

echo.
echo  Генерирую dashboard из базы данных...
C:\Users\Gigabyte\AppData\Local\Programs\Python\Python313\python.exe generate_dashboard.py
if errorlevel 1 ( echo  Ошибка генерации! & pause & exit /b 1 )

echo.
echo  Отправляю на GitHub Pages...
git add docs/index.html
git commit -m "dashboard: auto-update %date% %time:~0,5%"
git push

echo.
echo  Готово! Dashboard обновлён на GitHub Pages.
echo  Обновление появится на сайте через 1-2 минуты.
echo.
pause
