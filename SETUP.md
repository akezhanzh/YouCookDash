# YouCook Procurement System — Setup Guide

## 1. Install Python 3.11+

**Option A — Microsoft Store (easiest):**
Open PowerShell and run:
```
winget install Python.Python.3.11
```

**Option B — Direct download:**
https://www.python.org/downloads/ → Download Python 3.11.x → Run installer
Check "Add Python to PATH" during install.

## 2. Install Dependencies

Open PowerShell/CMD in `C:\Users\Gigabyte\procurement\`:
```
pip install -r requirements.txt
playwright install chromium
```

## 3. Initialize Database

```
python init_db.py
```

Expected output:
```
[OK] procurement.db initialized at .\data\procurement.db
```

## 4. Add Your First Invoices

Drop PDF files into `C:\Users\Gigabyte\procurement\invoices\`
Then run:
```
python parse_invoice.py --batch
```

## 5. Run Daily Checks

```
# Check anomalies from last 7 days
python price_check.py --anomalies

# Find cheapest supplier for a SKU
python price_check.py --sku "Куриное филе"

# Generate weekly report
python weekly_report.py
```

## 6. Telegram Bot

1. Message @BotFather on Telegram → /newbot → copy your TOKEN
2. Get your Telegram chat ID from @userinfobot
3. Set environment variables:
   ```
   set YOUCOOK_BOT_TOKEN=your_token_here
   set MANAGER_CHAT_ID=your_chat_id
   set CFO_CHAT_ID=cfo_chat_id
   ```
4. Run:
   ```
   python telegram_bot.py
   ```

## 7. WhatsApp Monitor (optional)

```
python whatsapp_monitor.py --group "Закупки YouCook" --interval 60
```
A browser window will open. Scan QR code, then press Enter in terminal.

## Directory Structure

```
procurement/
├── data/
│   └── procurement.db        ← SQLite database
├── invoices/                 ← Drop PDF invoices here
├── reports/                  ← Generated weekly reports
├── logs/                     ← Application logs
├── init_db.py                ← DB schema initialization
├── parse_invoice.py          ← PDF invoice parser
├── price_check.py            ← Price lookup & anomaly detection
├── weekly_report.py          ← Monday report generator
├── whatsapp_monitor.py       ← WhatsApp Web price capture
├── telegram_bot.py           ← Order submission & approval bot
└── requirements.txt
```
