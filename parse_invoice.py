"""
parse_invoice.py — YouCook Procurement System
Parses 1С «Счет на оплату» PDF invoices → YouCookDashOG.db

Usage:
    python parse_invoice.py --batch                  # all PDFs in ./invoices/
    python parse_invoice.py invoices/59.pdf          # single file
    python parse_invoice.py --batch ./other_dir/     # custom directory

Table format (fixed column positions):
    [0]№  [1]КОД  [2]НАИМЕНОВАНИЕ  [3]КОЛ-ВО  [4]ЕД  [5]ЦЕНА  [6]СУММА
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pdfplumber

# Force UTF-8 output so Cyrillic and ₸ print correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH      = Path(__file__).parent / "data" / "YouCookDashOG.db"
INVOICES_DIR = Path(__file__).parent / "invoices"

# Russian month name → zero-padded number
RU_MONTHS = {
    "января": "01", "февраля": "02", "марта": "03",   "апреля": "04",
    "мая":    "05", "июня":    "06", "июля":  "07",   "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}


# ── Supplier name helpers ──────────────────────────────────────────────────────

def clean_supplier_name(name: str) -> str:
    """Сокращает полные юридические формы: 'Индивидуальный Предприниматель' → 'ИП' и т.д."""
    s = str(name or '').strip()
    s = re.sub(r'Индивидуальный\s+Предприниматель', 'ИП', s, flags=re.IGNORECASE)
    s = re.sub(r'Товарищество\s+с\s+[Оо]граниченной\s+[Оо]тветственностью', 'ТОО', s, flags=re.IGNORECASE)
    s = re.sub(r'Общество\s+с\s+[Оо]граниченной\s+[Оо]тветственностью', 'ООО', s, flags=re.IGNORECASE)
    s = re.sub(r'Акционерное\s+[Оо]бщество', 'АО', s, flags=re.IGNORECASE)
    return s


# ── Number helpers ─────────────────────────────────────────────────────────────

def to_float(s: str) -> float:
    """'1 849,97' → 1849.97   '1200.00' → 1200.0"""
    return float(re.sub(r"[\s\u00a0\u202f]", "", str(s)).replace(",", "."))


# ── Metadata extraction ────────────────────────────────────────────────────────

def extract_meta(lines: list[str]) -> dict:
    """
    Parse supplier BIN, name, invoice number, and date from 1С invoice text lines.

    Typical 1С structure:
      11 : Счет на оплату № 59 от 4 марта 2026 г.
      12 : Поставщик: БИН / ИИН 000614550126, ИП "МИЛАНА", 130000, Костанай, ...
      15 : Покупатель: БИН / ИИН 211040029330, ТОО "GKGM", ...
    """
    meta = {"supplier": None, "supplier_bin": None, "supplier_city": None,
            "date": None, "invoice_id": None}

    # Postal code → city fallback (Kazakhstan regions)
    KZ_POSTAL = {
        "010": "Астана",     "050": "Алматы",     "100": "Қарағанды",
        "110": "Жезқазған",  "120": "Балқаш",     "130": "Қостанай",
        "140": "Петропавл",  "150": "Павлодар",   "160": "Семей",
        "070": "Өскемен",    "080": "Шымкент",    "030": "Атырау",
        "040": "Ақтау",      "060": "Орал",       "090": "Қызылорда",
        "020": "Ақтөбе",
    }

    sup_line_idx = None
    for idx, line in enumerate(lines):
        # ── Invoice number + date ──────────────────────────────────────────
        m = re.search(r"№\s*(\d+)\s+от\s+(\d{1,2})\s+(\S+)\s+(\d{4})", line, re.IGNORECASE)
        if m and not meta["invoice_id"]:
            day, month_str, year = m.group(2).zfill(2), m.group(3).lower().rstrip("."), m.group(4)
            meta["invoice_id"] = m.group(1)
            month = RU_MONTHS.get(month_str)
            if month:
                meta["date"] = f"{year}-{month}-{day}"

        # ── Supplier: БИН + name ───────────────────────────────────────────
        if re.match(r"Поставщик:", line) and "," in line and not meta["supplier"]:
            bin_m = re.search(r"\b(\d{12})\b", line)
            if bin_m:
                meta["supplier_bin"] = bin_m.group(1)

            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                name = parts[1].strip()
                if name and not re.match(r"^\d", name) and len(name) > 3:
                    meta["supplier"] = clean_supplier_name(name)
                    sup_line_idx = idx

            # Try city from same line (parts[3])
            if len(parts) >= 4:
                city_candidate = parts[3].strip()
                if city_candidate and not re.match(r"^\d", city_candidate):
                    meta["supplier_city"] = city_candidate

            # Fallback: infer city from postal code prefix
            if not meta["supplier_city"] and meta["supplier_bin"]:
                postal_m = re.search(r"\b(\d{6})\b", line)
                if postal_m:
                    prefix = postal_m.group(1)[:3]
                    meta["supplier_city"] = KZ_POSTAL.get(prefix)

    # Try city from the line immediately after the Поставщик: line
    if not meta["supplier_city"] and sup_line_idx is not None:
        next_lines = lines[sup_line_idx + 1: sup_line_idx + 3]
        for nl in next_lines:
            city_m = re.search(
                r"(Алматы|Астана|Шымкент|Қарағанды|Актобе|Ақтөбе|Тараз|Павлодар|"
                r"Семей|Өскемен|Костанай|Қостанай|Петропавл|Кызылорда|Атырау|Актау|Орал)",
                nl, re.IGNORECASE
            )
            if city_m:
                meta["supplier_city"] = city_m.group(1)
                break

    return meta


# ── З-2 «Накладная на отпуск запасов» — PDF helpers ──────────────────────────

_Z2_FMT = re.compile(r'\d{1,3}(?:[\s\u00a0]\d{3})*,\d+')  # «9 000,00» — цена/сумма
_Z2_INT = re.compile(r'\d+(?:,\d+)?')                          # целые или «25,585» — кол-во
_Z2_ROW = re.compile(r'^(\d{1,3})\s+')                         # строка-позиция
_Z2_COD = re.compile(r'\b(\d{8,12})\b')                        # номенклатурный код


def _z2_meta(lines: list[str]) -> dict:
    meta = {"supplier": None, "supplier_bin": None, "supplier_city": None,
            "date": None, "invoice_id": None}

    for line in lines:
        # Поставщик и БИН: «... "МИЛАНА" ИИН/БИН 000614550126»
        if "ИИН/БИН" in line and not meta["supplier_bin"]:
            bin_m = re.search(r'ИИН/БИН\s+(\d{12})', line)
            if bin_m:
                meta["supplier_bin"] = bin_m.group(1)
            name_m = re.search(r'\)\s+(.+?)\s+ИИН/БИН', line)
            if name_m:
                name = name_m.group(1).strip()
                # «Индивидуальный предприниматель» → «ИП»
                name = re.sub(r'Индивидуальный предприниматель\s*', 'ИП ', name, flags=re.IGNORECASE).strip()
                meta["supplier"] = name

        # Номер и дата: строка вида «102 08.04.2026»
        if not meta["invoice_id"]:
            m = re.match(r'^(\d+)\s+(\d{2})\.(\d{2})\.(\d{4})$', line.strip())
            if m:
                meta["invoice_id"] = str(int(m.group(1)))
                meta["date"] = f"{m.group(4)}-{m.group(3)}-{m.group(2)}"

    return meta


def _z2_data(lines: list[str]) -> list[dict]:
    items = []
    for line in lines:
        m_row = _Z2_ROW.match(line)
        if not m_row:
            continue
        rest = line[m_row.end():]

        m_code = _Z2_COD.search(rest)
        if not m_code:
            continue

        name  = rest[:m_code.start()].strip()
        after = rest[m_code.end():].strip()
        if not name:
            continue

        # Единица измерения — первое слово после кода
        parts = after.split(None, 1)
        if not parts:
            continue
        unit     = parts[0]
        nums_str = parts[1] if len(parts) > 1 else ""

        # Числа с запятой-десятичной (цена, сумма, НДС) отделяем от целых (кол-во)
        # «30 30 970,00 29 100,00» → integers=[30,30], formatted=[970,29100,...]
        fmt_matches = list(_Z2_FMT.finditer(nums_str))
        if fmt_matches:
            before = nums_str[:fmt_matches[0].start()]
            qty_nums = [to_float(n) for n in _Z2_INT.findall(before)]
            fmt_nums = [to_float(m.group()) for m in fmt_matches]
            nums = qty_nums + fmt_nums
        else:
            nums = [to_float(n) for n in _Z2_INT.findall(nums_str)]

        if len(nums) < 3:
            continue

        # Найти тройку qty * price ≈ total
        qty = price = total = None
        for a in range(len(nums)):
            for b in range(len(nums)):
                if b == a: continue
                for c in range(len(nums)):
                    if c in (a, b): continue
                    if abs(nums[a] * nums[b] - nums[c]) < max(0.5, nums[c] * 0.005):
                        qty, price, total = nums[a], nums[b], nums[c]
                        if qty > price: qty, price = price, qty
                        break
                if qty is not None: break
            if qty is not None: break

        # Запасной вариант: [qty_заказ, qty_отпуск, цена, сумма, ндс]
        if qty is None and len(nums) >= 4:
            qty, price, total = nums[1], nums[2], nums[3]

        if not qty or price <= 0:
            continue

        items.append({"sku": name, "qty": qty, "unit": unit, "price": price, "total": total})

    return items


# ── PDF parser ─────────────────────────────────────────────────────────────────

def parse_pdf(pdf_path: Path) -> dict:
    result = {
        "pdf_filename":   pdf_path.name,
        "source":         "pdf",
        "supplier":       None,
        "supplier_bin":   None,
        "supplier_city":  None,
        "date":           None,
        "invoice_id":     None,
        "total":          0.0,
        "lines":          [],
    }

    with pdfplumber.open(pdf_path) as pdf:
        all_lines = []
        all_rows  = []

        for page in pdf.pages:
            text = page.extract_text() or ""
            all_lines.extend(text.splitlines())

            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if row and row[0] and str(row[0]).strip().isdigit():
                    all_rows.append(row)

        # Определяем формат: З-2 или 1С
        is_z2 = any("Форма З-2" in l or "НАКЛАДНАЯ НА ОТПУСК ЗАПАСОВ" in l for l in all_lines)

        if is_z2:
            meta = _z2_meta(all_lines)
            result.update({k: v for k, v in meta.items() if v})
            # Для З-2 данные берём из таблицы PDF — чистые колонки, без неоднозначности
            # Колонки: [0]№ [1]Наименование [2]Код [3]Ед [4]кол.заказ [5]кол.отпуск [6]Цена [7]Сумма [8]НДС
            for page in pdf.pages:
                tbl = page.extract_table()
                if not tbl:
                    continue
                for row in tbl:
                    if not row or not row[0] or not str(row[0]).strip().isdigit():
                        continue
                    try:
                        name = str(row[1]).strip() if row[1] else ""
                        if not name or name.isdigit():
                            continue

                        # Определяем формат таблицы по колонке [3]:
                        # Вариант А: [1]Наим [2]Код [3]Ед [4]кол.заказ [5]кол.отпуск [6]Цена [7]Сумма
                        # Вариант Б: [1]Наим [2]Ед  [3]Код [4]кол.заказ [5]кол.отпуск [6]кол.dup [7]Цена [8]Сумма
                        col3 = str(row[3]).strip() if len(row) > 3 and row[3] else ""
                        is_code = re.match(r'^\d{6,}$', col3.replace('\xa0', '').replace(' ', ''))

                        if is_code and len(row) > 8:
                            unit  = str(row[2]).strip() if row[2] else "кг"
                            qty   = to_float(row[5] or row[4])
                            price = to_float(row[7])
                            total = to_float(row[8])
                        else:
                            unit  = col3 or "кг"
                            qty   = to_float(row[5] or row[4])
                            price = to_float(row[6])
                            total = to_float(row[7])

                        if price <= 0 or qty <= 0:
                            continue
                        result["lines"].append({"sku": name, "qty": qty, "unit": unit,
                                                 "price": price, "total": total})
                        result["total"] += total
                    except (ValueError, TypeError, IndexError):
                        continue
            return result

        meta = extract_meta(all_lines)
        result.update({k: v for k, v in meta.items() if v})

        for row in all_rows:
            # Expected: [№, КОД, НАИМЕНОВАНИЕ, КОЛ-ВО, ЕД, ЦЕНА, СУММА]
            if len(row) < 6:
                continue
            try:
                sku_name = str(row[2]).strip()
                qty      = to_float(row[3])
                unit     = str(row[4]).strip() if row[4] else "кг"
                price    = to_float(row[5])

                if not sku_name or price <= 0:
                    continue

                line_total = price * qty
                result["lines"].append({
                    "sku":   sku_name,
                    "qty":   qty,
                    "unit":  unit,
                    "price": price,
                    "total": line_total,
                })
                result["total"] += line_total
            except (ValueError, TypeError, IndexError):
                continue

    return result


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_or_create_supplier(conn, name: str, bin_: str = None, city: str = None) -> int:
    """
    Look up supplier by BIN first (canonical), then by name/alias.
    Creates a new record only if truly not found.
    """
    # 1. Exact BIN match — most reliable
    if bin_:
        row = conn.execute("SELECT id FROM suppliers WHERE bin = ?", (bin_,)).fetchone()
        if row:
            # Update name if it changed (name drift)
            conn.execute("UPDATE suppliers SET name=? WHERE id=? AND name!=?", (name, row[0], name))
            conn.commit()
            return row[0]

    # 2. Exact name match
    row = conn.execute("SELECT id FROM suppliers WHERE name = ?", (name,)).fetchone()
    if row:
        if bin_:  # backfill BIN if we now know it
            conn.execute("UPDATE suppliers SET bin=? WHERE id=? AND bin IS NULL", (bin_, row[0]))
            conn.commit()
        return row[0]

    # 3. Alias table match (catches "ТОО Агромаркет" vs "Агромаркет ТОО")
    row = conn.execute("SELECT supplier_id FROM supplier_aliases WHERE alias = ?", (name,)).fetchone()
    if row:
        return row[0]

    # 4. Fuzzy: strip legal form and search core name
    core = re.sub(r'^(ТОО|ИП|АО|ООО|ЧП|КХ)\s*[\"«]?', '', name, flags=re.IGNORECASE).strip().strip('"«»')
    if core and len(core) > 3:
        row = conn.execute(
            "SELECT id FROM suppliers WHERE name LIKE ?", (f"%{core[:20]}%",)
        ).fetchone()
        if row:
            # Register this variant as an alias to prevent future duplicates
            try:
                conn.execute("INSERT INTO supplier_aliases (supplier_id, alias) VALUES (?,?)", (row[0], name))
                conn.commit()
                print(f"  [ALIAS] '{name}' → существующий поставщик id={row[0]}")
            except Exception:
                pass
            return row[0]

    # 5. New supplier
    legal_form = None
    lf_m = re.match(r'^(ТОО|ИП|АО|ООО|ЧП|КХ)\b', name, re.IGNORECASE)
    if lf_m:
        legal_form = lf_m.group(1).upper()

    conn.execute(
        "INSERT INTO suppliers (name, bin, legal_form, city, is_vetted) VALUES (?,?,?,?,0)",
        (name, bin_, legal_form, city),
    )
    conn.commit()
    sup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    print(f"  [НОВЫЙ ПОСТАВЩИК] '{name}' (БИН: {bin_ or '?'}, {city or '?'}) — id={sup_id}, не проверен.")
    return sup_id


def get_or_create_sku(conn, name: str, unit: str) -> int:
    row = conn.execute("SELECT id FROM sku_catalog WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute("INSERT INTO sku_catalog (name, unit) VALUES (?, ?)", (name, unit))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_market_min(conn, sku_id: int):
    row = conn.execute("SELECT MIN(price) FROM prices WHERE sku_id = ?", (sku_id,)).fetchone()
    return row[0] if row and row[0] else None


def is_duplicate(conn, supplier_id: int, date: str, total: float) -> bool:
    row = conn.execute(
        "SELECT 1 FROM invoices WHERE supplier_id=? AND invoice_date=? AND ABS(total_amount-?)< 0.5",
        (supplier_id, date, total),
    ).fetchone()
    return row is not None


# ── Ingest one invoice ─────────────────────────────────────────────────────────

def ingest(conn, data: dict) -> dict:
    summary = {"ingested": 0, "overpriced": [], "duplicate": False}

    if not data["supplier"]:
        data["supplier"] = input(
            f"  Поставщик не найден в {data['pdf_filename']}. Введите вручную: "
        ).strip()

    supplier_id = get_or_create_supplier(
        conn, data["supplier"],
        bin_=data.get("supplier_bin"),
        city=data.get("supplier_city"),
    )

    if data["date"] and is_duplicate(conn, supplier_id, data["date"], data["total"]):
        conn.execute(
            "INSERT INTO anomalies (anomaly_type, supplier_id, detail, severity) VALUES (?,?,?,?)",
            ("duplicate", supplier_id, f"PDF: {data['pdf_filename']}", "high"),
        )
        conn.commit()
        summary["duplicate"] = True
        return summary

    # Invoice header
    conn.execute(
        """INSERT OR IGNORE INTO invoices
           (invoice_id, supplier_id, invoice_date, total_amount, pdf_filename, source, is_processed)
           VALUES (?,?,?,?,?,?,1)""",
        (data["invoice_id"], supplier_id, data["date"],
         data["total"], data["pdf_filename"], data.get("source", "pdf")),
    )
    conn.commit()
    inv_db_id = conn.execute(
        "SELECT id FROM invoices WHERE pdf_filename=?", (data["pdf_filename"],)
    ).fetchone()[0]

    for line in data["lines"]:
        sku_id     = get_or_create_sku(conn, line["sku"], line["unit"])
        market_min = get_market_min(conn, sku_id)
        is_ovr     = 0
        ovr_pct    = 0.0

        if market_min and line["price"] > market_min * 1.10:
            is_ovr  = 1
            ovr_pct = (line["price"] - market_min) / market_min * 100
            summary["overpriced"].append({
                "sku":       line["sku"],
                "price":     line["price"],
                "min":       market_min,
                "ovr_pct":   round(ovr_pct, 1),
            })
            conn.execute(
                """INSERT INTO anomalies
                   (anomaly_type, sku_id, supplier_id, invoice_id, detail, severity)
                   VALUES (?,?,?,?,?,?)""",
                ("overpriced", sku_id, supplier_id, inv_db_id,
                 f"{line['sku']}: {line['price']:.0f}₸ vs min {market_min:.0f}₸ (+{ovr_pct:.1f}%)",
                 "high" if ovr_pct > 20 else "medium"),
            )

        conn.execute(
            """INSERT INTO invoice_lines
               (invoice_id, sku_id, sku_raw, unit, qty, unit_price, line_total,
                is_overpriced, overprice_pct)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (inv_db_id, sku_id, line["sku"], line["unit"], line["qty"],
             line["price"], line["total"], is_ovr, ovr_pct),
        )
        conn.execute(
            """INSERT INTO prices
               (sku_id, supplier_id, price, unit, date, invoice_id, is_overpriced)
               VALUES (?,?,?,?,?,?,?)""",
            (sku_id, supplier_id, line["price"], line["unit"],
             data["date"] or datetime.now().strftime("%Y-%m-%d"),
             inv_db_id, is_ovr),
        )
        summary["ingested"] += 1

    conn.commit()
    return summary


