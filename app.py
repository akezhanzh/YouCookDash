"""
app.py — Flask сервер с Google OAuth + ручное одобрение пользователей.
Деплой: Render.com (gunicorn app:app)
"""
import os
import sqlite3
from pathlib import Path
from datetime import timedelta, datetime

from flask import Flask, session, redirect, url_for, send_file, request, g

BASE      = Path(__file__).parent
DASHBOARD = BASE / "docs" / "index.html"
LOGIN_HTML= BASE / "login.html"
USERS_DB  = BASE / "users.db"

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

# ── База данных пользователей ──────────────────────────────────────────────────
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

# ── Google OAuth ───────────────────────────────────────────────────────────────
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

PENDING_PAGE = page(
    "Заявка отправлена", """
    <div style="font-size:40px;margin-bottom:16px">⏳</div>
    <h2>Заявка отправлена</h2>
    <p>Ваш аккаунт ожидает подтверждения администратора.</p>
    <p>Попробуйте войти позже.</p>
    <br>
    <a href="/logout">← Выйти</a>
""", "#f59e0b")

DENIED_PAGE = page(
    "Нет доступа", """
    <div style="font-size:40px;margin-bottom:16px">🚫</div>
    <h2>Нет доступа</h2>
    <p>Ваш аккаунт не был одобрен.</p>
    <br>
    <a href="/logout">← Выйти</a>
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
        return send_file(DASHBOARD)
    if row["status"] == "denied":
        return DENIED_PAGE
    return PENDING_PAGE


@app.route("/login")
def login():
    return oauth.google.authorize_redirect(url_for("callback", _external=True))


@app.route("/auth/callback")
def callback():
    token    = oauth.google.authorize_access_token()
    info     = token.get("userinfo", {})
    email    = info.get("email", "")
    name     = info.get("name", email)
    picture  = info.get("picture", "")
    now      = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    exists = db.execute("SELECT status FROM users WHERE email=?", (email,)).fetchone()

    if not exists:
        # Новый пользователь — добавляем со статусом pending
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


# ── Админ-панель ──────────────────────────────────────────────────────────────
@app.route("/admin", methods=["GET", "POST"])
def admin():
    # Проверка пароля
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

    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect("/admin")
        return redirect("/admin")

    # Действия: одобрить / отклонить
    action = request.args.get("action")
    email  = request.args.get("email")
    if action in ("approve", "deny") and email:
        new_status = "approved" if action == "approve" else "denied"
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db = get_db()
        db.execute("UPDATE users SET status=?, updated_at=? WHERE email=?", (new_status, now, email))
        db.commit()
        return redirect("/admin")

    # Список пользователей
    db    = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()

    STATUS_LABEL = {
        "pending":  ("<span style='color:#f59e0b'>⏳ Ожидает</span>",  "approve", "deny",    "Одобрить", "Отклонить"),
        "approved": ("<span style='color:#22c55e'>✅ Одобрен</span>",  "deny",    "",        "Отклонить", ""),
        "denied":   ("<span style='color:#ef4444'>🚫 Отклонён</span>", "approve", "",        "Одобрить",  ""),
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
  <p style="margin-top:16px;color:#2a2d3e;font-size:11px">Страница обновляется вручную — нажми F5 чтобы увидеть новых</p>
</body></html>"""
