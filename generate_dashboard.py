"""
generate_dashboard.py — Генерирует docs/index.html из текущих данных YouCookDashOG.db
Запуск: python generate_dashboard.py
        (или через push_dashboard.bat — сразу генерирует и пушит на GitHub Pages)
"""
import re
import sqlite3
import sys
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict, OrderedDict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
DB   = BASE / "data" / "YouCookDashOG.db"
TPL  = BASE / "dashboard.html"
OUT  = BASE / "docs" / "index.html"
OUT.parent.mkdir(exist_ok=True)

# ── Вспомогательные функции ────────────────────────────────────────────────────
def js_str(s):
    return '"' + (
        str(s)
        .replace('\\', '\\\\')
        .replace('"', '\\"')
        .replace('\n', '\\n')
        .replace('\r', '\\r')
        .replace('\t', '\\t')
    ) + '"'

def fmt_date(d):
    """'2026-03-04' → '04.03.2026'"""
    try:
        y, m, day = d.split("-")
        return f"{day}.{m}.{y}"
    except Exception:
        return d or "—"

def fmt_date_short(d):
    """'2026-03-04' → '04.03'"""
    try:
        y, m, day = d.split("-")
        return f"{day}.{m}"
    except Exception:
        return d or "—"

def clean_sku(name):
    """Синтетические OCR-агрегаты [OCR-скан scanN] → одна общая группа."""
    if not name:
        return name
    if str(name).startswith('[OCR-скан'):
        return '[Скан без позиций]'
    return name

def clean_sup(name):
    """Сокращает полные юридические формы до аббревиатур."""
    import re as _re
    s = str(name or '').strip()
    s = _re.sub(r'Индивидуальный\s+Предприниматель', 'ИП', s, flags=_re.IGNORECASE)
    s = _re.sub(r'Товарищество\s+с\s+[Оо]граниченной\s+[Оо]тветственностью', 'ТОО', s, flags=_re.IGNORECASE)
    s = _re.sub(r'Общество\s+с\s+[Оо]граниченной\s+[Оо]тветственностью', 'ООО', s, flags=_re.IGNORECASE)
    s = _re.sub(r'Акционерное\s+[Оо]бщество', 'АО', s, flags=_re.IGNORECASE)
    return s

def shorten(name, maxlen=22):
    return name if len(name) <= maxlen else name[:maxlen-1] + "…"

# ── Категории ──────────────────────────────────────────────────────────────────
CATEGORY_MAP = [
    ('яйцо',            'Яйца'),
    ('сливки',          'Молочные'),
    ('творог',          'Молочные'),
    ('молоко',          'Молочные'),
    ('сыр',             'Молочные'),
    ('сливочное масло', 'Молочные'),
    ('масло сливочное', 'Молочные'),
    ('масло подсолн',   'Масла'),
    ('масло фритюр',    'Масла'),
    ('масло кунжут',    'Масла'),
    ('рис',             'Крупы'),
    ('мука',            'Крупы'),
    ('гречка',          'Крупы'),
    ('геркулес',        'Крупы'),
    ('манная',          'Крупы'),
    ('чечевица',        'Крупы'),
    ('нут',             'Крупы'),
    ('фасоль',          'Крупы'),
    ('лапша',           'Крупы'),
    ('макарон',         'Крупы'),
    ('грудка',          'Мясо'),
    ('колбаса',         'Мясо'),
    ('креветки',        'Мясо'),
    ('крабовые',        'Мясо'),
    ('копченн',         'Мясо'),
    ('картофель',       'Овощи'),
    ('лук',             'Овощи'),
    ('помидор',         'Овощи'),
    ('огурц',           'Овощи'),
    ('морковь',         'Овощи'),
    ('капуста',         'Овощи'),
    ('томат',           'Овощи'),
    ('петрушка',        'Овощи'),
    ('перец свет',      'Овощи'),
    ('перец п/г',       'Овощи'),
    ('перец',           'Специи'),
    ('майонез',         'Специи'),
    ('соус',            'Специи'),
    ('приправа',        'Специи'),
    ('уксус',           'Специи'),
    ('дошида',          'Специи'),
    ('куркума',         'Специи'),
    ('кунжут',          'Специи'),
    ('соль',            'Специи'),
    ('мед',             'Специи'),
    ('изюм',            'Специи'),
    ('нават',           'Специи'),
    ('горох',           'Специи'),
    ('финики',          'Специи'),
    ('тостер',          'Хлеб'),
    ('хлеб',            'Хлеб'),
    ('разрыхлитель',    'Хлеб'),
    ('тряпка',          'Хозтовары'),
    ('губка',           'Хозтовары'),
    ('антижир',         'Хозтовары'),
    ('ценник',          'Хозтовары'),
    ('ведро',           'Хозтовары'),
    ('зажигалка',       'Хозтовары'),
    ('марля',           'Хозтовары'),
    ('кассовый',        'Хозтовары'),
]

