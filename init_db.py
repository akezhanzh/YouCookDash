"""
init_db.py — YouCook Procurement System
Initializes the SQLite database schema for YouCookDashOG.db
Run once on first setup, safe to re-run (uses IF NOT EXISTS).
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "YouCookDashOG.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── SUPPLIERS ──────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            contact     TEXT,
            phone       TEXT,
            whatsapp    TEXT,
            city        TEXT    DEFAULT 'Алматы',
            is_vetted   INTEGER DEFAULT 0,
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (date('now')),
            notes       TEXT
        )
    """)

    # ── SKU CATALOG ────────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS sku_catalog (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            category    TEXT,
            unit        TEXT    NOT NULL DEFAULT 'кг',
            description TEXT,
            created_at  TEXT    DEFAULT (date('now'))
        )
    """)

    # ── INVOICES (header) ──────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id      TEXT    UNIQUE,
            supplier_id     INTEGER REFERENCES suppliers(id),
            invoice_date    TEXT,
            total_amount    REAL,
            pdf_filename    TEXT,
            source          TEXT    DEFAULT 'pdf',  -- 'pdf' | 'whatsapp' | 'manual'
            is_processed    INTEGER DEFAULT 0,
            is_duplicate    INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT (datetime('now')),
            notes           TEXT
        )
    """)

    # ── INVOICE LINES ──────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS invoice_lines (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id      INTEGER REFERENCES invoices(id),
            sku_id          INTEGER REFERENCES sku_catalog(id),
            sku_raw         TEXT,
            unit            TEXT,
            qty             REAL,
            unit_price      REAL,
            line_total      REAL,
            is_overpriced   INTEGER DEFAULT 0,
            overprice_pct   REAL,
            created_at      TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── PRICE MATRIX ──────────────────────────────────────────────────────────
    # One row per (sku, supplier, date). Query view handles aggregation.
    c.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_id      INTEGER REFERENCES sku_catalog(id),
            supplier_id INTEGER REFERENCES suppliers(id),
            price       REAL    NOT NULL,
            unit        TEXT,
            date        TEXT    NOT NULL,
            invoice_id  INTEGER REFERENCES invoices(id),
            source      TEXT    DEFAULT 'invoice',
            is_overpriced INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── PRICE SUMMARY VIEW ────────────────────────────────────────────────────
    c.execute("DROP VIEW IF EXISTS price_summary")
    c.execute("""
        CREATE VIEW price_summary AS
        SELECT
            sc.name                                 AS sku,
            sc.unit                                 AS unit,
            s.name                                  AS supplier,
            p.price                                 AS last_price,
            p.date                                  AS last_date,
            AVG(p.price) OVER (PARTITION BY p.sku_id)        AS avg_price_all_time,
            MIN(p.price) OVER (PARTITION BY p.sku_id)        AS min_price_ever,
            MAX(p.price) OVER (PARTITION BY p.sku_id)        AS max_price_ever,
            AVG(p.price) OVER (
                PARTITION BY p.sku_id
                ORDER BY p.date DESC
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )                                       AS avg_30d
        FROM prices p
        JOIN sku_catalog sc ON sc.id = p.sku_id
        JOIN suppliers   s  ON s.id  = p.supplier_id
    """)

    # ── ANOMALIES LOG ─────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            anomaly_type    TEXT,   -- 'overpriced' | 'duplicate' | 'spike'
            sku_id          INTEGER REFERENCES sku_catalog(id),
            supplier_id     INTEGER REFERENCES suppliers(id),
            invoice_id      INTEGER REFERENCES invoices(id),
            detail          TEXT,
            severity        TEXT    DEFAULT 'medium',  -- low/medium/high
            is_resolved     INTEGER DEFAULT 0,
            detected_at     TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── WEEKLY REPORTS INDEX ──────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date     TEXT    UNIQUE,
            filename        TEXT,
            total_spend     REAL,
            total_overpay   REAL,
            top_saving_sku  TEXT,
            top_saving_amt  REAL,
            created_at      TEXT    DEFAULT (datetime('now'))
        )
    """)

    # ── INDEXES ───────────────────────────────────────────────────────────────
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_sku      ON prices(sku_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_supplier ON prices(supplier_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_prices_date     ON prices(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_invoices_date   ON invoices(invoice_date)")

    conn.commit()
    conn.close()
    print(f"[OK] YouCookDashOG.db initialized at {DB_PATH}")
    return str(DB_PATH)


if __name__ == "__main__":
    init_db()
