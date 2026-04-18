@echo off
setlocal enabledelayedexpansion

REM YouCook Procurement — универсальный запуск скриптов
REM Использование: run.bat parse_invoice.py --batch
REM               run.bat bot.py
REM               run.bat manage_suppliers.py --list

SET PYTHON=C:\Users\Gigabyte\AppData\Local\Programs\Python\Python313\python.exe
SET SCRIPTS_DIR=%~dp0

REM ── Читаем .env файл если он есть ────────────────────────────────────────────
IF EXIST "%SCRIPTS_DIR%.env" (
    FOR /F "usebackq eol=# tokens=1,* delims==" %%A IN ("%SCRIPTS_DIR%.env") DO (
        SET "%%A=%%B"
    )
)

"%PYTHON%" "%SCRIPTS_DIR%%*"