def get_category(name):
    n = name.lower()
    for key, cat in CATEGORY_MAP:
        if key in n:
            return cat
    return 'Прочее'

# ── Запросы ────────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB)

total = conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM invoices").fetchone()[0]
n_inv = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

suppliers = conn.execute("""
    SELECT COALESCE(s.short_name,s.name), s.city,
           COALESCE(SUM(i.total_amount),0), COUNT(i.id)
    FROM suppliers s LEFT JOIN invoices i ON i.supplier_id=s.id
    GROUP BY s.id ORDER BY 3 DESC
""").fetchall()

cities = conn.execute("""
    SELECT COALESCE(s.city,'?'), COALESCE(SUM(i.total_amount),0)
    FROM suppliers s LEFT JOIN invoices i ON i.supplier_id=s.id
    GROUP BY s.city ORDER BY 2 DESC
""").fetchall()

invoices = conn.execute("""
    SELECT i.invoice_id, COALESCE(s.short_name,s.name), s.city,
           i.invoice_date, i.total_amount, COUNT(il.id)
    FROM invoices i
    JOIN suppliers s ON s.id=i.supplier_id
    LEFT JOIN invoice_lines il ON il.invoice_id=i.id
    GROUP BY i.id ORDER BY i.invoice_date
""").fetchall()

top_sku = conn.execute("""
    SELECT sc.name, SUM(il.line_total), COUNT(DISTINCT i.supplier_id)
    FROM invoice_lines il
    JOIN invoices i ON i.id=il.invoice_id
    JOIN sku_catalog sc ON sc.id=il.sku_id
    GROUP BY il.sku_id ORDER BY 2 DESC LIMIT 15
""").fetchall()

anomalies = conn.execute("""
    SELECT sc.name,
           MIN(p.unit), MIN(p.price), MAX(p.price),
           ROUND((MAX(p.price)-MIN(p.price))/MIN(p.price)*100,1),
           COUNT(DISTINCT p.supplier_id)
    FROM prices p
    JOIN sku_catalog sc ON sc.id=p.sku_id
    JOIN suppliers s ON s.id=p.supplier_id
    GROUP BY p.sku_id, s.city
    HAVING (MAX(p.price)/MIN(p.price)) > 1.10
    ORDER BY 5 DESC LIMIT 8
""").fetchall()

suspicious = conn.execute("""
    SELECT sc.name, il.unit, il.unit_price, il.line_total,
           COALESCE(s.short_name,s.name), s.city
    FROM invoice_lines il
    JOIN sku_catalog sc ON sc.id=il.sku_id
    JOIN invoices i ON i.id=il.invoice_id
    JOIN suppliers s ON s.id=i.supplier_id
    ORDER BY il.unit_price DESC LIMIT 10
""").fetchall()

# ── Топ SKU по частоте (в скольких накладных встречается) ──────────────────────
freq_sku_raw = conn.execute("""
    SELECT sc.name, COUNT(DISTINCT i.id) as inv_count,
           ROUND(AVG(il.unit_price), 0) as avg_price
    FROM invoice_lines il
    JOIN invoices i ON i.id = il.invoice_id
    JOIN sku_catalog sc ON sc.id = il.sku_id
    GROUP BY il.sku_id
    ORDER BY inv_count DESC, avg_price DESC
    LIMIT 15
""").fetchall()

# ── Категории: суммы по категориям ────────────────────────────────────────────
all_lines = conn.execute("""
    SELECT sc.name, COALESCE(il.line_total, 0)
    FROM invoice_lines il
    JOIN sku_catalog sc ON sc.id=il.sku_id
""").fetchall()

cat_totals = defaultdict(float)
for name, amt in all_lines:
    cat_totals[get_category(name)] += (amt or 0)
cat_spend = sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)

