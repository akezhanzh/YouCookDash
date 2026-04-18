"""
app.py — Flask + Google OAuth + Telegram бот + динамический дашборд.
Деплой: Render.com (gunicorn app:app)
"""
import base64
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import timedelta, datetime

import requests as http_req
from flask import Flask, session, redirect, url_for, send_file, request, g, Response

BASE       = Path(__file__).parent
DASHBOARD  = BASE / "docs" / "index.html"
LOGIN_HTML = BASE / "login.html"
USERS_DB   = BASE / "users.db"
PROC_DB    = BASE / "data" / "YouCookDashOG.db"

sys.path.insert(0, str(BASE))

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.permanent_session_lifetime = timedelta(days=30)

IS_PROD = bool(os.environ.get("RENDER"))
if IS_PROD:
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO    = "akezhanzh/YouCookDash"

# ── Дашборд в памяти ──────────────────────────────────────────────────────────
_dashboard_html: str | None = None

def _load_dashboard():
    global _dashboard_html
    if DASHBOARD.exists():
        _dashboard_html = DASHBOARD.read_text(encoding="utf-8")

def regenerate_dashboard():
    global _dashboard_html
    if not PROC_DB.exists():
        return
    try:
        subprocess.run(
            [sys.executable, str(BASE / "generate_dashboard.py")],
            cwd=BASE, check=True, timeout=60,
            capture_output=True
        )
        _load_dashboard()
        print("[dashboard] regenerated")
    except Exception as e:
        print(f"[dashboard] failed: {e}")


# ── GitHub — бэкап базы ───────────────────────────────────────────────────────
def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

def pull_db_from_github():
    """Скачать YouCookDashOG.db из GitHub при старте."""
    if not GITHUB_TOKEN:
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/YouCookDashOG.db"
    try:
        r = http_req.get(url, headers=_gh_headers(), timeout=30)
        if r.ok:
            content = base64.b64decode(r.json()["content"].replace("\n", ""))
            PROC_DB.parent.mkdir(parents=True, exist_ok=True)
            PROC_DB.write_bytes(content)
            print("[startup] YouCookDashOG.db pulled from GitHub")
    except Exception as e:
        print(f"[startup] pull DB failed: {e}")

def push_db_to_github(message="update: YouCookDashOG.db via bot"):
    """Сохранить YouCookDashOG.db в GitHub после каждого обновления."""
    if not GITHUB_TOKEN or not PROC_DB.exists():
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/YouCookDashOG.db"
    headers = _gh_headers()
    try:
        r = http_req.get(url, headers=headers, timeout=30)
        sha = r.json().get("sha") if r.ok else None
        content = base64.b64encode(PROC_DB.read_bytes()).decode()
        payload = {"message": message, "content": content}
        if sha:
            payload["sha"] = sha
        http_req.put(url, json=payload, headers=headers, timeout=60)
        print(f"[github] DB pushed: {message}")
    except Exception as e:
        print(f"[github] push DB failed: {e}")


# ── Старт ─────────────────────────────────────────────────────────────────────
if not PROC_DB.exists():
    pull_db_from_github()

PROC_DB.parent.mkdir(parents=True, exist_ok=True)
regenerate_dashboard()
_load_dashboard()


# ── База пользователей ────────────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(USERS_DB)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    db = sqlite3.connect(USERS_DB)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email      TEXT PRIMARY KEY,
            name       TEXT,
            picture    TEXT,
            status     TEXT DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    db.commit()
    db.close()

init_db()


