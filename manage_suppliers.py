"""
manage_suppliers.py — YouCook Supplier Management
Просмотр, добавление, алиасы, проверка и редактирование поставщиков.

Использование:
    python manage_suppliers.py --list               # все поставщики
    python manage_suppliers.py --add                # интерактивное добавление
    python manage_suppliers.py --alias              # добавить псевдоним
    python manage_suppliers.py --vet 3              # одобрить поставщика id=3
    python manage_suppliers.py --show 3             # детали по поставщику
    python manage_suppliers.py --merge 4 2          # слить id=4 в id=2 (дубль)
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from tabulate import tabulate

DB_PATH = Path(__file__).parent / "data" / "YouCookDashOG.db"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def get_conn():
    return sqlite3.connect(DB_PATH)


# ── СПИСОК ────────────────────────────────────────────────────────────────────

def list_suppliers():
    conn = get_conn()
    rows = conn.execute("""
        SELECT
            s.id,
            s.short_name,
            s.name,
            s.bin,
            s.legal_form,
            s.city,
            s.region,
            CASE WHEN s.is_vetted=1 THEN '✅' ELSE '⛔' END as vetted,
            COUNT(DISTINCT i.id) as invoices,
            COALESCE(SUM(i.total_amount),0) as spend,
            GROUP_CONCAT(sa.alias, ' | ') as aliases
        FROM suppliers s
        LEFT JOIN invoices i ON i.supplier_id = s.id
        LEFT JOIN supplier_aliases sa ON sa.supplier_id = s.id
        GROUP BY s.id
        ORDER BY spend DESC
    """).fetchall()
    conn.close()

    if not rows:
        print("Поставщиков нет.")
        return

    table = []
    for r in rows:
        sup_id, short, name, bin_, lf, city, region, vet, inv, spend, aliases = r
        display = short or (name[:35] + "…" if name and len(name) > 35 else name)
        table.append([
            sup_id,
            display,
            bin_ or "—",
            lf or "—",
            city or "—",
            vet,
            inv,
            f"{spend:,.0f} ₸",
            aliases or "—",
        ])

    print("\n" + tabulate(
        table,
        headers=["ID", "Поставщик", "БИН", "Форма", "Город", "Проверен", "Накл.", "Расход", "Псевдонимы"],
        tablefmt="rounded_outline"
    ))
    print(f"  Итого: {len(rows)} поставщиков\n")


# ── ДЕТАЛИ ────────────────────────────────────────────────────────────────────

def show_supplier(sup_id: int):
    conn = get_conn()
    s = conn.execute("SELECT * FROM suppliers WHERE id=?", (sup_id,)).fetchone()
    if not s:
        print(f"Поставщик id={sup_id} не найден.")
        return

    cols = [d[0] for d in conn.execute("PRAGMA table_info(suppliers)").fetchall()]
    print(f"\n{'─'*55}")
    for col, val in zip(cols, s):
        print(f"  {col:<16}: {val}")

    aliases = conn.execute("SELECT alias FROM supplier_aliases WHERE supplier_id=?", (sup_id,)).fetchall()
    print(f"  {'aliases':<16}: {', '.join(a[0] for a in aliases) or '—'}")

    top_sku = conn.execute("""
        SELECT sc.name, SUM(il.line_total) as t, AVG(il.unit_price) as avg_p
        FROM invoice_lines il
        JOIN invoices i ON i.id = il.invoice_id
        JOIN sku_catalog sc ON sc.id = il.sku_id
        WHERE i.supplier_id = ?
        GROUP BY sc.id ORDER BY t DESC LIMIT 10
    """, (sup_id,)).fetchall()

    if top_sku:
        print(f"\n  Топ-10 SKU у этого поставщика:")
        tbl = [(n, f"{t:,.0f} ₸", f"{p:,.0f} ₸/ед") for n, t, p in top_sku]
        print(tabulate(tbl, headers=["SKU", "Итого", "Avg цена"], tablefmt="simple", colalign=("left","right","right")))
    conn.close()
    print()


# ── ДОБАВИТЬ ВРУЧНУЮ ──────────────────────────────────────────────────────────

def add_supplier():
    print("\n=== Добавление нового поставщика ===")
    name   = input("  Полное название (напр. ТОО «АгроМаркет»): ").strip()
    short  = input("  Краткое название (напр. АгроМаркет): ").strip()
    bin_   = input("  БИН/ИИН (12 цифр): ").strip() or None
    lf_m   = re.match(r'^(ТОО|ИП|АО|ООО|ЧП|КХ)\b', name, re.IGNORECASE)
    lf     = lf_m.group(1).upper() if lf_m else input("  Правовая форма (ТОО/ИП/АО): ").strip() or None
    city   = input("  Город (Алматы/Астана/Шымкент/...): ").strip() or "Алматы"
    region = input("  Регион (необязательно): ").strip() or None
    phone  = input("  Телефон (необязательно): ").strip() or None
    wa     = input("  WhatsApp (необязательно): ").strip() or None
    vetted = input("  Проверен? (y/n): ").strip().lower() == "y"

    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO suppliers (name, short_name, bin, legal_form, city, region, phone, whatsapp, is_vetted)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, short or None, bin_, lf, city, region, phone, wa, int(vetted)),
        )
        conn.commit()
        sup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        print(f"\n  ✅ Поставщик добавлен: id={sup_id} — {name}")
    except sqlite3.IntegrityError as e:
        print(f"\n  ⚠ Ошибка (БИН уже существует?): {e}")
    finally:
        conn.close()


# ── ПСЕВДОНИМ ─────────────────────────────────────────────────────────────────

def add_alias():
    list_suppliers()
    sup_id = int(input("  ID поставщика для псевдонима: ").strip())
    alias  = input("  Псевдоним (как он появляется в накладных): ").strip()
    conn = get_conn()
    try:
        conn.execute("INSERT INTO supplier_aliases (supplier_id, alias) VALUES (?,?)", (sup_id, alias))
        conn.commit()
        print(f"  ✅ Псевдоним '{alias}' → id={sup_id} добавлен.")
    except sqlite3.IntegrityError:
        print(f"  ⚠ Псевдоним '{alias}' уже существует.")
    conn.close()


# ── ОДОБРИТЬ ──────────────────────────────────────────────────────────────────

def vet_supplier(sup_id: int):
    conn = get_conn()
    row = conn.execute("SELECT name FROM suppliers WHERE id=?", (sup_id,)).fetchone()
    if not row:
        print(f"Поставщик id={sup_id} не найден.")
        return
    conn.execute("UPDATE suppliers SET is_vetted=1 WHERE id=?", (sup_id,))
    conn.commit()
    conn.close()
    print(f"  ✅ Поставщик '{row[0]}' одобрен.")


# ── СЛИТЬ ДУБЛИ ───────────────────────────────────────────────────────────────

def merge_suppliers(from_id: int, into_id: int):
    """Move all data from from_id → into_id, then delete from_id."""
    conn = get_conn()
    from_row = conn.execute("SELECT name FROM suppliers WHERE id=?", (from_id,)).fetchone()
    into_row = conn.execute("SELECT name FROM suppliers WHERE id=?", (into_id,)).fetchone()
    if not from_row or not into_row:
        print("Один из поставщиков не найден.")
        return

    confirm = input(f"  Слить '{from_row[0]}' (id={from_id}) → '{into_row[0]}' (id={into_id})? [y/n]: ")
    if confirm.lower() != "y":
        print("  Отменено.")
        return

    c = conn.cursor()
    # Register the old name as alias so future invoices still resolve
    try:
        c.execute("INSERT INTO supplier_aliases (supplier_id, alias) VALUES (?,?)", (into_id, from_row[0]))
    except sqlite3.IntegrityError:
        pass

    # Re-point all related records
    for table, col in [("invoices", "supplier_id"), ("prices", "supplier_id"), ("anomalies", "supplier_id")]:
        c.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (into_id, from_id))

    c.execute("DELETE FROM suppliers WHERE id=?", (from_id,))
    conn.commit()
    conn.close()
    print(f"  ✅ Слито. '{from_row[0]}' теперь псевдоним '{into_row[0]}'.")


# ── ДИАГНОСТИКА дублей ────────────────────────────────────────────────────────

def find_duplicates():
    conn = get_conn()
    rows = conn.execute("SELECT id, name, bin FROM suppliers ORDER BY name").fetchall()
    conn.close()

    print("\n=== Проверка возможных дублей ===")
    found = []
    for i, (id1, n1, b1) in enumerate(rows):
        for id2, n2, b2 in rows[i+1:]:
            # Same BIN = definite duplicate
            if b1 and b2 and b1 == b2:
                found.append([id1, n1, id2, n2, "🔴 ОДИНАКОВЫЙ БИН"])
                continue
            # Similar name (strip legal form)
            core1 = re.sub(r'^(ТОО|ИП|АО|ООО|ЧП|КХ)\s*[\"«]?', '', n1 or '', flags=re.I).strip()[:15].lower()
            core2 = re.sub(r'^(ТОО|ИП|АО|ООО|ЧП|КХ)\s*[\"«]?', '', n2 or '', flags=re.I).strip()[:15].lower()
            if core1 and core2 and core1 == core2:
                found.append([id1, n1, id2, n2, "🟡 Похожее название"])

    if found:
        print(tabulate(found, headers=["ID1","Имя1","ID2","Имя2","Тип"], tablefmt="rounded_outline"))
        print(f"\n  Используйте: python manage_suppliers.py --merge <ID_дубль> <ID_основной>")
    else:
        print("  Дублей не обнаружено.\n")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="YouCook Supplier Management")
    parser.add_argument("--list",   action="store_true", help="Список всех поставщиков")
    parser.add_argument("--add",    action="store_true", help="Добавить поставщика вручную")
    parser.add_argument("--alias",  action="store_true", help="Добавить псевдоним")
    parser.add_argument("--vet",    type=int, metavar="ID", help="Одобрить поставщика")
    parser.add_argument("--show",   type=int, metavar="ID", help="Детали поставщика")
    parser.add_argument("--merge",  nargs=2, type=int, metavar=("FROM","INTO"), help="Слить дубли")
    parser.add_argument("--dupes",  action="store_true", help="Найти возможные дубли")
    args = parser.parse_args()

    if args.list:   list_suppliers()
    elif args.add:  add_supplier()
    elif args.alias: add_alias()
    elif args.vet:  vet_supplier(args.vet)
    elif args.show: show_supplier(args.show)
    elif args.merge: merge_suppliers(args.merge[0], args.merge[1])
    elif args.dupes: find_duplicates()
    else:
        list_suppliers()


if __name__ == "__main__":
    main()