detail_lines_raw = conn.execute("""
    SELECT sc.name, COALESCE(s.short_name, s.name), s.city,
           i.invoice_date, il.unit_price, il.qty, COALESCE(il.line_total, 0),
           i.invoice_id, COALESCE(il.unit, sc.unit, 'кг')
    FROM invoice_lines il
    JOIN invoices i ON i.id = il.invoice_id
    JOIN suppliers s ON s.id = i.supplier_id
    JOIN sku_catalog sc ON sc.id = il.sku_id
    ORDER BY i.invoice_date
""").fetchall()

# ── Недельные расходы ──────────────────────────────────────────────────────────
weekly_raw = conn.execute("""
    SELECT
        strftime('%Y-%W', invoice_date) as yw,
        MIN(invoice_date) as wstart,
        COALESCE(s.short_name, s.name) as sup_name,
        SUM(i.total_amount) as wamt
    FROM invoices i
    JOIN suppliers s ON s.id=i.supplier_id
    GROUP BY yw, s.id
    ORDER BY yw
""").fetchall()

weeks = OrderedDict()
for yw, wstart, sup_name, wamt in weekly_raw:
    if yw not in weeks:
        weeks[yw] = {'start': wstart, 'milana': 0, 'osmanov': 0}
    if sup_name and 'МИЛАН' in sup_name.upper():
        weeks[yw]['milana'] += wamt
    else:
        weeks[yw]['osmanov'] += wamt

# ── Тренды цен (летучие SKU) ───────────────────────────────────────────────────
volatile_skus = conn.execute("""
    SELECT DISTINCT p.sku_id, sc.name,
           ROUND((MAX(p.price)-MIN(p.price))/MIN(p.price)*100,1) as spread
    FROM prices p
    JOIN sku_catalog sc ON sc.id=p.sku_id
    GROUP BY p.sku_id
    HAVING COUNT(DISTINCT p.date) > 1 AND spread > 5
    ORDER BY spread DESC
    LIMIT 5
""").fetchall()

# Все даты по этим SKU
trend_date_set = set()
for sku_id, _, _ in volatile_skus:
    rows = conn.execute("SELECT DISTINCT date FROM prices WHERE sku_id=?", (sku_id,)).fetchall()
    for (d,) in rows:
        trend_date_set.add(d)
trend_dates = sorted(trend_date_set)

price_trends = []
for sku_id, sku_name, spread in volatile_skus:
    price_map = {}
    rows = conn.execute("""
        SELECT date, AVG(price)
        FROM prices WHERE sku_id=?
        GROUP BY date ORDER BY date
    """, (sku_id,)).fetchall()
    for d, p in rows:
        price_map[d] = round(p, 2)
    data = [price_map.get(d, None) for d in trend_dates]
    non_null = [x for x in data if x is not None]
    if len(non_null) >= 2:
        price_trends.append((sku_name, data, spread))

# ── Сравнение поставщиков (общие SKU) ─────────────────────────────────────────
cross_raw = conn.execute("""
    SELECT sc.name, sc.id,
           p.supplier_id, COALESCE(s.short_name, s.name), MIN(p.price)
    FROM prices p
    JOIN sku_catalog sc ON sc.id=p.sku_id
    JOIN suppliers s ON s.id=p.supplier_id
    WHERE EXISTS (
        SELECT 1 FROM prices p2
        JOIN suppliers s2 ON s2.id=p2.supplier_id
        WHERE p2.sku_id=p.sku_id
          AND s2.city=s.city
          AND p2.supplier_id != p.supplier_id
    )
    GROUP BY p.sku_id, p.supplier_id
    ORDER BY sc.name
""").fetchall()

# Группировка: {sku_name: {sup_name: price}}
from collections import defaultdict as dd2
cross_by_sku = dd2(dict)
for sku_name, sku_id, sup_id, sup_name, price in cross_raw:
    cross_by_sku[sku_name][sup_name] = price

