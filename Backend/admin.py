"""
admin.py - the NextHome Energy administrator console (a web page served by the same Flask server).

Open   http://localhost:5000/admin   and sign in with the admin password.

What the administrator can do
  * Overview   - accounts, meters, alerts sent today, open feedback
  * Health     - database, meters, solar link, email/SMS delivery mode, server uptime and version
  * Accounts   - see every user and their meters; disable or re-enable an account
  * Alerts     - the log of every SMS alert the system sent (who, which meter, why, when)
  * Feedback   - messages users send from the app, with a "mark resolved" button

Also here:  POST /feedback  (the app sends feedback with the user's login token)
            GET  /version   (app and server version, used for upgrade checks)

Environment:  ADMIN_PASSWORD  the console password (default "admin123" - change it for any real use)
"""
import datetime
import hmac
import os
import secrets
import time

from flask import Blueprint, jsonify, make_response, redirect, render_template_string, request

from database import get_connection
import accounts
import notify

bp = Blueprint("admin", __name__)

APP_VERSION = "1.0.0"
STARTED = time.time()
COOKIE = "nh_admin"
_sessions = {}            # admin session tokens -> expiry time (kept in memory; a restart signs the admin out)
_fails = {"count": 0, "until": 0.0}


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "admin123")


def init():
    conn = get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER, "
                 "email TEXT, category TEXT, message TEXT NOT NULL, status TEXT DEFAULT 'open', "
                 "created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
    if "disabled" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN disabled INTEGER DEFAULT 0")
    conn.commit()
    conn.close()
    if admin_password() == "admin123":
        print("NOTE: the admin console uses the default password 'admin123'. Set ADMIN_PASSWORD to change it.")


def _signed_in():
    token = request.cookies.get(COOKIE)
    exp = _sessions.get(token)
    if exp and exp > time.time():
        return True
    _sessions.pop(token, None)
    return False


# ---------------- endpoints used by the app ----------------

@bp.route("/version", methods=["GET"])
def version():
    return jsonify({"app": "NextHome Energy", "version": APP_VERSION,
                    "uptime_seconds": int(time.time() - STARTED)}), 200


@bp.route("/feedback", methods=["POST"])
def feedback():
    conn = get_connection()
    try:
        acct = accounts.current_account(conn)
        if not acct:
            return jsonify({"error": "Please log in first"}), 401
        d = request.get_json(silent=True) or {}
        message = (d.get("message") or "").strip()
        category = d.get("category") if d.get("category") in ("bug", "idea", "other") else "other"
        if len(message) < 3:
            return jsonify({"error": "Please write a short message"}), 400
        conn.execute("INSERT INTO feedback (account_id, email, category, message) VALUES (?,?,?,?)",
                     (acct["id"], acct["email"], category, message[:1000]))
        conn.commit()
        return jsonify({"message": "Thank you - your feedback was sent"}), 201
    finally:
        conn.close()


# ---------------- the console ----------------

def _health(conn):
    rows = []

    def add(label, ok, detail=""):
        rows.append({"label": label, "ok": ok, "detail": detail})

    for t in ("accounts", "account_meters", "meters", "consumption_history", "alert_log", "feedback"):
        try:
            n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            add(f"Database table: {t}", True, f"{n} rows")
        except Exception as e:  # noqa: BLE001
            add(f"Database table: {t}", False, str(e))

    for m in conn.execute("SELECT meter_number, balance FROM meters ORDER BY meter_number"):
        days = conn.execute("SELECT COUNT(*) AS n FROM consumption_history WHERE meter_number = ?",
                            (m["meter_number"],)).fetchone()["n"]
        solar = conn.execute("SELECT last_updated FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1",
                             (m["meter_number"],)).fetchone()
        extra = f", solar last reported {solar['last_updated']} UTC" if solar else ""
        add(f"Meter {m['meter_number']}", True, f"balance KSh {m['balance']:.2f}, {days} days of history{extra}")

    email_real = bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))
    sms_real = bool(os.environ.get("AT_USERNAME") and os.environ.get("AT_API_KEY"))
    add("Email delivery", True, "real (SMTP)" if email_real else "simulated - codes print in the server window")
    add("SMS delivery", True, "real (Africa's Talking)" if sms_real else "simulated - alerts print in the server window")
    return rows


@bp.route("/admin/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if time.time() < _fails["until"]:
            error = "Too many wrong attempts. Wait a minute and try again."
        elif hmac.compare_digest((request.form.get("password") or "").encode(), admin_password().encode()):
            _fails["count"] = 0
            token = secrets.token_urlsafe(32)
            _sessions[token] = time.time() + 8 * 3600
            resp = make_response(redirect("/admin"))
            resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=8 * 3600)
            return resp
        else:
            _fails["count"] += 1
            if _fails["count"] >= 5:
                _fails["count"], _fails["until"] = 0, time.time() + 60
            error = "Wrong password."
    return render_template_string(LOGIN_HTML, error=error, css=CSS)


