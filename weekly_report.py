"""
weekly_report.py — YouCook Procurement System
Generates a markdown weekly report every Monday.

Usage:
    python weekly_report.py
    python weekly_report.py --date 2026-04-07   # override week start
"""

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from tabulate import tabulate

DB_PATH   = Path(__file__).parent / "data" / "YouCookDashOG.db"
REPORT_DIR = Path(__file__).parent / "reports"


def get_conn():
    return sqlite3.connect(DB_PATH)


def build_report(week_start: str) -> str:
    conn = get_conn()
    c    = conn.cursor()

    week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d")
    prev_week_start = (datetime.strptime(week_start, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = []
    lines.append(f"# YouCook — Procurement Weekly Report")
    lines.append(f"**Период:** {week_start} — {week_end}  |  **Создан:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("---\n")

    # ── 1. Total spend ────────────────────────────────────────────────────────
    spend_row = c.execute(
        "SELECT SUM(total_amount), COUNT(*) FROM invoices WHERE invoice_date BETWEEN ? AND ?",
        (week_start, week_end),
    ).fetchone()
    total_spend, invoice_count = spend_row if spend_row[0] else (0, 0)

    lines.append(f"## Общие расходы")
    lines.append(f"- **Закупок:** {invoice_count} накладных")
    lines.append(f"- **Итого потрачено:** {total_spend:,.0f} ₸\n")

    # ── 2. Overpayments ───────────────────────────────────────────────────────
    overpriced = c.execute(
        """
        SELECT sc.name, s.name, p.price,
               MIN(p2.price) as market_min,
               ROUND((p.price - MIN(p2.price))/MIN(p2.price)*100, 1) as pct,
               il.qty,
               ROUND((p.price - MIN(p2.price)) * il.qty, 0) as waste_tenge
        FROM prices p
        JOIN sku_catalog sc ON sc.id = p.sku_id
        JOIN suppliers   s  ON s.id  = p.supplier_id
        JOIN prices p2 ON p2.sku_id = p.sku_id
        JOIN invoice_lines il ON il.sku_id = p.sku_id AND il.unit_price = p.price
        WHERE p.date BETWEEN ? AND ? AND p.is_overpriced = 1
        GROUP BY p.id
        ORDER BY waste_tenge DESC
        """,
        (week_start, week_end),
    ).fetchall()

    total_waste = sum(r[6] for r in overpriced) if overpriced else 0
    lines.append(f"## Переплаты")
    if overpriced:
        lines.append(f"**Всего переплачено: {total_waste:,.0f} ₸**\n")
        tbl = [
            [sku, sup, f"{price:,.0f}₸", f"{mn:,.0f}₸", f"+{pct}%", f"{qty} {u_waste:.0f}₸"]
            for sku, sup, price, mn, pct, qty, u_waste in overpriced
        ]
        # fallback for missing qty
        tbl_clean = []
        for row in overpriced:
            sku, sup, price, mn, pct, qty, waste = row
            tbl_clean.append([sku, sup, f"{price:,.0f}₸", f"{mn:,.0f}₸", f"+{pct}%", f"{waste:,.0f}₸"])
        lines.append(tabulate(tbl_clean, headers=["SKU","Поставщик","Цена","Min","Переплата%","Потери ₸"],
                               tablefmt="pipe"))
    else:
        lines.append("_Переплат не обнаружено_")
    lines.append("")

    # ── 3. Savings opportunities (top 5 supplier switches) ───────────────────
    savings = c.execute(
        """
        SELECT sc.name, sc.unit,
               s_cheap.name  as cheap_sup, MIN(p_cheap.price) as cheap_price,
               s_curr.name   as curr_sup,
               AVG(p_curr.price) as curr_price,
               ROUND(AVG(il.qty), 1) as avg_weekly_qty,
               ROUND((AVG(p_curr.price) - MIN(p_cheap.price)) * AVG(il.qty) * 52, 0) as annual_saving
        FROM sku_catalog sc
        JOIN prices p_cheap ON p_cheap.sku_id = sc.id
        JOIN suppliers s_cheap ON s_cheap.id = p_cheap.supplier_id
        JOIN prices p_curr ON p_curr.sku_id = sc.id
            AND p_curr.supplier_id != p_cheap.supplier_id
            AND p_curr.date >= date('now', '-30 days')
        JOIN suppliers s_curr ON s_curr.id = p_curr.supplier_id
        JOIN invoice_lines il ON il.sku_id = sc.id
        GROUP BY sc.id
        HAVING MIN(p_cheap.price) < AVG(p_curr.price)
        ORDER BY annual_saving DESC
        LIMIT 5
        """,
    ).fetchall()

    lines.append("## Топ-5 возможностей экономии")
    if savings:
        tbl = []
        for row in savings:
            sku, unit, cheap_sup, cheap_p, curr_sup, curr_p, qty, annual = row
            tbl.append([sku, f"{curr_sup}: {curr_p:,.0f}₸", f"{cheap_sup}: {cheap_p:,.0f}₸",
                        f"{annual:,.0f}₸/год"])
        lines.append(tabulate(tbl, headers=["SKU","Текущий поставщик","Альтернатива","Экономия/год"],
                               tablefmt="pipe"))
    else:
        lines.append("_Недостаточно данных_")
    lines.append("")

    # ── 4. Supplier Scorecard ─────────────────────────────────────────────────
    scorecard = c.execute(
        """
        SELECT s.name,
               COUNT(DISTINCT i.id) as invoices,
               ROUND(SUM(i.total_amount), 0) as spend,
               SUM(CASE WHEN p.is_overpriced=1 THEN 1 ELSE 0 END) as overpriced_lines,
               COUNT(p.id) as total_lines,
               ROUND(AVG(CASE WHEN p.is_overpriced=1 THEN p.overprice_pct ELSE NULL END), 1) as avg_overpay
        FROM suppliers s
        JOIN invoices i ON i.supplier_id = s.id
        JOIN prices   p ON p.supplier_id = s.id
        WHERE i.invoice_date BETWEEN ? AND ?
        GROUP BY s.id
        ORDER BY spend DESC
        """,
        (week_start, week_end),
    ).fetchall()

    lines.append("## Рейтинг поставщиков")
    if scorecard:
        tbl = []
        for name, inv, spend, ovr, total, avg_op in scorecard:
            score = "★★★" if (ovr or 0) == 0 else ("★★☆" if (ovr or 0) / max(total, 1) < 0.2 else "★☆☆")
            tbl.append([name, inv, f"{spend:,.0f}₸", f"{ovr}/{total}", f"{avg_op or 0:.1f}%", score])
        lines.append(tabulate(tbl, headers=["Поставщик","Накл.","Потрачено","Переплат","Avg%","Рейтинг"],
                               tablefmt="pipe"))
    else:
        lines.append("_Нет данных за период_")
    lines.append("")

    # ── 5. Price movers ───────────────────────────────────────────────────────
    movers = c.execute(
        """
        WITH this_week AS (
            SELECT sku_id, AVG(price) as avg_price
            FROM prices WHERE date BETWEEN ? AND ?
            GROUP BY sku_id
        ),
        last_week AS (
            SELECT sku_id, AVG(price) as avg_price
            FROM prices WHERE date BETWEEN ? AND ?
            GROUP BY sku_id
        )
        SELECT sc.name,
               ROUND(l.avg_price, 0) as prev,
               ROUND(t.avg_price, 0) as curr,
               ROUND((t.avg_price - l.avg_price)/l.avg_price*100, 1) as change_pct
        FROM this_week t
        JOIN last_week l ON l.sku_id = t.sku_id
        JOIN sku_catalog sc ON sc.id = t.sku_id
        WHERE ABS(t.avg_price - l.avg_price) / l.avg_price > 0.03
        ORDER BY ABS(change_pct) DESC
        LIMIT 10
        """,
        (week_start, week_end, prev_week_start, week_start),
    ).fetchall()

    lines.append("## Изменения цен (неделя к неделе)")
    if movers:
        tbl = []
        for sku, prev, curr, pct in movers:
            arrow = "▲" if pct > 0 else "▼"
            tbl.append([sku, f"{prev:,.0f}₸", f"{curr:,.0f}₸", f"{arrow} {abs(pct)}%"])
        lines.append(tabulate(tbl, headers=["SKU","Прошлая нед.","Эта нед.","Изменение"],
                               tablefmt="pipe"))
    else:
        lines.append("_Значительных изменений нет_")
    lines.append("")

    # ── 6. Action items ───────────────────────────────────────────────────────
    lines.append("## Action Items (приоритет по ROI)")
    actions = []

    if savings:
        for i, row in enumerate(savings[:3], 1):
            sku, unit, cheap_sup, cheap_p, curr_sup, curr_p, qty, annual = row
            actions.append((annual, f"Переключить {sku} с {curr_sup} на {cheap_sup} → экономия {annual:,.0f} ₸/год"))

    if overpriced:
        for row in overpriced[:2]:
            sku, sup, price, mn, pct, qty, waste = row
            actions.append((waste * 52, f"Пересогласовать цену на {sku} у {sup} (+{pct}% к рынку)"))

    if movers:
        for sku, prev, curr, pct in movers:
            if pct > 15:
                actions.append((0, f"Расследовать рост {sku} +{pct}% — рассмотреть форвардную закупку"))

    actions.sort(reverse=True, key=lambda x: x[0])
    for i, (val, text) in enumerate(actions, 1):
        lines.append(f"{i}. {text}")

    if not actions:
        lines.append("_Нет приоритетных действий_")

    lines.append("\n---")
    lines.append(f"_Отчет сгенерирован автоматически системой YouCook Procurement Agent_")

    conn.close()
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="Week start date YYYY-MM-DD (default: last Monday)")
    args = parser.parse_args()

    if args.date:
        week_start = args.date
    else:
        today = datetime.now()
        days_since_monday = today.weekday()
        week_start = (today - timedelta(days=days_since_monday)).strftime("%Y-%m-%d")

    report_md = build_report(week_start)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = REPORT_DIR / f"weekly_report_{week_start}.md"
    filename.write_text(report_md, encoding="utf-8")

    # Save to DB index
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO weekly_reports (report_date, filename, created_at)
           VALUES (?, ?, datetime('now'))""",
        (week_start, str(filename)),
    )
    conn.commit()
    conn.close()

    print(report_md)
    print(f"\n[SAVED] {filename}")


if __name__ == "__main__":
    main()