# ── Детали аномалий (по накладным) ────────────────────────────────────────────
anomaly_detail_raw = conn.execute("""
    SELECT sc.name, i.invoice_id, i.invoice_date,
           COALESCE(s.short_name, s.name), il.unit_price, il.qty, COALESCE(il.line_total, 0)
    FROM invoice_lines il
    JOIN sku_catalog sc ON sc.id = il.sku_id
    JOIN invoices i ON i.id = il.invoice_id
    JOIN suppliers s ON s.id = i.supplier_id
    WHERE sc.id IN (
        SELECT p.sku_id FROM prices p
        JOIN suppliers s ON s.id=p.supplier_id
        GROUP BY p.sku_id, s.city
        HAVING (MAX(p.price)/MIN(p.price)) > 1.10
    )
    GROUP BY sc.id, i.id
    ORDER BY sc.name, i.invoice_date
""").fetchall()

anom_detail = defaultdict(list)
for sku_name, inv_id, inv_date, sup_name, price, qty, line_ttl in anomaly_detail_raw:
    anom_detail[sku_name].append({
        'inv': inv_id, 'date': fmt_date(inv_date),
        'sup': clean_sup(sup_name), 'price': price,
        'qty': qty or 0, 'total': int(line_ttl or 0)
    })

conn.close()

# ── Расчёт метрик ──────────────────────────────────────────────────────────────
dates = [r[3] for r in invoices if r[3]]
period_from = fmt_date(min(dates)) if dates else "—"
period_to   = fmt_date(max(dates)) if dates else "—"
n_days = 1
if len(dates) >= 2:
    d1 = date.fromisoformat(min(dates))
    d2 = date.fromisoformat(max(dates))
    n_days = max(1, (d2 - d1).days)
weekly_avg  = int(total / max(1, n_days / 7))
avg_invoice = int(total / n_inv) if n_inv else 0

sup1_name  = clean_sup(suppliers[0][0]) if suppliers else "—"
sup1_spend = int(suppliers[0][2]) if suppliers else 0
sup2_name  = clean_sup(suppliers[1][0]) if len(suppliers) > 1 else "—"
sup2_spend = int(suppliers[1][2]) if len(suppliers) > 1 else 0
sup1_pct   = round(sup1_spend / total * 100, 1) if total else 0
sup2_pct   = round(100 - sup1_pct, 1)
sup1_inv   = suppliers[0][3] if suppliers else 0
sup2_inv   = suppliers[1][3] if len(suppliers) > 1 else 0

city1_name  = cities[0][0] if cities else "—"
city1_spend = int(cities[0][1]) if cities else 0
city2_name  = cities[1][0] if len(cities) > 1 else "—"
city2_spend = int(cities[1][1]) if len(cities) > 1 else 0
city1_pct   = round(city1_spend / total * 100, 1) if total else 0
city2_pct   = round(100 - city1_pct, 1)

top_cat     = cat_spend[0][0] if cat_spend else "—"
top_cat_amt = int(cat_spend[0][1]) if cat_spend else 0
top_cat_pct = round(top_cat_amt / total * 100, 1) if total else 0

anom_top3 = " · ".join(
    f"{shorten(a[0],12)} +{a[4]}%" for a in anomalies[:3]
) if anomalies else "не обнаружено"
critical_count = len([a for a in anomalies if a[4] > 50])

# ── JS arrays ──────────────────────────────────────────────────────────────────
def js_invoices():
    rows = []
    for inv_id, sup, city, dt, amt, lines in invoices:
        rows.append(
            f'  [{js_str(inv_id)}, {js_str(clean_sup(sup))}, {js_str(city or "—")}, '
            f'{js_str(fmt_date(dt))}, {int(amt)}, {int(lines)}, {js_str(dt or "")}]'
        )
    return "const invoices = [\n" + ",\n".join(rows) + "\n];"

def js_top_sku():
    rows = []
    for name, spend, n_sup in top_sku:
        cat = get_category(name)
        rows.append(f'  [{js_str(shorten(name))}, {int(spend)}, {int(n_sup)}, {js_str(cat)}]')
    return "const topSku = [\n" + ",\n".join(rows) + "\n];"

def js_anomalies():
    rows = []
    for name, unit, mn, mx, spread, n_sup in anomalies:
        rows.append(
            f'  [{js_str(shorten(name))}, {js_str(unit or "шт")}, '
            f'{mn}, {mx}, {spread}, {n_sup}]'
        )
    return "const anomalies = [\n" + ",\n".join(rows) + "\n];"

def js_suspicious():
    rows = []
    for name, unit, price, ttl, sup, city in suspicious:
        rows.append(
            f'  [{js_str(shorten(name))}, {js_str(unit or "—")}, '
            f'{price}, {int(ttl)}, {js_str(clean_sup(sup))}, {js_str(city or "—")}]'
        )
    return "const suspicious = [\n" + ",\n".join(rows) + "\n];"

