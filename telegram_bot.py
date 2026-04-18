"""
telegram_bot.py — YouCook Procurement Order Submission Bot
Chefs/warehouse staff submit orders via Telegram.
Bot auto-checks price vs DB, flags overpriced items, routes for approval.

Setup:
    1. Create bot via @BotFather → get TOKEN
    2. pip install python-telegram-bot
    3. Set BOT_TOKEN and MANAGER_CHAT_ID below
    4. python telegram_bot.py

Commands:
    /order  — submit a new order
    /price  — check current price for an SKU
    /report — today's order summary (manager only)
"""

import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN       = os.environ.get("YOUCOOK_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MANAGER_CHAT_ID = int(os.environ.get("MANAGER_CHAT_ID", "0"))   # Telegram chat ID for approvals
CFO_CHAT_ID     = int(os.environ.get("CFO_CHAT_ID", "0"))        # For orders > 500,000 ₸
DB_PATH         = Path(__file__).parent / "data" / "YouCookDashOG.db"

# Conversation states
SUPPLIER, SKU, QTY, PRICE, CONFIRM = range(5)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── DB Helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH)


def get_market_min(sku_name: str) -> tuple[float | None, str | None]:
    conn = get_conn()
    c = conn.cursor()
    row = c.execute(
        """SELECT MIN(p.price), s.name
           FROM prices p
           JOIN sku_catalog sc ON sc.id = p.sku_id
           JOIN suppliers   s  ON s.id  = p.supplier_id
           WHERE sc.name LIKE ?
           ORDER BY p.price ASC LIMIT 1""",
        (f"%{sku_name}%",),
    ).fetchone()
    conn.close()
    return (row[0], row[1]) if row and row[0] else (None, None)


def supplier_is_vetted(supplier_name: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT is_vetted FROM suppliers WHERE name LIKE ?", (f"%{supplier_name}%",)
    ).fetchone()
    conn.close()
    return bool(row and row[0])


def save_pending_order(user_data: dict, status: str):
    conn = get_conn()
    # Record as WhatsApp-style invoice stub
    conn.execute(
        """INSERT INTO invoices (invoice_id, supplier_id, invoice_date, total_amount, source, notes)
           SELECT ?, s.id, date('now'), ?, 'telegram', ?
           FROM suppliers s WHERE s.name LIKE ?""",
        (
            f"TG-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            user_data["price"] * user_data["qty"],
            f"status={status} user={user_data.get('username', '?')}",
            f"%{user_data['supplier']}%",
        ),
    )
    conn.commit()
    conn.close()


# ── Approval logic ────────────────────────────────────────────────────────────

def check_order_approval(order: dict) -> dict:
    """
    Returns:
        approval_required: bool
        approver: 'manager' | 'cfo' | None
        reason: str
    """
    total = order["price"] * order["qty"]
    market_min, _ = get_market_min(order["sku"])

    if not supplier_is_vetted(order["supplier"]):
        return {"approval_required": True, "approver": "manager",
                "reason": f"Поставщик '{order['supplier']}' не проверен"}

    if market_min and order["price"] > market_min * 1.10:
        pct = (order["price"] - market_min) / market_min * 100
        return {"approval_required": True, "approver": "manager",
                "reason": f"Цена {order['price']:,.0f}₸ превышает минимум рынка на {pct:.1f}% (мин: {market_min:,.0f}₸)"}

    if total > 500_000:
        return {"approval_required": True, "approver": "cfo",
                "reason": f"Сумма заказа {total:,.0f}₸ > 500,000₸ — требуется согласование CFO"}

    return {"approval_required": False, "approver": None, "reason": "OK"}


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот закупок YouCook.\n\n"
        "Команды:\n"
        "/order — подать заявку на закупку\n"
        "/price <SKU> — проверить цену\n"
        "/report — сводка заказов (только менеджеры)"
    )


async def order_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    ctx.user_data["username"] = update.effective_user.username or update.effective_user.full_name
    await update.message.reply_text("Введите название поставщика:")
    return SUPPLIER


