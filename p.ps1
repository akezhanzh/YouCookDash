# YouCook — PowerShell shortcut
# Usage from procurement folder:
#   .\p.ps1 init_db.py
#   .\p.ps1 price_check.py --anomalies
#   .\p.ps1 parse_invoice.py --batch
#   .\p.ps1 weekly_report.py

$PYTHON = "C:\Users\Gigabyte\AppData\Local\Programs\Python\Python313\python.exe"
& $PYTHON $args