def js_cat_spend():
    rows = []
    for cat, amt in cat_spend:
        pct = round(amt / total * 100, 1) if total else 0
        rows.append(f'  [{js_str(cat)}, {int(amt)}, {pct}]')
    return "const catSpend = [\n" + ",\n".join(rows) + "\n];"

def js_weekly_spend():
    rows = []
    for yw, data in weeks.items():
        label = fmt_date_short(data['start'])
        rows.append(f'  [{js_str(label)}, {int(data["milana"])}, {int(data["osmanov"])}]')
    return "const weeklySpend = [\n" + ",\n".join(rows) + "\n];"

def js_price_trends():
    date_labels_js = "[" + ", ".join(f'"{fmt_date_short(d)}"' for d in trend_dates) + "]"
    result = f"const trendDates = {date_labels_js};\n"
    trend_rows = []
    for sku_name, data, spread in price_trends:
        data_str = "[" + ", ".join("null" if v is None else str(v) for v in data) + "]"
        trend_rows.append(f'  [{js_str(shorten(sku_name, 24))}, {data_str}, {spread}]')
    result += "const priceTrends = [\n" + ",\n".join(trend_rows) + "\n];"
    return result

def js_anomaly_detail():
    rows = []
    for sku_name, occs in anom_detail.items():
        occ_js = []
        for o in occs:
            occ_js.append(
                f'{{{js_str("inv")}:{js_str(o["inv"])},'
                f'{js_str("date")}:{js_str(o["date"])},'
                f'{js_str("sup")}:{js_str(o["sup"])},'
                f'{js_str("price")}:{o["price"]},'
                f'{js_str("qty")}:{o["qty"]},'
                f'{js_str("total")}:{o["total"]}}}'
            )
        rows.append(f'  {js_str(sku_name)}: [{", ".join(occ_js)}]')
    return "const anomalyDetail = {\n" + ",\n".join(rows) + "\n};"

def js_freq_sku():
    rows = []
    for name, cnt, avg_p in freq_sku_raw:
        name = clean_sku(name)
        cat = get_category(name)
        rows.append(f'  [{js_str(name)}, {int(cnt)}, {int(avg_p or 0)}, {js_str(cat)}]')
    return "const freqSku = [\n" + ",\n".join(rows) + "\n];"

def js_cross_supplier():
    rows = []
    for sku_name, prices_by_sup in cross_by_sku.items():
        if len(prices_by_sup) >= 2:
            items = list(prices_by_sup.items())
            sup1n, p1 = items[0]
            sup2n, p2 = items[1]
            cheaper = sup1n if p1 <= p2 else sup2n
            diff_pct = round(abs(p2 - p1) / min(p1, p2) * 100, 1)
            rows.append(
                f'  [{js_str(shorten(sku_name))}, {js_str(clean_sup(sup1n))}, {p1}, '
                f'{js_str(clean_sup(sup2n))}, {p2}, {diff_pct}, {js_str(clean_sup(cheaper))}]'
            )
    return "const crossSupplier = [\n" + ",\n".join(rows) + "\n];"

def js_detail_lines():
    rows = []
    for sku, sup, city, dt, price, qty, total, inv_id, unit in detail_lines_raw:
        rows.append(
            f'  [{js_str(clean_sku(sku))},{js_str(clean_sup(sup))},{js_str(city or "—")},'
            f'{js_str(dt or "")},{float(price or 0):.2f},{float(qty or 0):.3f},'
            f'{int(total or 0)},{js_str(str(inv_id or ""))},{js_str(unit or "кг")}]'
        )
    return "const ALL_LINES=[\n" + ",\n".join(rows) + "\n];"

new_data = f"""// ── DATA ──────────────────────────────────────────────────────────────────────
const TOTAL = {int(total)};
const MILANA_COLOR   = '#2dd4a0';
const OSMANOV_COLOR  = '#f4a942';
const SALMURZ_COLOR  = '#c084fc';
const GRID      = 'rgba(255,255,255,0.04)';
const TICK      = '#6b7999';
const FONT      = {{ family: "'Segoe UI', system-ui, sans-serif", size: 11 }};
const TOOLTIP_BG = '#1d2540';

{js_invoices()}

{js_top_sku()}

{js_anomalies()}

{js_suspicious()}

{js_cat_spend()}

{js_weekly_spend()}

{js_price_trends()}

{js_freq_sku()}

{js_cross_supplier()}

{js_anomaly_detail()}

{js_detail_lines()}

"""

