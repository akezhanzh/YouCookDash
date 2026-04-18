"""
app.py — Flask сервер с Google OAuth аутентификацией для YouCook Dashboard.
Деплой: Render.com (gunicorn app:app)
"""
import os
from pathlib import Path
from datetime import timedelta

from flask import Flask, session, redirect, url_for, send_file, request
from authlib.integrations.flask_client import OAuth

BASE      = Path(__file__).parent
DASHBOARD = BASE / "docs" / "index.html"
LOGIN_HTML= BASE / "login.html"

app = Flask(__name__)
app.secret_key        = os.environ["SECRET_KEY"]
app.permanent_session_lifetime = timedelta(days=30)

# На Render.com автоматически ставится переменная RENDER=true
IS_PROD = bool(os.environ.get("RENDER"))
if IS_PROD:
    app.config.update(
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

# ── Необязательно: ограничение по email ───────────────────────────────────────
# Если ALLOWED_EMAILS не задан → пускает любой Google-аккаунт
# Если задан → только эти email (через запятую, без пробелов)
ALLOWED_EMAILS = set(
    e.strip() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
)

# ── Google OAuth ───────────────────────────────────────────────────────────────
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Маршруты ──────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("user"):
        return LOGIN_HTML.read_text(encoding="utf-8")
    return send_file(DASHBOARD)


@app.route("/login")
def login():
    redirect_uri = url_for("callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def callback():
    token = oauth.google.authorize_access_token()
    info  = token.get("userinfo", {})
    email = info.get("email", "")

    if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
        return (
            f"""<html><body style="background:#0f1117;color:#e2e8f0;
            font-family:system-ui;display:flex;align-items:center;
            justify-content:center;min-height:100vh;text-align:center">
            <div>
              <h2 style="color:#ef4444">Нет доступа</h2>
              <p style="color:#64748b;margin:12px 0">
                Аккаунт <b>{email}</b> не авторизован.
              </p>
              <a href="/logout" style="color:#6366f1">← Выйти</a>
            </div></body></html>""",
            403,
        )

    session.permanent = True
    session["user"] = {
        "email": email,
        "name":  info.get("name", email),
    }
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(debug=not IS_PROD, port=5000)
