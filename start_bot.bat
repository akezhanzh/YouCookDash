@echo off
title YouCook Procurement Bot
cd /d "%~dp0"

REM Читаем .env
FOR /F "usebackq eol=# tokens=1,* delims==" %%A IN (".env") DO SET "%%A=%%B"

echo.
echo  ====================================
echo   YouCook Procurement Bot
echo  ====================================
echo   Бот запущен. Не закрывай это окно.
echo   Для остановки нажми Ctrl+C
echo  ====================================
echo.

C:\Users\Gigabyte\AppData\Local\Programs\Python\Python313\python.exe bot.py

echo.
echo  Бот остановлен.
pause
