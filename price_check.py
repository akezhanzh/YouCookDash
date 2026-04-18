"""
price_check.py — YouCook Procurement System
- Find cheapest supplier for any SKU or shopping list
- Run anomaly detection (overpricing, duplicates, spikes)
- Output negotiation briefs

Usage:
    python price_check.py --sku "Куриное филе"
    python price_check.py --list shopping_list.txt
    python price_check.py --anomalies
    python price_check.py --negotiate
"""

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from tabulate import tabulate

DB_PATH = Path(__file__).parent / "data" / "YouCookDashOG.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


# ── Cheapest Supplier ─────────────────────────────────────────────────────────

def find_cheapest(sku_name: str) -> None:
    conn = get_conn()
    c = conn.cursor()

    sku_row = c.execute(
        "SELECT id, name, unit FROM sku_catalog WHERE name LIKE ?", (f"%{sku_name}%",)
    ).fetchone()
    if not sku_row:
        print(f"SKU '{sku_name}' not found in catalog.")
        return

    sku_id, sku_full, unit = sku_row

    rows = c.execute(
        """
        SELECT s.name, p.price, p.date
        FROM prices p
        JOIN suppliers s ON s.id = p.supplier_id
        WHERE p.sku_id = ?
        ORDER BY p.date DESC
        """,
        (sku_id,),
    ).fetchall()

    if not rows:
        print(f"No price data for '{sku_full}'.")
        return

    # Latest price per supplier
    seen = {}
    for sup, price, date in rows:
        if sup not in seen:
            seen[sup] = (price, date)

    sorted_suppliers = sorted(seen.items(), key=lambda x: x[1][0])
    cheapest_sup, (cheapest_price, cheapest_date) = sorted_suppliers[0]

    print(f"\n{'─'*60}")
    print(f"  SKU            : {sku_full} ({unit})")
    print(f"  Cheapest       : {cheapest_sup} — {cheapest_price:,.0f} ₸/{unit}")
    if len(sorted_suppliers) > 1:
        current_sup, (current_price, _) = sorted_suppliers[-1]
        overpay = current_price - cheapest_price
        overpay_pct = overpay / cheapest_price * 100
        print(f"  Most expensive : {current_sup} — {current_price:,.0f} ₸/{unit}")
        print(f"  Overpayment    : {overpay:,.0f} ₸/{unit} (+{overpay_pct:.1f}%) → {'SWITCH SUPPLIER' if overpay_pct > 10 else 'OK'}")
    print(f"{'─'*60}")

    # Full table
    table = [(s, f"{p:,.0f} ₸", d) for s, (p, d) in sorted_suppliers]
    print(tabulate(table, headers=["Поставщик", "Цена", "Дата"], tablefmt="rounded_outline"))
    conn.close()


def find_cheapest_list(sku_list: list[str]) -> None:
    total_saving = 0.0
    conn = get_conn()
    c = conn.cursor()

    results = []
    for sku_name in sku_list:
        sku_row = c.execute(
            "SELECT id, name, unit FROM sku_catalog WHERE name LIKE ?", (f"%{sku_name.strip()}%",)
        ).fetchone()
        if not sku_row:
            results.append([sku_name, "—", "—", "—", "SKU not in DB"])
            continue

        sku_id, sku_full, unit = sku_row
        rows = c.execute(
            """
            SELECT s.name, p.price FROM prices p
            JOIN suppliers s ON s.id = p.supplier_id
            WHERE p.sku_id = ?
            ORDER BY p.date DESC
            """,
            (sku_id,),
        ).fetchall()
        if not rows:
            results.append([sku_full, "—", "—", "—", "Нет данных"])
            continue

        seen = {}
        for sup, price in rows:
            if sup not in seen:
                seen[sup] = price

        sorted_sups = sorted(seen.items(), key=lambda x: x[1])
        cheapest_sup, cheapest_price = sorted_sups[0]
        if len(sorted_sups) > 1:
            worst_sup, worst_price = sorted_sups[-1]
            save_per_unit = worst_price - cheapest_price
            results.append(
                [sku_full, unit, f"{cheapest_sup}: {cheapest_price:,.0f}₸",
                 f"{worst_sup}: {worst_price:,.0f}₸", f"Экономия {save_per_unit:,.0f}₸"]
            )
        else:
            results.append([sku_full, unit, f"{cheapest_sup}: {cheapest_price:,.0f}₸", "—", "Только 1 поставщик"])

    conn.close()
    print("\n" + tabulate(
        results,
        headers=["SKU", "Ед", "Дешевле", "Дороже", "Статус"],
        tablefmt="rounded_outline"
    ))


