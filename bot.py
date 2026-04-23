"""
bot.py — YouCook Procurement Telegram Bot

Сотрудник отправляет PDF или XLSX накладную → бот парсит → сохраняет в БД → отвечает итогом.

Команды:
    /start      — приветствие
    /stats      — общая статистика
    /suppliers  — список поставщиков
    /help       — помощь

Переменные окружения:
    TELEGRAM_TOKEN  — токен от @BotFather (обязательно)
    DB_PATH         — путь к YouCookDashOG.db (по умолчанию ./data/YouCookDashOG.db)
    ALLOWED_IDS     — через запятую ID пользователей, кому разрешён доступ (необязательно)
"""

import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

WAITING_SUPPLIER = 1  # состояние диалога

# ── Пути и конфигурация ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent

# Читаем .env файл (если есть) — работает независимо от способа запуска
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

DB_PATH  = Path(os.environ.get("DB_PATH", BASE_DIR / "data" / "YouCookDashOG.db"))
TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")

# Если задан ALLOWED_IDS — пускать только этих пользователей
_allowed_raw = os.environ.get("ALLOWED_IDS", "")
ALLOWED_IDS  = set(int(x.strip()) for x in _allowed_raw.split(",") if x.strip()) if _allowed_raw else set()

# Добавляем папку проекта в sys.path, чтобы импортировать parse_invoice
sys.path.insert(0, str(BASE_DIR))
from parse_invoice import parse_pdf, parse_xlsx, ingest

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ── Хелперы ────────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH)

def fmt(n: float) -> str:
    return f"{int(n):,}".replace(",", "\u00a0")  # неразрывный пробел как разделитель тысяч