# ── Читаем шаблон и заменяем data-блок ────────────────────────────────────────
html = TPL.read_text(encoding="utf-8")

html = re.sub(
    r"// ── DATA ──.*?(?=// ── HELPERS ──)",
    lambda _m: new_data,
    html,
    flags=re.DOTALL,
)

# ── Обновляем статику в HTML ───────────────────────────────────────────────────
def r(pattern, replacement, text):
    return re.sub(pattern, replacement, text, count=1)

# Header — период убран из шаблона

# KPI 1 — расходы
html = r(r'(💰 Общие расходы</div>\s*<div class="card-value">)[^<]+',
         f'\\g<1>{int(total):,} ₸'.replace(',', '\u00a0'), html)
html = r(r'(за )\d+( дней · )\d+( накладных)',
         f'\\g<1>{n_days}\\g<2>{n_inv}\\g<3>', html)
html = r(r'\d[\d\s]+ ₸/нед среднее',
         f'{weekly_avg:,} ₸/нед среднее'.replace(',', '\u00a0'), html)

# KPI 2 — поставщики
html = r(r'(🏢 Поставщики</div>.*?card-sub">)[^<]+',
         f'\\g<1>{sup1_name} {sup1_spend:,} ₸ · {sup2_name} {sup2_spend:,} ₸'.replace(',', '\u00a0'), html)
html = r(r'(delta-green">)[^<]+(</div>\s*</div>\s*<div class="card">\s*<div class="card-title">🗺️)',
         f'\\g<1>{sup1_name} {sup1_inv} накл · {sup2_name} {sup2_inv} накл\\g<2>', html)

# KPI 3 — города
html = r(r'(Ақтау )\d[\d\s]+( ₸ · Алматы )\d[\d\s]+( ₸</div>)',
         f'\\g<1>{city1_spend:,}\\g<2>{city2_spend:,}\\g<3>'.replace(',', '\u00a0'), html)
html = r(r'Ақтау \d+\.?\d*% · Алматы \d+\.?\d*%',
         f'Ақтау {city1_pct}% · {city2_name} {city2_pct}%', html)

# KPI 4 — аномалии
html = r(r'(<div class="card-value" style="color:var\(--red\)">)\d+',
         f'\\g<1>{len(anomalies)}', html)
html = r(r'(⚠️ Ценовые аномалии</div>.*?card-sub">)[^<]+',
         f'\\g<1>{anom_top3}', html)
html = r(r'(\d+ критичн)[^<]+',
         f'\\g<1>{"ая" if critical_count==1 else "ых"} · требуют проверки', html)

# KPI 5 — средняя накладная
html = r(r'(📊 Средняя накладная</div>\s*<div class="card-value">)[^<]+',
         f'\\g<1>{avg_invoice:,} ₸'.replace(',', '\u00a0'), html)
html = r(r'(топ категория.*?card-sub">)[^<]+',
         f'\\g<1>топ: {top_cat} · {top_cat_pct}%', html)

# Supplier split bar
html = r(r'(width:)\d+\.?\d*(%.*?background:var\(--milana\))',
         f'\\g<1>{sup1_pct}\\g<2>', html)
html = r(r'(width:)\d+\.?\d*(%.*?background:var\(--osmanov\))',
         f'\\g<1>{sup2_pct}\\g<2>', html)

OUT.write_text(html, encoding="utf-8")

updated = datetime.now().strftime("%d.%m.%Y %H:%M")
print(f"✅ docs/index.html обновлён [{updated}]")
print(f"   Итого: {int(total):,} ₸  |  {n_inv} накладных  |  {len(suppliers)} поставщика".replace(',', ' '))
print(f"   Аномалий: {len(anomalies)} (критичных: {critical_count})  |  Period: {period_from} — {period_to}")
print(f"   Категорий: {len(cat_spend)}  |  Топ: {top_cat} {top_cat_pct}%  |  Ср.накл: {avg_invoice:,} ₸".replace(',', ' '))
print(f"   Тренды цен: {len(price_trends)} SKU  |  Недель: {len(weeks)}")