# ── Google OAuth ──────────────────────────────────────────────────────────────
from authlib.integrations.flask_client import OAuth
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── HTML-шаблоны ──────────────────────────────────────────────────────────────
def page(title, body, color="#6366f1"):
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouCook — {title}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f1117;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;
       min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}}
  .card{{background:#1a1d27;border:1px solid #2a2d3e;border-radius:20px;
         padding:40px;text-align:center;max-width:420px;width:100%}}
  h2{{font-size:22px;font-weight:700;margin-bottom:10px;color:{color}}}
  p{{color:#64748b;font-size:13px;line-height:1.6;margin-bottom:8px}}
  a{{color:#6366f1;text-decoration:none;font-size:13px}}
  a:hover{{text-decoration:underline}}
</style></head><body><div class="card">{body}</div></body></html>"""

PENDING_PAGE = page("Заявка отправлена", """
    <div style="font-size:40px;margin-bottom:16px">⏳</div>
    <h2>Заявка отправлена</h2>
    <p>Ваш аккаунт ожидает подтверждения администратора.</p>
    <p>Попробуйте войти позже.</p>
    <br><a href="/logout">← Выйти</a>
""", "#f59e0b")

DENIED_PAGE = page("Нет доступа", """
    <div style="font-size:40px;margin-bottom:16px">🚫</div>
    <h2>Нет доступа</h2>
    <p>Ваш аккаунт не был одобрен.</p>
    <br><a href="/logout">← Выйти</a>
""", "#ef4444")


# ── Маршруты ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return LOGIN_HTML.read_text(encoding="utf-8")

    db  = get_db()
    row = db.execute("SELECT status FROM users WHERE email=?", (user["email"],)).fetchone()
    if not row:
        return PENDING_PAGE
    if row["status"] == "approved":
        if _dashboard_html:
            return Response(_dashboard_html, mimetype="text/html")
        return send_file(DASHBOARD)
    if row["status"] == "denied":
        return DENIED_PAGE
    return PENDING_PAGE


@app.route("/login")
def login():
    return oauth.google.authorize_redirect(url_for("callback", _external=True))


@app.route("/auth/callback")
def callback():
    token   = oauth.google.authorize_access_token()
    info    = token.get("userinfo", {})
    email   = info.get("email", "")
    name    = info.get("name", email)
    picture = info.get("picture", "")
    now     = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    exists = db.execute("SELECT status FROM users WHERE email=?", (email,)).fetchone()
    if not exists:
        db.execute(
            "INSERT INTO users (email, name, picture, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (email, name, picture, "pending", now, now)
        )
        db.commit()

    session.permanent = True
    session["user"] = {"email": email, "name": name}
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ── Быстрое одобрение владельца ───────────────────────────────────────────────
@app.route("/setup")
def setup():
    if request.args.get("key") != ADMIN_PASSWORD:
        return "403", 403
    owner = "akezhanz@youcook.kz"
    now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db    = get_db()
    db.execute("UPDATE users SET status='approved', updated_at=? WHERE email=?", (now, owner))
    db.commit()
    return redirect("/")


# ── Админ-панель ──────────────────────────────────────────────────────────────
@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            return redirect(f"/admin?key={ADMIN_PASSWORD}")
        return redirect("/admin")

    if request.args.get("key") != ADMIN_PASSWORD and session.get("admin") != True:
        return page("Вход в админ", f"""
            <h2>Админ-панель</h2>
            <form method="POST" action="/admin" style="margin-top:20px">
              <input name="password" type="password" placeholder="Пароль"
                style="width:100%;padding:10px 14px;border-radius:8px;
                       border:1px solid #2a2d3e;background:#0f1117;color:#e2e8f0;
                       font-size:14px;margin-bottom:12px">
              <button type="submit"
                style="width:100%;padding:10px;background:#6366f1;color:#fff;
                       border:none;border-radius:8px;font-size:14px;cursor:pointer">
                Войти
              </button>
            </form>
        """)

    action = request.args.get("action")
    email  = request.args.get("email")
    if action in ("approve", "deny") and email:
        new_status = "approved" if action == "approve" else "denied"
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db  = get_db()
        db.execute("UPDATE users SET status=?, updated_at=? WHERE email=?", (new_status, now, email))
        db.commit()
        return redirect(f"/admin?key={ADMIN_PASSWORD}")

    db    = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    STATUS_LABEL = {
        "pending":  ("<span style='color:#f59e0b'>⏳ Ожидает</span>",  "approve", "deny",   "Одобрить", "Отклонить"),
        "approved": ("<span style='color:#22c55e'>✅ Одобрен</span>",  "deny",    "",       "Отклонить", ""),
        "denied":   ("<span style='color:#ef4444'>🚫 Отклонён</span>", "approve", "",       "Одобрить",  ""),
    }

    rows_html = ""
    for u in users:
        label, act1, act2, lbl1, lbl2 = STATUS_LABEL.get(u["status"], ("—","","","",""))
        btn1 = f'<a href="/admin?key={ADMIN_PASSWORD}&action={act1}&email={u["email"]}" style="color:#6366f1;font-size:12px;margin-right:8px">{lbl1}</a>' if act1 else ""
        btn2 = f'<a href="/admin?key={ADMIN_PASSWORD}&action={act2}&email={u["email"]}" style="color:#ef4444;font-size:12px">{lbl2}</a>' if act2 else ""
        pic  = f'<img src="{u["picture"]}" style="width:28px;height:28px;border-radius:50%;vertical-align:middle;margin-right:8px">' if u["picture"] else ""
        rows_html += f"""
        <tr>
          <td style="padding:10px 12px">{pic}{u['name']}<br>
            <span style="color:#64748b;font-size:11px">{u['email']}</span></td>
          <td style="padding:10px 12px;color:#64748b;font-size:11px">{u['created_at']}</td>
          <td style="padding:10px 12px">{label}</td>
          <td style="padding:10px 12px">{btn1}{btn2}</td>
        </tr>"""

    pending_count = sum(1 for u in users if u["status"] == "pending")
    badge = f' <span style="background:#ef4444;color:#fff;border-radius:20px;padding:2px 8px;font-size:11px">{pending_count}</span>' if pending_count else ""

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>YouCook — Админ</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#0f1117;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:24px}}
  h1{{font-size:20px;font-weight:700;margin-bottom:20px}}
  h1 span{{color:#6366f1}}
  table{{width:100%;border-collapse:collapse;background:#1a1d27;border:1px solid #2a2d3e;border-radius:12px;overflow:hidden}}
  th{{text-align:left;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.6px;padding:10px 12px;border-bottom:1px solid #2a2d3e}}
  tr:hover td{{background:rgba(255,255,255,.02)}}
  tr:last-child td{{border-bottom:none}}
  td{{border-bottom:1px solid rgba(255,255,255,.04)}}
  .back{{color:#6366f1;font-size:12px;text-decoration:none;display:inline-block;margin-bottom:16px}}
</style></head>
<body>
  <a href="/" class="back">← Дашборд</a>
  <h1>You<span>Cook</span> · Пользователи{badge}</h1>
  <table>
    <thead><tr>
      <th>Пользователь</th><th>Дата заявки</th><th>Статус</th><th>Действия</th>
    </tr></thead>
    <tbody>{rows_html if rows_html else '<tr><td colspan="4" style="padding:20px;color:#64748b;text-align:center">Пользователей пока нет</td></tr>'}</tbody>
  </table>
  <p style="margin-top:16px;color:#2a2d3e;font-size:11px">Страница обновляется вручную — нажми F5</p>
</body></html>"""


# ── Telegram helpers ──────────────────────────────────────────────────────────
def _tg(method, **kwargs):
    if not TELEGRAM_TOKEN:
        return {}
    try:
        r = http_req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}",
            json=kwargs, timeout=30
        )
        return r.json().get("result", {})
    except Exception:
        return {}

def tg_send(chat_id, text):
    return _tg("sendMessage", chat_id=chat_id, text=text).get("message_id")

def tg_edit(chat_id, msg_id, text):
    if not msg_id:
        return tg_send(chat_id, text)
    _tg("editMessageText", chat_id=chat_id, message_id=msg_id, text=text)


# Хранилище для накладных без поставщика: {chat_id: parsed_data}
_pending_invoices: dict = {}


def _process_invoice(chat_id, parsed, status_id):
    """Загружает накладную в БД, обновляет дашборд и бэкапит в GitHub."""
    from parse_invoice import ingest

    PROC_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(PROC_DB)
    summary = ingest(conn, parsed)
    conn.close()

    if summary["duplicate"]:
        tg_edit(chat_id, status_id,
                f"ℹ️ Накладная №{parsed.get('invoice_id','?')} уже есть в базе.")
        return

    # Обновить дашборд сразу
    regenerate_dashboard()

    # Бэкап базы в GitHub (в фоне — не блокирует ответ)
    push_db_to_github(f"update: invoice {parsed.get('invoice_id','?')} via telegram")

    ovr = (f"\n⚠️ Переплата по {len(summary['overpriced'])} позициям"
           if summary["overpriced"] else "")

    total_fmt = f"{int(parsed.get('total', 0)):,}".replace(",", " ")
    tg_edit(chat_id, status_id,
        f"✅ Накладная добавлена!\n"
        f"№{parsed.get('invoice_id','—')} от {parsed.get('date','—')}\n"
        f"🏢 {parsed.get('supplier','—')}\n"
        f"💰 {total_fmt} ₸ · {len(parsed.get('lines', []))} позиций"
        + ovr
    )


# ── Telegram webhook ──────────────────────────────────────────────────────────
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    if not TELEGRAM_TOKEN:
        return "ok"

    data    = request.get_json(force=True) or {}
    message = data.get("message") or data.get("edited_message") or {}
    chat_id = message.get("chat", {}).get("id")
    if not chat_id:
        return "ok"

    text     = (message.get("text") or "").strip()
    document = message.get("document")

    # ── Команды ───────────────────────────────────────────────────────────────
    if text.startswith("/start") or text.startswith("/help"):
        tg_send(chat_id,
            "YouCook Procurement Bot\n\n"
            "Отправь PDF или XLSX накладную — добавлю в базу и обновлю дашборд.\n\n"
            "/stats — статистика закупок\n"
            "/help — эта справка"
        )
        return "ok"

    if text.startswith("/stats"):
        if PROC_DB.exists():
            conn  = sqlite3.connect(PROC_DB)
            total = conn.execute("SELECT COALESCE(SUM(total_amount),0) FROM invoices").fetchone()[0]
            n_inv = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
            n_sku = conn.execute("SELECT COUNT(DISTINCT sku_id) FROM invoice_lines").fetchone()[0]
            last  = conn.execute("SELECT MAX(invoice_date) FROM invoices").fetchone()[0]
            conn.close()
            total_fmt = f"{int(total):,}".replace(",", " ")
            tg_send(chat_id,
                f"📊 Статистика YouCook\n\n"
                f"💰 Итого: {total_fmt} ₸\n"
                f"📋 Накладных: {n_inv}\n"
                f"📦 Уникальных SKU: {n_sku}\n"
                f"📅 Последняя: {last or '—'}"
            )
        else:
            tg_send(chat_id, "База данных пуста. Отправь первую накладную!")
        return "ok"

    # ── Ответ на вопрос о поставщике ──────────────────────────────────────────
    if chat_id in _pending_invoices and text and not text.startswith("/"):
        entry  = _pending_invoices.pop(chat_id)
        parsed = entry["data"]
        s_id   = entry["status_id"]
        parsed["supplier"] = text
        try:
            _process_invoice(chat_id, parsed, s_id)
        except Exception as e:
            tg_edit(chat_id, s_id, f"❌ Ошибка: {str(e)[:300]}")
        return "ok"

    # ── Файл ──────────────────────────────────────────────────────────────────
    if not document:
        if text:
            tg_send(chat_id, "Отправь PDF или XLSX накладную.")
        return "ok"

    fname = document.get("file_name", "invoice.pdf")
    ext   = Path(fname).suffix.lower()

    if ext not in (".pdf", ".xlsx", ".xls"):
        tg_send(chat_id, "⚠️ Поддерживаются только PDF и XLSX файлы.")
        return "ok"

    status_id = tg_send(chat_id, "⏳ Обрабатываю накладную...")

    tmp_dir  = Path(tempfile.mkdtemp())
    tmp_path = tmp_dir / fname

    try:
        # Скачать файл из Telegram
        file_info = _tg("getFile", file_id=document["file_id"])
        file_path = file_info.get("file_path", "")
        content   = http_req.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
            timeout=60
        ).content
        tmp_path.write_bytes(content)

        # Парсинг
        from parse_invoice import parse_pdf, parse_xlsx
        parsed = parse_pdf(tmp_path) if ext == ".pdf" else parse_xlsx(tmp_path)

        # Если поставщик не найден — спросить
        if not parsed.get("supplier"):
            _pending_invoices[chat_id] = {"data": parsed, "status_id": status_id}
            tg_edit(chat_id, status_id,
                "❓ Не удалось определить поставщика автоматически.\n\n"
                "Напиши название поставщика в ответном сообщении\n"
                "(например: ИП Иванов или ТОО АгроМаркет)"
            )
            return "ok"

        _process_invoice(chat_id, parsed, status_id)

    except Exception as e:
        tg_edit(chat_id, status_id, f"❌ Ошибка: {str(e)[:300]}")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass

    return "ok"


# ── Регистрация вебхука (один раз при старте) ─────────────────────────────────
if TELEGRAM_TOKEN and IS_PROD:
    try:
        http_req.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            json={"url": "https://youcookdash.onrender.com/telegram",
                  "allowed_updates": ["message"]},
            timeout=15
        )
        print("[telegram] webhook registered")
    except Exception as e:
        print(f"[telegram] webhook setup failed: {e}")
