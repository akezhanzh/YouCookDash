@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ═══════════════════════════════════════
echo   YouCook — Обновление дашборда
echo ═══════════════════════════════════════
echo.

echo [1/3] Парсим накладные из папки invoices...
python parse_invoice.py --batch
if errorlevel 1 (
    echo ОШИБКА при парсинге!
    pause
    exit /b 1
)

echo.
echo [2/3] Генерируем дашборд...
python generate_dashboard.py
if errorlevel 1 (
    echo ОШИБКА при генерации!
    pause
    exit /b 1
)

echo.
echo [3/3] Публикуем на сайт...
git add -A
git commit -m "update: dashboard %date% %time%"
git push
if errorlevel 1 (
    echo ОШИБКА при публикации!
    pause
    exit /b 1
)

echo.
echo ✅ Готово! Дашборд обновлён.
echo    Открой: https://youcookdash.onrender.com
echo.
pause