@bp.route("/admin/logout", methods=["POST"])
def logout():
    _sessions.pop(request.cookies.get(COOKIE), None)
    resp = make_response(redirect("/admin/login"))
    resp.delete_cookie(COOKIE)
    return resp


@bp.route("/admin", methods=["GET"])
def dashboard():
    if not _signed_in():
        return redirect("/admin/login")
    conn = get_connection()
    try:
        since = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        one = lambda q, *a: conn.execute(q, a).fetchone()["n"]  # noqa: E731
        stats = {
            "accounts": one("SELECT COUNT(*) AS n FROM accounts"),
            "verified": one("SELECT COUNT(*) AS n FROM accounts WHERE verified = 1"),
            "disabled": one("SELECT COUNT(*) AS n FROM accounts WHERE COALESCE(disabled,0) = 1"),
            "meters": one("SELECT COUNT(*) AS n FROM meters"),
            "alerts_24h": one("SELECT COUNT(*) AS n FROM alert_log WHERE sent_at > ?", since),
            "open_feedback": one("SELECT COUNT(*) AS n FROM feedback WHERE status = 'open'"),
        }
        people = []
        for a in conn.execute("SELECT * FROM accounts ORDER BY id DESC"):
            ms = [r["meter_number"] for r in conn.execute(
                "SELECT meter_number FROM account_meters WHERE account_id = ? ORDER BY rowid", (a["id"],))]
            people.append({"id": a["id"], "name": a["full_name"], "email": a["email"], "phone": a["phone"],
                           "type": a["account_type"], "verified": bool(a["verified"]),
                           "disabled": bool(a["disabled"]), "created": a["created_at"], "meters": ", ".join(ms) or "-"})
        alerts = [dict(r) for r in conn.execute(
            "SELECT meter_number, kind, message, sent_at FROM alert_log ORDER BY id DESC LIMIT 50")]
        notes = [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY (status = 'open') DESC, id DESC LIMIT 50")]
        health = _health(conn)
    finally:
        conn.close()
    up = int(time.time() - STARTED)
    return render_template_string(DASH_HTML, css=CSS, stats=stats, people=people, alerts=alerts, notes=notes,
                                  health=health, version=APP_VERSION,
                                  uptime=f"{up // 3600}h {(up % 3600) // 60}m",
                                  all_ok=all(h["ok"] for h in health))


def _post_action(sql, *params):
    if not _signed_in():
        return redirect("/admin/login")
    conn = get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    return redirect("/admin")


@bp.route("/admin/accounts/<int:account_id>/toggle", methods=["POST"])
def toggle_account(account_id):
    if not _signed_in():
        return redirect("/admin/login")
    conn = get_connection()
    try:
        conn.execute("UPDATE accounts SET disabled = CASE WHEN COALESCE(disabled,0) = 1 THEN 0 ELSE 1 END WHERE id = ?",
                     (account_id,))
        # Signing a user out everywhere makes "disable" take effect immediately.
        conn.execute("DELETE FROM sessions WHERE account_id = ? AND (SELECT disabled FROM accounts WHERE id = ?) = 1",
                     (account_id, account_id))
        conn.commit()
    finally:
        conn.close()
    return redirect("/admin")


@bp.route("/admin/feedback/<int:note_id>/resolve", methods=["POST"])
def resolve_feedback(note_id):
    return _post_action("UPDATE feedback SET status = 'resolved' WHERE id = ?", note_id)


# ---------------- page templates ----------------

CSS = """
:root{--bg:#0B1220;--card:#121C30;--tile:#1B2740;--text:#F8FAFC;--muted:#9AA8BD;--gold:#F5B301;--ok:#34D399;--bad:#F87171}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
header{background:linear-gradient(#0B1220,#0F2A66);padding:18px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
header h1{margin:0;font-size:20px}header h1 span{color:var(--gold)}header small{color:var(--muted)}
main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.stat{background:var(--card);border-radius:14px;padding:14px 16px}.stat b{display:block;font-size:26px;color:var(--gold)}.stat span{color:var(--muted);font-size:13px}
section{background:var(--card);border-radius:14px;padding:16px;margin-bottom:20px;overflow-x:auto}
h2{margin:0 0 12px;font-size:17px}table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--tile);vertical-align:top}th{color:var(--muted);font-weight:600}
.ok{color:var(--ok)}.bad{color:var(--bad)}.pill{display:inline-block;padding:1px 9px;border-radius:99px;background:var(--tile);font-size:12px}
.pill.off{background:#3b1d24;color:var(--bad)}.pill.on{background:#12352b;color:var(--ok)}
button,.btn{background:var(--gold);color:#0B1220;border:0;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer}
button.ghost{background:var(--tile);color:var(--text)}input[type=password]{width:100%;padding:12px;border-radius:10px;border:1px solid var(--tile);background:var(--bg);color:var(--text);font-size:16px}
.login{max-width:360px;margin:12vh auto;background:var(--card);padding:24px;border-radius:16px}.empty{color:var(--muted)}
"""

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NextHome Admin</title><style>{{ css|safe }}</style></head><body>
<form class="login" method="post"><h1 style="margin-top:0">NextHome <span style="color:var(--gold)">Admin</span></h1>
<p style="color:var(--muted)">Administrator sign-in</p>
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<input type="password" name="password" placeholder="Admin password" autofocus required>
<p><button type="submit" style="width:100%;padding:12px">Sign in</button></p></form></body></html>"""

DASH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NextHome Admin</title><style>{{ css|safe }}</style></head><body>
<header><h1>NextHome <span>Admin</span></h1>
<div><small>Version {{ version }} &middot; up {{ uptime }}</small>
<form method="post" action="/admin/logout" style="display:inline;margin-left:12px"><button class="ghost">Sign out</button></form></div></header>
<main>
<div class="stats">
 <div class="stat"><b>{{ stats.accounts }}</b><span>Accounts ({{ stats.verified }} verified)</span></div>
 <div class="stat"><b>{{ stats.meters }}</b><span>Meters</span></div>
 <div class="stat"><b>{{ stats.alerts_24h }}</b><span>Alerts in last 24 h</span></div>
 <div class="stat"><b>{{ stats.open_feedback }}</b><span>Open feedback</span></div>
 <div class="stat"><b class="{{ 'ok' if all_ok else 'bad' }}">{{ 'OK' if all_ok else 'CHECK' }}</b><span>System health</span></div>
</div>

<section><h2>System health</h2><table>
{% for h in health %}<tr><td class="{{ 'ok' if h.ok else 'bad' }}">{{ 'PASS' if h.ok else 'FAIL' }}</td><td>{{ h.label }}</td><td style="color:var(--muted)">{{ h.detail }}</td></tr>{% endfor %}
</table></section>

<section><h2>Accounts</h2>
{% if people %}<table><tr><th>Name</th><th>Email</th><th>Phone</th><th>Type</th><th>Meters</th><th>Status</th><th>Joined</th><th></th></tr>
{% for p in people %}<tr><td>{{ p.name }}</td><td>{{ p.email }}</td><td>{{ p.phone }}</td><td>{{ p.type }}</td><td>{{ p.meters }}</td>
<td>{% if p.disabled %}<span class="pill off">disabled</span>{% elif p.verified %}<span class="pill on">active</span>{% else %}<span class="pill">unverified</span>{% endif %}</td>
<td>{{ p.created }}</td>
<td><form method="post" action="/admin/accounts/{{ p.id }}/toggle"><button class="ghost">{{ 'Enable' if p.disabled else 'Disable' }}</button></form></td></tr>{% endfor %}
</table>{% else %}<p class="empty">No accounts yet.</p>{% endif %}</section>

<section><h2>Alerts sent (latest 50)</h2>
{% if alerts %}<table><tr><th>When (UTC)</th><th>Meter</th><th>Type</th><th>Message</th></tr>
{% for a in alerts %}<tr><td>{{ a.sent_at }}</td><td>{{ a.meter_number }}</td><td>{{ a.kind }}</td><td>{{ a.message }}</td></tr>{% endfor %}
</table>{% else %}<p class="empty">No alerts have been sent yet.</p>{% endif %}</section>

<section><h2>User feedback</h2>
{% if notes %}<table><tr><th>When</th><th>From</th><th>Type</th><th>Message</th><th></th></tr>
{% for n in notes %}<tr><td>{{ n.created_at }}</td><td>{{ n.email }}</td><td>{{ n.category }}</td><td>{{ n.message }}</td>
<td>{% if n.status == 'open' %}<form method="post" action="/admin/feedback/{{ n.id }}/resolve"><button class="ghost">Mark resolved</button></form>{% else %}<span class="pill on">resolved</span>{% endif %}</td></tr>{% endfor %}
</table>{% else %}<p class="empty">No feedback yet.</p>{% endif %}</section>
</main></body></html>"""