def allowed(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    return update.effective_user.id in ALLOWED_IDS


# ── Команды ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text(
        "👋 *YouCook Procurement Bot*\n\n"
        "Отправь накладную (PDF или XLSX) — автоматически добавлю в базу.\n\n"
        "📊 /stats — общая статистика\n"
        "🏢 /suppliers — список поставщиков\n"
        "❓ /help — эта справка",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    conn = get_conn()
    total    = conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM invoices").fetchone()[0]
    n_sup    = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
    n_inv    = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    n_sku    = conn.execute("SELECT COUNT(DISTINCT sku_id) FROM invoice_lines").fetchone()[0]
    n_anom   = conn.execute("SELECT COUNT(*) FROM anomalies WHERE anomaly_type='overpriced'").fetchone()[0]
    last_dt  = conn.execute("SELECT MAX(invoice_date) FROM invoices").fetchone()[0]
    # Разбивка по городам
    cities   = conn.execute("""
        SELECT COALESCE(s.city,'?'), COALESCE(SUM(i.total_amount),0)
        FROM suppliers s LEFT JOIN invoices i ON i.supplier_id=s.id
        GROUP BY s.city ORDER BY 2 DESC
    """).fetchall()
    conn.close()

    city_lines = "\n".join(f"  • {c}: *{fmt(sp)} ₸*" for c, sp in cities if sp > 0)
    await update.message.reply_text(
        f"📊 *Статистика YouCook*\n\n"
        f"💰 Общий закуп: *{fmt(total)} ₸*\n"
        f"🏢 Поставщиков: {n_sup}\n"
        f"📋 Накладных: {n_inv}\n"
        f"📦 Уникальных SKU: {n_sku}\n"
        f"⚠️ Ценовых аномалий: {n_anom}\n"
        f"📅 Последняя: {last_dt or '—'}\n\n"
        f"🗺️ По городам:\n{city_lines or '  —'}",
        parse_mode="Markdown",
    )


async def cmd_suppliers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    conn = get_conn()
    rows = conn.execute("""
        SELECT COALESCE(s.short_name, s.name), s.city,
               COALESCE(SUM(i.total_amount), 0), COUNT(DISTINCT i.id)
        FROM suppliers s
        LEFT JOIN invoices i ON i.supplier_id = s.id
        GROUP BY s.id ORDER BY 3 DESC
    """).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Поставщиков пока нет.")
        return

    lines = ["🏢 *Поставщики:*\n"]
    for name, city, spend, n in rows:
        lines.append(f"• *{name}* ({city or '—'})\n  {fmt(spend)} ₸ · {n} накл.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Обработчик файлов ──────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    doc   = update.message.document
    fname = doc.file_name or "file"
    ext   = Path(fname).suffix.lower()

    if ext not in (".pdf", ".xlsx", ".xls"):
        await update.message.reply_text(
            "⚠️ Поддерживаются только *PDF* и *XLSX* файлы.\n"
            "Экспортируй накладную из 1С как PDF и пришли ещё раз.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("⏳ Обрабатываю...")

    # Скачиваем файл во временную папку
    tmp_dir  = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / fname
    tg_file  = await ctx.bot.get_file(doc.file_id)
    await tg_file.download_to_drive(tmp_path)

    try:
        if ext == ".pdf":
            data = parse_pdf(tmp_path)
        else:
            data = parse_xlsx(tmp_path)

        if not data.get("supplier"):
            # Сохраняем данные и просим ввести поставщика вручную
            ctx.user_data["pending_data"] = data
            ctx.user_data["pending_msg"]  = msg
            await msg.edit_text(
                "❓ Не удалось определить поставщика автоматически.\n\n"
                "Напиши название поставщика ответным сообщением\n"
                "_(например: ИП Иванов или ТОО АгроМаркет)_",
                parse_mode="Markdown",
            )
            return WAITING_SUPPLIER

        conn    = get_conn()
        summary = ingest(conn, data)

        if summary["duplicate"]:
            conn.close()
            await msg.edit_text(
                f"ℹ️ Накладная №{data.get('invoice_id','?')} от {data.get('date','?')} "
                f"уже есть в базе — пропущена."
            )
            return

        # Актуальный итог из БД
        conn.close()

        ovr_text = ""
        if summary["overpriced"]:
            ovr_text = f"\n\n⚠️ *Переплата* по {len(summary['overpriced'])} позициям:"
            for item in summary["overpriced"][:4]:
                ovr_text += (
                    f"\n  • {item['sku']}: {item['price']:.0f} ₸"
                    f" vs мин {item['min']:.0f} ₸ (*+{item['ovr_pct']}%*)"
                )

        await msg.edit_text(
            f"✅ *Накладная добавлена!*\n\n"
            f"📋 №{data.get('invoice_id') or '—'} от {data.get('date') or '—'}\n"
            f"🏢 {data['supplier']}\n"
            f"💰 {fmt(data['total'])} ₸  ·  {len(data['lines'])} позиций"
            + ovr_text,
            parse_mode="Markdown",
        )
        await _push_dashboard(update)

    except Exception as e:
        log.exception("Ошибка при обработке %s", fname)
        await msg.edit_text(f"❌ Ошибка:\n`{str(e)[:400]}`", parse_mode="Markdown")

    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass


async def _push_dashboard(update: Update):
    """Регенерирует дашборд и пушит на GitHub."""
    status_msg = await update.message.reply_text("⏳ Обновляю дашборд...")
    try:
        subprocess.run([sys.executable, str(BASE_DIR / "generate_dashboard.py")], check=True, cwd=BASE_DIR)
        subprocess.run(["git", "add", "-A"], check=True, cwd=BASE_DIR)
        subprocess.run(["git", "commit", "-m", "update: dashboard via telegram bot"], cwd=BASE_DIR)
        subprocess.run(["git", "push"], check=True, cwd=BASE_DIR)
        await status_msg.edit_text("🌐 Дашборд обновлён!\nyoucookdash.onrender.com")
    except Exception as e:
        log.warning("Ошибка при обновлении дашборда: %s", e)
        await status_msg.edit_text("⚠️ Накладная добавлена, но дашборд не обновился автоматически.\nЗапусти обновить.bat на компьютере.")


async def handle_supplier_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Пользователь написал название поставщика вручную."""
    if not allowed(update):
        return ConversationHandler.END

    supplier_name = update.message.text.strip()
    data  = ctx.user_data.get("pending_data")
    msg   = ctx.user_data.get("pending_msg")

    if not data:
        await update.message.reply_text("⚠️ Нет ожидающего файла. Отправь накладную заново.")
        return ConversationHandler.END

    data["supplier"] = supplier_name
    ctx.user_data.clear()

    conn = get_conn()
    try:
        summary = ingest(conn, data)

        if summary["duplicate"]:
            conn.close()
            await update.message.reply_text(
                f"ℹ️ Накладная №{data.get('invoice_id','?')} уже есть в базе."
            )
            return ConversationHandler.END

        conn.close()

        ovr_text = ""
        if summary["overpriced"]:
            ovr_text = f"\n\n⚠️ *Переплата* по {len(summary['overpriced'])} позициям"

        await update.message.reply_text(
            f"✅ *Накладная добавлена!*\n\n"
            f"📋 №{data.get('invoice_id') or '—'} от {data.get('date') or '—'}\n"
            f"🏢 {data['supplier']}\n"
            f"💰 {fmt(data['total'])} ₸  ·  {len(data['lines'])} позиций"
            + ovr_text,
            parse_mode="Markdown",
        )
        await _push_dashboard(update)
    except Exception as e:
        log.exception("Ошибка при обработке с ручным поставщиком")
        await update.message.reply_text(f"❌ Ошибка: `{str(e)[:300]}`", parse_mode="Markdown")

    return ConversationHandler.END


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Фото накладной — просим прислать PDF."""
    if not allowed(update):
        return
    await update.message.reply_text(
        "📷 Фото получено, но для точного распознавания нужен *PDF*.\n\n"
        "Как получить PDF из 1С:\n"
        "  Накладная → Печать → Сохранить как PDF\n\n"
        "Или XLSX — экспортируй из таблицы и пришли файл.",
        parse_mode="Markdown",
    )


# ── Запуск ─────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN:
        print("❌ Укажи TELEGRAM_TOKEN в переменных окружения или в .env файле.")
        sys.exit(1)

    if not DB_PATH.exists():
        print(f"❌ База данных не найдена: {DB_PATH}")
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()

    # Диалог: файл → (если нет поставщика) → ответ текстом
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, handle_document)],
        states={
            WAITING_SUPPLIER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_supplier_reply)
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_start))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CommandHandler("suppliers", cmd_suppliers))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    log.info("Бот запущен. DB: %s", DB_PATH)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