# ── XLSX parser (З-2 «Накладная на отпуск запасов») ───────────────────────────

def parse_xlsx(xlsx_path: Path) -> dict:
    import openpyxl

    result = {
        "pdf_filename": xlsx_path.name,
        "source":       "xlsx",
        "supplier":     None,
        "supplier_bin": None,
        "supplier_city": None,
        "date":         None,
        "invoice_id":   None,
        "total":        0.0,
        "lines":        [],
    }

    wb   = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws   = wb.active
    rows = [list(row) for row in ws.iter_rows(values_only=True)]
    hdr  = rows[:35]

    # ── BIN (12-digit number in header) ──────────────────────────────────────
    for row in hdr:
        for cell in row:
            if cell and re.match(r"^\d{12}$", str(cell).strip()):
                result["supplier_bin"] = str(cell).strip()
                break
        if result["supplier_bin"]:
            break

    # ── Supplier name (ИП / ТОО / АО ...) ────────────────────────────────────
    for row in hdr:
        for cell in row:
            if cell:
                s = str(cell).strip()
                if re.match(r"^(ТОО|ИП|АО|ООО|ЧП)\b", s, re.IGNORECASE) and 5 < len(s) < 150:
                    result["supplier"] = s
                    break
        if result["supplier"]:
            break

    # ── Invoice number and date ───────────────────────────────────────────────
    for row in hdr:
        flat = " ".join(str(c) for c in row if c)
        if not result["invoice_id"]:
            m = re.search(r"№\s*(\d+)", flat)
            if m:
                result["invoice_id"] = str(int(m.group(1)))  # strip leading zeros
        if not result["date"]:
            m = re.search(r"от\s+(\d{1,2})[./](\d{1,2})[./](\d{4})", flat)
            if m:
                result["date"] = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        if not result["date"]:
            pat = "(" + "|".join(RU_MONTHS) + ")"
            m = re.search(r"(\d{1,2})\s+" + pat + r"\s+(\d{4})", flat, re.IGNORECASE)
            if m:
                mo = RU_MONTHS.get(m.group(2).lower())
                if mo:
                    result["date"] = f"{m.group(3)}-{mo}-{m.group(1).zfill(2)}"

    # ── Data rows (first cell = sequential row number 1,2,3…) ────────────────
    for row in rows:
        if not row or not row[0]:
            continue
        if not re.match(r"^\d{1,3}$", str(row[0]).strip()):
            continue

        # Gather non-empty indexed values (skip col 0)
        cells = [(i, row[i]) for i in range(1, len(row)) if row[i] is not None and str(row[i]).strip()]
        if len(cells) < 3:
            continue

        # Separate strings from numbers
        strings, numbers = [], []
        for i, v in cells:
            s = str(v).strip()
            try:
                n = to_float(s)
                numbers.append(n)
            except (ValueError, TypeError):
                if len(s) > 2:
                    strings.append(s)

        if not strings or len(numbers) < 2:
            continue

        sku_name = strings[0]
        unit = next((s for s in strings[1:] if len(s) <= 5 and s.replace(".", "").isalpha()), "кг")
        nums = [n for n in numbers if n > 0]

        # Find triplet qty * price ≈ total
        qty = price = line_total = None
        for a, b, c in [(nums[i], nums[j], nums[k])
                        for i in range(len(nums))
                        for j in range(len(nums)) if j != i
                        for k in range(len(nums)) if k != i and k != j]:
            if abs(a * b - c) < max(0.5, c * 0.002):
                qty, price, line_total = (a, b, c) if a <= b else (b, a, c)
                break

        if qty is None and len(nums) >= 2:
            qty, price = nums[0], nums[1]
            line_total = qty * price

        if not qty or not price or price <= 0:
            continue

        result["lines"].append({"sku": sku_name, "qty": qty, "unit": unit,
                                 "price": price, "total": line_total})
        result["total"] += line_total

    return result