# ── Anomaly Detection ─────────────────────────────────────────────────────────

def run_anomaly_detection(days: int = 7) -> None:
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    print(f"\n{'═'*65}")
    print(f"  ANOMALY DIGEST — last {days} days (since {cutoff})")
    print(f"{'═'*65}")

    # 1. Overpriced purchases
    overpriced = c.execute(
        """
        SELECT sc.name, s.name, p.price,
               MIN(p2.price) as market_min,
               ROUND((p.price - MIN(p2.price)) / MIN(p2.price) * 100, 1) as pct,
               p.date
        FROM prices p
        JOIN sku_catalog sc ON sc.id = p.sku_id
        JOIN suppliers   s  ON s.id  = p.supplier_id
        JOIN prices p2 ON p2.sku_id = p.sku_id
        WHERE p.date >= ? AND p.is_overpriced = 1
        GROUP BY p.id
        ORDER BY pct DESC
        """,
        (cutoff,),
    ).fetchall()

    if overpriced:
        print(f"\n[!] OVERPRICED ({len(overpriced)} items — paid >10% above market min):")
        table = [
            [sku, sup, f"{price:,.0f}₸", f"{mn:,.0f}₸", f"+{pct}%", date]
            for sku, sup, price, mn, pct, date in overpriced
        ]
        print(tabulate(table, headers=["SKU", "Поставщик", "Цена", "Min рынок", "Переплата%", "Дата"],
                       tablefmt="rounded_outline"))
    else:
        print("\n[✓] Нет переплат за период")

    # 2. Duplicate invoices
    dupes = c.execute(
        """
        SELECT s.name, i.invoice_date, i.total_amount, COUNT(*) as cnt
        FROM invoices i
        JOIN suppliers s ON s.id = i.supplier_id
        WHERE i.is_duplicate = 0
        GROUP BY s.name, i.invoice_date, i.total_amount
        HAVING cnt > 1
        """,
    ).fetchall()

    if dupes:
        print(f"\n[!] ДУБЛИРОВАННЫЕ НАКЛАДНЫЕ ({len(dupes)}):")
        print(tabulate(dupes, headers=["Поставщик", "Дата", "Сумма ₸", "Дублей"],
                       tablefmt="rounded_outline"))

    # 3. Price spikes (>20% week-over-week)
    spikes = c.execute(
        """
        WITH ranked AS (
            SELECT sku_id, supplier_id, price, date,
                   LAG(price) OVER (PARTITION BY sku_id, supplier_id ORDER BY date) as prev_price
            FROM prices
        )
        SELECT sc.name, s.name,
               ROUND(prev_price,0) as prev,
               ROUND(price,0) as curr,
               ROUND((price - prev_price)/prev_price*100, 1) as spike_pct,
               date
        FROM ranked r
        JOIN sku_catalog sc ON sc.id = r.sku_id
        JOIN suppliers   s  ON s.id  = r.supplier_id
        WHERE prev_price IS NOT NULL
          AND (price - prev_price)/prev_price > 0.20
          AND date >= ?
        ORDER BY spike_pct DESC
        """,
        (cutoff,),
    ).fetchall()

    if spikes:
        print(f"\n[!] ЦЕНОВЫЕ СКАЧКИ >20% ({len(spikes)}):")
        table = [
            [sku, sup, f"{prev:,.0f}₸", f"{curr:,.0f}₸", f"+{pct}%", date]
            for sku, sup, prev, curr, pct, date in spikes
        ]
        print(tabulate(table, headers=["SKU", "Поставщик", "Было", "Стало", "Рост%", "Дата"],
                       tablefmt="rounded_outline"))
    else:
        print("\n[✓] Ценовых скачков не обнаружено")

    # 4. Unvetted suppliers with orders
    unvetted = c.execute(
        """
        SELECT s.name, COUNT(DISTINCT i.id) as invoice_cnt, SUM(i.total_amount) as spend
        FROM suppliers s
        JOIN invoices i ON i.supplier_id = s.id
        WHERE s.is_vetted = 0
        GROUP BY s.id
        """,
    ).fetchall()

    if unvetted:
        print(f"\n[!] НЕОДОБРЕННЫЕ ПОСТАВЩИКИ с заказами ({len(unvetted)}):")
        table = [(n, c, f"{s:,.0f}₸") for n, c, s in unvetted]
        print(tabulate(table, headers=["Поставщик", "Накладных", "Потрачено ₸"],
                       tablefmt="rounded_outline"))

    conn.close()
    total_flags = len(overpriced) + len(dupes) + len(spikes) + len(unvetted)
    print(f"\n{'═'*65}")
    print(f"  Итого флагов: {total_flags}")
    print(f"{'═'*65}\n")