async def order_supplier(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["supplier"] = update.message.text.strip()
    await update.message.reply_text("Введите наименование товара (SKU):")
    return SKU


async def order_sku(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sku"] = update.message.text.strip()
    await update.message.reply_text("Введите количество (число):")
    return QTY


async def order_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["qty"] = float(update.message.text.strip().replace(",", "."))
    except ValueError:
        await update.message.reply_text("Некорректное количество. Попробуйте ещё раз:")
        return QTY
    await update.message.reply_text("Введите цену за единицу (₸):")
    return PRICE


async def order_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ctx.user_data["price"] = float(update.message.text.strip().replace(",", ".").replace(" ", ""))
    except ValueError:
        await update.message.reply_text("Некорректная цена. Попробуйте ещё раз:")
        return PRICE

    order = ctx.user_data
    total = order["price"] * order["qty"]
    market_min, cheapest_sup = get_market_min(order["sku"])
    approval = check_order_approval(order)

    summary = (
        f"📋 Заявка на закупку\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Поставщик : {order['supplier']}\n"
        f"Товар     : {order['sku']}\n"
        f"Кол-во    : {order['qty']}\n"
        f"Цена      : {order['price']:,.0f} ₸\n"
        f"Итого     : {total:,.0f} ₸\n"
    )

    if market_min:
        diff = order["price"] - market_min
        pct  = diff / market_min * 100
        if diff > 0:
            summary += f"\n⚠️ Мин. на рынке: {market_min:,.0f}₸ ({cheapest_sup})\n"
            summary += f"   Переплата: +{diff:,.0f}₸ (+{pct:.1f}%)\n"
        else:
            summary += f"\n✅ Цена ниже рыночного минимума ({market_min:,.0f}₸)\n"

    if approval["approval_required"]:
        summary += f"\n🔴 ТРЕБУЕТ СОГЛАСОВАНИЯ: {approval['reason']}"
        keyboard = [[InlineKeyboardButton("Отправить на согласование", callback_data="send_approval")]]
    else:
        summary += "\n✅ Заказ в рамках нормы"
        keyboard = [[InlineKeyboardButton("Подтвердить заказ", callback_data="confirm_order")]]

    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel_order")])
    ctx.user_data["approval"] = approval

    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRM


async def order_confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order    = ctx.user_data
    approval = order.get("approval", {})
    total    = order["price"] * order["qty"]

    if query.data == "cancel_order":
        await query.edit_message_text("Заявка отменена.")
        return ConversationHandler.END

    if query.data == "confirm_order":
        save_pending_order(order, "confirmed")
        await query.edit_message_text(f"✅ Заказ подтверждён и записан в базу.")
        return ConversationHandler.END

    if query.data == "send_approval":
        save_pending_order(order, "pending_approval")
        approver_id = CFO_CHAT_ID if approval.get("approver") == "cfo" else MANAGER_CHAT_ID
        if approver_id:
            msg = (
                f"🔔 ЗАПРОС НА СОГЛАСОВАНИЕ\n"
                f"Пользователь: {order['username']}\n"
                f"Поставщик: {order['supplier']}\n"
                f"Товар: {order['sku']} × {order['qty']}\n"
                f"Цена: {order['price']:,.0f}₸  |  Итого: {total:,.0f}₸\n"
                f"Причина: {approval['reason']}"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{query.from_user.id}"),
                 InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{query.from_user.id}")]
            ])
            await ctx.bot.send_message(approver_id, msg, reply_markup=keyboard)
        await query.edit_message_text("📤 Заявка отправлена на согласование. Ожидайте ответа.")
        return ConversationHandler.END


async def price_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Использование: /price <название товара>")
        return
    sku = " ".join(ctx.args)
    market_min, cheapest_sup = get_market_min(sku)
    if market_min:
        await update.message.reply_text(
            f"💰 {sku}\nМинимальная цена: {market_min:,.0f} ₸\nПоставщик: {cheapest_sup}"
        )
    else:
        await update.message.reply_text(f"Товар '{sku}' не найден в базе.")


async def daily_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in [MANAGER_CHAT_ID, CFO_CHAT_ID]:
        await update.message.reply_text("Нет доступа.")
        return
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.name, SUM(i.total_amount), COUNT(i.id)
           FROM invoices i JOIN suppliers s ON s.id=i.supplier_id
           WHERE i.invoice_date = date('now')
           GROUP BY s.id ORDER BY SUM(i.total_amount) DESC""",
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Сегодня заказов не было.")
        return
    text = "📊 Сводка заказов сегодня:\n" + "\n".join(
        f"{name}: {total:,.0f}₸ ({cnt} накл.)" for name, total, cnt in rows
    )
    await update.message.reply_text(text)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("order", order_start)],
        states={
            SUPPLIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, order_supplier)],
            SKU:      [MessageHandler(filters.TEXT & ~filters.COMMAND, order_sku)],
            QTY:      [MessageHandler(filters.TEXT & ~filters.COMMAND, order_qty)],
            PRICE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, order_price)],
            CONFIRM:  [CallbackQueryHandler(order_confirm_callback)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("price",  price_check))
    app.add_handler(CommandHandler("report", daily_report))
    app.add_handler(conv)

    print("[Telegram Bot] Running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