# ── Process one file ───────────────────────────────────────────────────────────

def process_file(pdf_path: Path, conn) -> dict:
    print(f"\n[PARSING] {pdf_path.name}")
    ext = pdf_path.suffix.lower()
    if ext == ".pdf":
        data = parse_pdf(pdf_path)
    elif ext in (".xlsx", ".xls"):
        data = parse_xlsx(pdf_path)
    else:
        print(f"  Пропуск — неподдерживаемый формат: {ext}")
        return {"ingested": 0, "overpriced": [], "duplicate": False}
    summary = ingest(conn, data)

    status = "ДУБЛЬ — пропущен" if summary["duplicate"] else f"{summary['ingested']} строк загружено"
    print(f"  Поставщик : {data['supplier']}")
    print(f"  Накладная : №{data['invoice_id']}  Дата: {data['date']}")
    print(f"  Позиций   : {len(data['lines'])}  Сумма: {data['total']:,.0f} ₸")
    print(f"  Статус    : {status}")

    if summary["overpriced"]:
        print(f"  ⚠ ПЕРЕПЛАТЫ ({len(summary['overpriced'])} позиций):")
        for item in summary["overpriced"]:
            print(f"    {item['sku']}: {item['price']:,.0f}₸  vs  min {item['min']:,.0f}₸  (+{item['ovr_pct']}%)")

    return summary


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Загрузка PDF накладных в YouCookDashOG.db")
    parser.add_argument("path",    nargs="?", default=None, help="PDF файл или папка")
    parser.add_argument("--batch", action="store_true",    help="Обработать все PDF в ./invoices/")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    if args.batch or args.path is None:
        scan_dir = Path(args.path) if args.path else INVOICES_DIR
        pdfs = sorted(scan_dir.glob("*.pdf")) + sorted(scan_dir.glob("*.xlsx"))
        if not pdfs:
            print(f"PDF/XLSX файлов не найдено в {scan_dir}")
            conn.close()
            return
        print(f"Найдено {len(pdfs)} файлов в {scan_dir}")
        totals = {"ingested": 0, "overpriced": 0, "duplicate": 0}
        for pdf in pdfs:
            s = process_file(pdf, conn)
            totals["ingested"]  += s["ingested"]
            totals["overpriced"] += len(s["overpriced"])
            totals["duplicate"]  += int(s["duplicate"])
        print(f"\n{'═'*50}")
        print(f"  ИТОГО: {len(pdfs)} накладных")
        print(f"  Строк загружено : {totals['ingested']}")
        print(f"  Переплат        : {totals['overpriced']}")
        print(f"  Дублей пропущено: {totals['duplicate']}")
        print(f"{'═'*50}")
    else:
        process_file(Path(args.path), conn)

    conn.close()


if __name__ == "__main__":
    main()