# ── Negotiation Briefs ────────────────────────────────────────────────────────

def generate_negotiation_briefs() -> None:
    conn = get_conn()
    c = conn.cursor()

    rows = c.execute(
        """
        SELECT s.name,
               sc.name as sku,
               sc.unit,
               p.price as our_price,
               MIN(p2.price) as market_min,
               SUM(il.line_total) as monthly_spend,
               ROUND((p.price - MIN(p2.price))/MIN(p2.price)*100, 1) as overpay_pct
        FROM prices p
        JOIN sku_catalog sc  ON sc.id  = p.sku_id
        JOIN suppliers   s   ON s.id   = p.supplier_id
        JOIN prices      p2  ON p2.sku_id = p.sku_id
        JOIN invoice_lines il ON il.sku_id = p.sku_id
              AND il.invoice_id IN (SELECT id FROM invoices WHERE supplier_id = s.id)
        WHERE p.is_overpriced = 1
          AND p.date >= date('now', '-30 days')
        GROUP BY s.id, sc.id
        ORDER BY (p.price - MIN(p2.price)) * SUM(il.qty) DESC
        """,
    ).fetchall()

    if not rows:
        print("\nНет переплат за последние 30 дней. Переговоры не требуются.")
        return

    print(f"\n{'═'*65}")
    print("  NEGOTIATION BRIEFS — топ поставщики для переговоров")
    print(f"{'═'*65}\n")

    for i, (sup, sku, unit, our_price, mkt_min, spend, overpay_pct) in enumerate(rows, 1):
        target_price = round(mkt_min * 0.95, 0)
        saving_per_unit = our_price - target_price

        print(f"  #{i}  {sup}")
        print(f"  {'─'*55}")
        print(f"  SKU           : {sku}")
        print(f"  Наша цена     : {our_price:,.0f} ₸/{unit}")
        print(f"  Мин. рынок    : {mkt_min:,.0f} ₸/{unit}")
        print(f"  Цель          : {target_price:,.0f} ₸/{unit} (рынок -5%)")
        print(f"  Переплата     : +{overpay_pct}%")
        print(f"  Месяч. траты  : {spend:,.0f} ₸")
        print()
        print(f"  СКРИПТ ДЛЯ ПЕРЕГОВОРОВ:")
        print(f"  «На прошлой неделе мы закупали {sku} по {our_price:,.0f} тенге.")
        print(f"   У нас есть предложение от альтернативного поставщика по")
        print(f"   {mkt_min:,.0f} тенге за {unit}. Готовы остаться с вами")
        print(f"   при цене не выше {target_price:,.0f} тенге.»")
        print()

    conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouCook Price Check & Anomaly Detection")
    parser.add_argument("--sku",       help="Find cheapest supplier for a single SKU")
    parser.add_argument("--list",      help="Text file with SKU names, one per line")
    parser.add_argument("--anomalies", action="store_true", help="Run anomaly detection")
    parser.add_argument("--days",      type=int, default=7, help="Days back for anomaly scan")
    parser.add_argument("--negotiate", action="store_true", help="Generate negotiation briefs")
    args = parser.parse_args()

    if args.sku:
        find_cheapest(args.sku)
    elif args.list:
        skus = Path(args.list).read_text(encoding="utf-8").splitlines()
        find_cheapest_list([s for s in skus if s.strip()])
    elif args.anomalies:
        run_anomaly_detection(args.days)
    elif args.negotiate:
        generate_negotiation_briefs()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
