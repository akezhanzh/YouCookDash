"""
whatsapp_monitor.py — YouCook Procurement System
Monitors a WhatsApp Web group for price/order messages and ingests them into YouCookDashOG.db.

Requires:
    pip install playwright python-telegram-bot
    playwright install chromium

Run:
    python whatsapp_monitor.py --group "Закупки YouCook" --interval 60
"""

import asyncio
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

DB_PATH = Path(__file__).parent / "data" / "YouCookDashOG.db"

# ── Regex patterns for Kazakh/Russian procurement messages ────────────────────
PRICE_LINE_PATTERN = re.compile(
    r"(?P<sku>[А-Яа-яЁёA-Za-z][^\d\n]{2,40}?)\s*[—\-–]\s*"
    r"(?P<price>[\d\s]+[.,]?\d*)\s*(?:тг|₸|тенге)?\s*"
    r"(?:/|за|per)?\s*(?P<unit>кг|шт|л|уп|пач|бут|г|мл)?",
    re.IGNORECASE | re.UNICODE,
)
ORDER_CONFIRM_PATTERN = re.compile(
    r"(принят|заказ|подтвержд|ok|ок|принимаю)",
    re.IGNORECASE | re.UNICODE,
)
SENDER_PRICE_PATTERN = re.compile(
    r"(?P<sku>[А-Яа-яёЁ][^\n\d]{2,30})\s+"
    r"(?P<qty>[\d]+)\s*(?P<unit>кг|шт|л|уп|пач)?\s+"
    r"(?P<price>[\d\s]{3,7})\s*(?:₸|тг|тенге)",
    re.IGNORECASE | re.UNICODE,
)


def clean_number(s: str) -> float:
    return float(re.sub(r"[\s\u00a0]", "", s).replace(",", "."))


def get_or_create_supplier(conn, name: str) -> int:
    c = conn.cursor()
    row = c.execute("SELECT id FROM suppliers WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    c.execute("INSERT INTO suppliers (name, is_vetted) VALUES (?, 0)", (name,))
    conn.commit()
    return c.lastrowid


def get_or_create_sku(conn, name: str, unit: str = "кг") -> int:
    c = conn.cursor()
    row = c.execute("SELECT id FROM sku_catalog WHERE name LIKE ?", (f"%{name.strip()[:30]}%",)).fetchone()
    if row:
        return row[0]
    c.execute("INSERT INTO sku_catalog (name, unit) VALUES (?, ?)", (name.strip()[:60], unit))
    conn.commit()
    return c.lastrowid


def save_whatsapp_price(conn, supplier_name: str, sku_name: str, price: float, unit: str, qty: float = 1.0):
    supplier_id = get_or_create_supplier(conn, supplier_name)
    sku_id      = get_or_create_sku(conn, sku_name, unit)
    today       = datetime.now().strftime("%Y-%m-%d")

    # Check for market min & flag overpriced
    row = conn.execute("SELECT MIN(price) FROM prices WHERE sku_id=?", (sku_id,)).fetchone()
    market_min = row[0] if row and row[0] else None
    is_overpriced = 1 if market_min and price > market_min * 1.10 else 0

    # Insert invoice stub
    conn.execute(
        """INSERT OR IGNORE INTO invoices (invoice_id, supplier_id, invoice_date, total_amount, source)
           VALUES (?, ?, ?, ?, 'whatsapp')""",
        (f"WA-{today}-{int(time.time())}", supplier_id, today, price * qty),
    )
    invoice_id = conn.execute(
        "SELECT id FROM invoices WHERE invoice_id = ?", (f"WA-{today}-{int(time.time())}",)
    ).fetchone()
    invoice_db_id = invoice_id[0] if invoice_id else None

    conn.execute(
        """INSERT INTO prices (sku_id, supplier_id, price, unit, date, source, is_overpriced)
           VALUES (?,?,?,?,?,?,?)""",
        (sku_id, supplier_id, price, unit or "кг", today, "whatsapp", is_overpriced),
    )
    conn.commit()
    return is_overpriced


def parse_message(text: str) -> list[dict]:
    """Extract structured price data from a raw message string."""
    results = []
    for m in PRICE_LINE_PATTERN.finditer(text):
        try:
            results.append({
                "sku":   m.group("sku").strip(),
                "price": clean_number(m.group("price")),
                "unit":  m.group("unit") or "кг",
                "qty":   1.0,
            })
        except ValueError:
            continue
    for m in SENDER_PRICE_PATTERN.finditer(text):
        try:
            results.append({
                "sku":   m.group("sku").strip(),
                "price": clean_number(m.group("price")),
                "unit":  m.group("unit") or "кг",
                "qty":   float(m.group("qty")),
            })
        except ValueError:
            continue
    return results


# ── Playwright WhatsApp Web monitor ───────────────────────────────────────────

async def monitor_whatsapp(group_name: str, interval: int = 60):
    print(f"[WA Monitor] Starting — watching group: '{group_name}' every {interval}s")
    conn = sqlite3.connect(DB_PATH)
    seen_messages = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Must be False — WhatsApp needs QR scan
            args=["--no-sandbox"],
        )
        context = await browser.new_context(
            user_data_dir=str(Path(__file__).parent / "data" / "wa_session"),
        )
        page = await context.new_page()
        await page.goto("https://web.whatsapp.com")
        print("[WA] Scan QR code in the browser window, then press Enter here...")
        input()

        # Find and open the group
        search = page.locator('[data-testid="chat-list-search"]')
        await search.fill(group_name)
        await page.wait_for_timeout(2000)
        await page.locator(f'span[title="{group_name}"]').first.click()
        print(f"[WA] Opened group: {group_name}")

        while True:
            try:
                messages = await page.locator('[data-testid="msg-container"]').all()
                for msg_el in messages[-30:]:  # last 30 messages
                    try:
                        text = await msg_el.inner_text()
                        msg_id = hash(text[:80])
                        if msg_id in seen_messages:
                            continue
                        seen_messages.add(msg_id)

                        items = parse_message(text)
                        if items:
                            # Try to extract sender name
                            try:
                                sender = await msg_el.locator('[data-testid="author"]').inner_text()
                            except Exception:
                                sender = "WhatsApp-Unknown"

                            for item in items:
                                is_ovr = save_whatsapp_price(
                                    conn, sender, item["sku"], item["price"], item["unit"], item["qty"]
                                )
                                flag = " ⚠️ OVERPRICED" if is_ovr else ""
                                print(f"  [NEW] {sender}: {item['sku']} {item['price']:,.0f}₸/{item['unit']}{flag}")
                    except Exception:
                        continue

                await asyncio.sleep(interval)
            except KeyboardInterrupt:
                print("\n[WA] Stopped by user.")
                break

    conn.close()


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="WhatsApp Web price monitor")
    parser.add_argument("--group",    default="Закупки YouCook", help="WhatsApp group name")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval in seconds")
    args = parser.parse_args()

    asyncio.run(monitor_whatsapp(args.group, args.interval))
