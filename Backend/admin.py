"""
admin.py - the NextHome Energy administrator console (a web page served by the same Flask server).

Open   http://localhost:5000/admin   (or  https://<your-site>/admin  once hosted)
Sign in with the admin EMAIL and PASSWORD.

Tabs
  Overview  - key numbers and charts (sign-ups, alerts, account types, account status)
  Accounts  - every user and their meters; search; disable / enable an account
  Meters    - every meter with tier, balance, daily use, time left; charts of daily use
  Alerts    - SMS alerts the system sent, with a chart by type
  Feedback  - messages users sent from the app; mark resolved
  System    - health checks, delivery mode, version, uptime, and the Solar demo panel

Also here:  POST /feedback  (the app sends feedback with the user's login token)
            GET  /version   (app and server version, used for upgrade checks)

Environment:  ADMIN_EMAIL     default admin@nexthome.test
              ADMIN_PASSWORD  default admin123   (change it for any real use)
"""
import datetime
import hmac
import html
import os
import secrets
import time

from flask import Blueprint, jsonify, make_response, redirect, render_template_string, request

from database import get_connection
import accounts

bp = Blueprint("admin", __name__)

APP_VERSION = "1.5.0"
STARTED = time.time()
COOKIE = "nh_admin"
_sessions = {}            # admin session tokens -> expiry time (kept in memory; a restart signs the admin out)
_fails = {"count": 0, "until": 0.0}
_helpers = {}

PALETTE = ["#F5B301", "#2DD4BF", "#A78BFA", "#FB7185", "#38BDF8", "#34D399", "#FB923C"]

# Demo scenarios for the solar-and-grid switching panel: (generation kW, home load kW, battery %)
SOLAR_SCENARIOS = {
    "sunny":   ("Sunny midday",              1.8, 0.6, 85.0),
    "cloudy":  ("Cloudy afternoon",          0.5, 1.2, 60.0),
    "evening": ("Evening, on battery",       0.0, 1.5, 45.0),
    "night":   ("Night, battery low",        0.0, 1.1, 15.0),
    "heavy":   ("Heavy load, weak sun",      0.6, 2.4, 12.0),
    "live":    ("Run live (follow the real sun)", 0.0, 0.0, 0.0),
}


def admin_email():
    return os.environ.get("ADMIN_EMAIL", "admin@nexthome.test").strip().lower()


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "admin123")


def init(billing_info=None, daily_kwh_series=None):
    _helpers.update(billing_info=billing_info, daily_kwh_series=daily_kwh_series)
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
        print(f"NOTE: the admin console uses the default login {admin_email()} / admin123. "
              "Set ADMIN_EMAIL and ADMIN_PASSWORD to change it.")


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
                     (acct["id"], acct["full_name"], category, message[:1000]))
        conn.commit()
        return jsonify({"message": "Thank you - your feedback was sent"}), 201
    finally:
        conn.close()


# ---------------- tiny SVG chart helpers (no internet or libraries needed) ----------------

def _esc(x):
    return html.escape(str(x), quote=True)


def _nice_max(v):
    if v <= 0:
        return 1.0
    for step in (1, 2, 4, 8, 12, 20, 40, 80, 100, 200, 400, 800, 1000, 2000, 4000, 8000):
        if v <= step:
            return float(step)
    return float(v)


def bar_chart(labels, values, color="#F5B301", width=520, height=210, unit=""):
    if not labels or not any(values):
        return '<p class="empty">No data yet.</p>'
    left, bottom, top = 34, 30, 14
    ph, pw = height - bottom - top, width - left - 8
    vmax = _nice_max(max(values))
    n = len(values)
    slot = pw / n
    bw = max(4, slot * 0.62)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for g in range(0, 5):
        y = top + ph - ph * g / 4
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - 6}" y2="{y:.1f}" class="grid"/>')
        label = vmax * g / 4
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{label:g}</text>')
    step = max(1, n // 7)
    for k, (lab, val) in enumerate(zip(labels, values)):
        h = ph * (val / vmax)
        x = left + k * slot + (slot - bw) / 2
        out.append(f'<rect x="{x:.1f}" y="{top + ph - h:.1f}" width="{bw:.1f}" height="{max(h, 0):.1f}" rx="4" '
                   f'fill="{color}"><title>{_esc(lab)}: {val:g}{_esc(unit)}</title></rect>')
        if k % step == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height - 10}" class="axis" text-anchor="middle">{_esc(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


def line_chart(labels, series, width=520, height=220, unit=""):
    """series = [(name, color, values)]"""
    allv = [v for _, _, vals in series for v in vals]
    if not labels or not allv or not any(allv):
        return '<p class="empty">No data yet.</p>'
    left, bottom, top = 34, 30, 14
    ph, pw = height - bottom - top, width - left - 10
    vmax = _nice_max(max(allv))
    n = len(labels)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for g in range(0, 5):
        y = top + ph - ph * g / 4
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - 6}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{left - 6}" y="{y + 4:.1f}" class="axis" text-anchor="end">{vmax * g / 4:g}</text>')
    xs = [left + (pw * k / max(1, n - 1)) for k in range(n)]
    step = max(1, n // 6)
    for k in range(0, n, step):
        out.append(f'<text x="{xs[k]:.1f}" y="{height - 10}" class="axis" text-anchor="middle">{_esc(labels[k])}</text>')
    for name, color, vals in series:
        pts = " ".join(f"{xs[k]:.1f},{top + ph - ph * (v / vmax):.1f}" for k, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5" '
                   f'stroke-linejoin="round" stroke-linecap="round"><title>{_esc(name)}</title></polyline>')
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{c}"></i>{_esc(nm)}</span>' for nm, c, _ in series)
    return "".join(out) + f'<div class="legend">{legend}</div>'


def donut(parts, size=170):
    """parts = [(label, value, color)]"""
    total = sum(v for _, v, _ in parts)
    if total <= 0:
        return '<p class="empty">No data yet.</p>'
    r, cx = 60, size / 2
    circ = 2 * 3.14159265 * r
    out = [f'<svg viewBox="0 0 {size} {size}" class="donut" role="img">'
           f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="var(--tile)" stroke-width="22"/>']
    offset = 0.0
    for label, v, color in parts:
        if v <= 0:
            continue
        length = circ * v / total
        out.append(f'<circle cx="{cx}" cy="{cx}" r="{r}" fill="none" stroke="{color}" stroke-width="22" '
                   f'stroke-dasharray="{length:.2f} {circ - length:.2f}" stroke-dashoffset="{-offset:.2f}" '
                   f'transform="rotate(-90 {cx} {cx})"><title>{_esc(label)}: {v}</title></circle>')
        offset += length
    out.append(f'<text x="{cx}" y="{cx + 7}" text-anchor="middle" class="donut-n">{total}</text></svg>')
    legend = "".join(f'<span class="key"><i style="background:{c}"></i>{_esc(l)} ({v})</span>' for l, v, c in parts if v > 0)
    return f'<div class="donut-wrap">{"".join(out)}<div class="legend col">{legend}</div></div>'


# ---------------- data for the console ----------------

def _day_labels(n):
    today = datetime.datetime.utcnow().date()
    days = [today - datetime.timedelta(days=n - 1 - k) for k in range(n)]
    return [d.isoformat() for d in days], [d.strftime("%d %b") for d in days]


def _counts_per_day(conn, table, column, n=14):
    keys, labels = _day_labels(n)
    rows = conn.execute(f"SELECT substr({column}, 1, 10) AS d, COUNT(*) AS c FROM {table} "
                        f"WHERE substr({column}, 1, 10) >= ? GROUP BY d", (keys[0],)).fetchall()
    have = {r["d"]: r["c"] for r in rows}
    return labels, [have.get(k, 0) for k in keys]


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
    sms_real = bool(os.environ.get("AT_USERNAME") and os.environ.get("AT_API_KEY"))
    add("SMS delivery", True, "real (Africa's Talking)" if sms_real else "simulated - alerts are logged, not sent")
    add("Login", True, "meter number or email + password (no email codes)")
    return rows


def _meter_rows(conn):
    billing, series_fn = _helpers.get("billing_info"), _helpers.get("daily_kwh_series")
    out = []
    for m in conn.execute("SELECT meter_number, balance FROM meters ORDER BY meter_number"):
        number = m["meter_number"]
        owners = conn.execute("SELECT COUNT(*) AS n FROM account_meters WHERE meter_number = ?", (number,)).fetchone()["n"]
        solar = conn.execute("SELECT battery_percent, generation_kw, load_kw, last_updated FROM solar_state "
                             "WHERE meter_number = ? ORDER BY id DESC LIMIT 1", (number,)).fetchone()
        info = billing(conn, number) if billing else {}
        series = series_fn(conn, number) if series_fn else []
        avg, rate = info.get("avg_daily_kwh"), info.get("rate")
        hours = (m["balance"] / (avg * rate) * 24) if avg and rate else None
        out.append({"number": number, "owners": owners, "balance": m["balance"], "tier": info.get("tier", "--"),
                    "rate": rate, "avg_daily": avg, "history_days": info.get("history_days", 0),
                    "hours_left": hours, "solar": dict(solar) if solar else None, "series": series})
    return out


def _context():
    conn = get_connection()
    try:
        since = (datetime.datetime.utcnow() - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        one = lambda q, *a: conn.execute(q, a).fetchone()["n"]  # noqa: E731
        stats = {
            "accounts": one("SELECT COUNT(*) AS n FROM accounts"),
            "disabled": one("SELECT COUNT(*) AS n FROM accounts WHERE COALESCE(disabled,0) = 1"),
            "meters": one("SELECT COUNT(*) AS n FROM meters"),
            "alerts_24h": one("SELECT COUNT(*) AS n FROM alert_log WHERE sent_at > ?", since),
            "open_feedback": one("SELECT COUNT(*) AS n FROM feedback WHERE status = 'open'"),
        }
        people = []
        for a in conn.execute("SELECT * FROM accounts ORDER BY id DESC"):
            ms = [r["meter_number"] for r in conn.execute(
                "SELECT meter_number FROM account_meters WHERE account_id = ? ORDER BY rowid", (a["id"],))]
            people.append({"id": a["id"], "name": a["full_name"], "email": a["email"] or "-", "phone": a["phone"],
                           "type": a["account_type"], "disabled": bool(a["disabled"]), "created": a["created_at"],
                           "threshold": a["alert_threshold"], "meters": ", ".join(ms) or "-"})
        alerts = [dict(r) for r in conn.execute(
            "SELECT meter_number, kind, message, sent_at FROM alert_log ORDER BY id DESC LIMIT 50")]
        notes = [dict(r) for r in conn.execute("SELECT * FROM feedback ORDER BY (status = 'open') DESC, id DESC LIMIT 50")]
        health = _health(conn)

        # charts
        sl, sv = _counts_per_day(conn, "accounts", "created_at")
        al, av = _counts_per_day(conn, "alert_log", "sent_at")
        types = {}
        for p in people:
            types[p["type"] or "home"] = types.get(p["type"] or "home", 0) + 1
        kinds = {}
        for a in alerts:
            k = "threshold" if a["kind"].startswith("threshold") else a["kind"]
            kinds[k] = kinds.get(k, 0) + 1
        fb_kinds = {}
        for n in notes:
            fb_kinds[n["category"] or "other"] = fb_kinds.get(n["category"] or "other", 0) + 1
        meters = _meter_rows(conn)
    finally:
        conn.close()

    # 30-day usage lines for up to 5 meters with history
    with_hist = [m for m in meters if m["series"]][:5]
    line_labels, line_series = [], []
    if with_hist:
        longest = max(with_hist, key=lambda m: len(m["series"]))
        tail = longest["series"][-30:]
        line_labels = [d[5:] for d, _ in tail]
        for idx, m in enumerate(with_hist):
            values = [k for _, k in m["series"][-30:]]
            values = [0.0] * (len(line_labels) - len(values)) + values
            line_series.append((m["number"], PALETTE[idx % len(PALETTE)], [round(v, 2) for v in values]))

    charts = {
        "signups": bar_chart(sl, sv, "#F5B301"),
        "alerts_day": bar_chart(al, av, "#FB7185"),
        "types": donut([("Home", types.get("home", 0), "#F5B301"), ("Work", types.get("work", 0), "#38BDF8")]),
        "status": donut([("Active", stats["accounts"] - stats["disabled"], "#34D399"),
                         ("Disabled", stats["disabled"], "#A78BFA"),
                         ("Disabled", stats["disabled"], "#FB7185")]),
        "alert_kinds": donut([(k.replace("_", " ").title(), v, PALETTE[i % len(PALETTE)])
                              for i, (k, v) in enumerate(sorted(kinds.items()))]),
        "feedback": donut([(k.title(), v, PALETTE[i % len(PALETTE)]) for i, (k, v) in enumerate(sorted(fb_kinds.items()))]),
        "meter_avg": bar_chart([m["number"] for m in meters],
                               [m["avg_daily"] or 0 for m in meters], "#2DD4BF", unit=" kWh/day"),
        "usage": line_chart(line_labels, line_series, unit=" kWh"),
    }
    up = int(time.time() - STARTED)
    return dict(stats=stats, people=people, alerts=alerts, notes=notes, health=health, meters=meters, charts=charts,
                version=APP_VERSION, uptime=f"{up // 3600}h {(up % 3600) // 60}m",
                all_ok=all(h["ok"] for h in health), scenarios=SOLAR_SCENARIOS,
                solar_meters=[m["number"] for m in meters if m["solar"]] or ["M001"],
                all_meters=[m["number"] for m in meters],
                admin=admin_email())


# ---------------- pages ----------------

@bp.route("/admin/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if time.time() < _fails["until"]:
            error = "Too many wrong attempts. Wait a minute and try again."
        else:
            email_ok = hmac.compare_digest((request.form.get("email") or "").strip().lower().encode(), admin_email().encode())
            pw_ok = hmac.compare_digest((request.form.get("password") or "").encode(), admin_password().encode())
            if email_ok and pw_ok:
                _fails["count"] = 0
                token = secrets.token_urlsafe(32)
                _sessions[token] = time.time() + 8 * 3600
                resp = make_response(redirect("/admin"))
                resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", max_age=8 * 3600)
                return resp
            _fails["count"] += 1
            if _fails["count"] >= 5:
                _fails["count"], _fails["until"] = 0, time.time() + 60
            error = "Wrong email or password."
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
    return render_template_string(DASH_HTML, css=CSS, **_context())


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
    return redirect("/admin#accounts")


@bp.route("/admin/feedback/<int:note_id>/resolve", methods=["POST"])
def resolve_feedback(note_id):
    if not _signed_in():
        return redirect("/admin/login")
    _post_action("UPDATE feedback SET status = 'resolved' WHERE id = ?", note_id)
    return redirect("/admin#feedback")


@bp.route("/admin/load/<action>", methods=["POST"])
def load_demo(action):
    """Demo panel: switch a heavy appliance on or off on the simulated meter, so usage visibly goes up or down."""
    if not _signed_in():
        return redirect("/admin/login")
    meter = (request.form.get("meter") or "M001").strip().upper()
    if action not in ("on", "off"):
        return jsonify({"error": "Unknown action"}), 404
    watts = 3000.0 if action == "on" else 0.0
    until = time.time() + 15 * 60 if action == "on" else None      # a switched-on heater stays on for 15 minutes
    _post_action("UPDATE meters SET boost_w = ?, boost_until = ? WHERE meter_number = ?", watts, until, meter)
    return redirect("/admin#system")


@bp.route("/admin/solar/<scenario>", methods=["POST"])
def solar_scenario(scenario):
    """Demo panel: set a meter's solar state to a ready-made situation so the app shows solar / battery / grid switching."""
    if not _signed_in():
        return redirect("/admin/login")
    if scenario not in SOLAR_SCENARIOS:
        return jsonify({"error": "Unknown scenario"}), 404
    meter = (request.form.get("meter") or "M001").strip().upper()
    _, gen, load, battery = SOLAR_SCENARIOS[scenario]
    conn = get_connection()
    try:
        if not conn.execute("SELECT 1 FROM meters WHERE meter_number = ?", (meter,)).fetchone():
            return redirect("/admin#system")
        if scenario == "live":              # let the built-in solar simulation take over again
            conn.execute("UPDATE meters SET sol_hold_until = NULL, sol_ext_at = NULL WHERE meter_number = ?", (meter,))
            conn.commit()
            return redirect("/admin#system")
        conn.execute("UPDATE meters SET sol_hold_until = ? WHERE meter_number = ?", (time.time() + 600, meter))   # hold the scenario for 10 minutes
        existing = conn.execute("SELECT id, solar_kwh_today FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1",
                                (meter,)).fetchone()
        if existing:
            conn.execute("UPDATE solar_state SET battery_percent=?, generation_kw=?, load_kw=?, last_updated=CURRENT_TIMESTAMP "
                         "WHERE id=?", (battery, gen, load, existing["id"]))
        else:
            conn.execute("INSERT INTO solar_state (meter_number, battery_percent, generation_kw, load_kw, solar_kwh_today) "
                         "VALUES (?,?,?,?,?)", (meter, battery, gen, load, 0.0))
        conn.commit()
    finally:
        conn.close()
    return redirect("/admin#system")


# ---------------- page templates ----------------

CSS = """
:root{--bg:#0B1220;--card:#121C30;--tile:#1B2740;--text:#F8FAFC;--muted:#9AA8BD;--gold:#F5B301;--ok:#34D399;--bad:#FB7185;--teal:#2DD4BF;--violet:#A78BFA}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
header{background:linear-gradient(120deg,#0B1220 0%,#0F2A66 60%,#3b2a7a 100%);padding:16px 24px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
header h1{margin:0;font-size:20px}header h1 span{color:var(--gold)}header small{color:var(--muted)}
nav{display:flex;gap:6px;flex-wrap:wrap;padding:12px 16px 0;max-width:1150px;margin:0 auto}
nav button{background:var(--card);color:var(--muted);border:1px solid var(--tile);border-radius:99px;padding:8px 16px;font-weight:600;cursor:pointer;font-size:14px}
nav button.on{background:var(--gold);color:#0B1220;border-color:var(--gold)}
main{max-width:1150px;margin:0 auto;padding:16px 16px 60px}
.tab{display:none}.tab.on{display:block}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.stat{background:var(--card);border-radius:16px;padding:14px 16px;border-top:3px solid var(--gold)}
.stat:nth-child(2){border-color:var(--teal)}.stat:nth-child(3){border-color:var(--violet)}.stat:nth-child(4){border-color:#FB7185}.stat:nth-child(5){border-color:#38BDF8}
.stat b{display:block;font-size:28px}.stat span{color:var(--muted);font-size:13px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:16px}
section,.card{background:var(--card);border-radius:16px;padding:16px;margin-bottom:16px;overflow-x:auto}
h2{margin:0 0 12px;font-size:17px}h3{margin:0 0 8px;font-size:15px;color:var(--muted);font-weight:600}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--tile);vertical-align:top}th{color:var(--muted);font-weight:600}
.ok{color:var(--ok)}.bad{color:var(--bad)}.pill{display:inline-block;padding:1px 9px;border-radius:99px;background:var(--tile);font-size:12px}
.pill.off{background:#3b1d24;color:var(--bad)}.pill.on{background:#12352b;color:var(--ok)}.pill.t1{background:#12352b;color:var(--ok)}.pill.t2{background:#3a2f0b;color:var(--gold)}.pill.t3{background:#3b1d24;color:var(--bad)}
button,.btn{background:var(--gold);color:#0B1220;border:0;border-radius:8px;padding:6px 12px;font-weight:700;cursor:pointer}
button.ghost{background:var(--tile);color:var(--text)}
input[type=password],input[type=email],input[type=text],select{width:100%;padding:12px;border-radius:10px;border:1px solid var(--tile);background:var(--bg);color:var(--text);font-size:16px}
.login{max-width:380px;margin:10vh auto;background:var(--card);padding:26px;border-radius:18px}.empty{color:var(--muted)}
.chart{width:100%;height:auto}.grid{stroke:var(--tile);stroke-width:1}.axis{fill:var(--muted);font-size:10px}
.donut{width:150px;height:150px;flex:none}.donut-n{fill:var(--text);font-size:22px;font-weight:700}
.donut-wrap{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:6px;color:var(--muted);font-size:13px}.legend.col{flex-direction:column;gap:6px;margin:0}
.key i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.filter{max-width:320px;margin-bottom:12px}
.scen{display:flex;gap:8px;flex-wrap:wrap}.scen button{background:var(--tile);color:var(--text)}.scen button:hover{background:var(--gold);color:#0B1220}
.note{color:var(--muted);font-size:13px}
"""

LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NextHome Admin</title><style>{{ css|safe }}</style></head><body>
<form class="login" method="post"><h1 style="margin-top:0">NextHome <span style="color:var(--gold)">Admin</span></h1>
<p style="color:var(--muted)">Administrator sign-in</p>
{% if error %}<p class="bad">{{ error }}</p>{% endif %}
<p><input type="email" name="email" placeholder="Admin email" autofocus required></p>
<p><input type="password" name="password" placeholder="Password" required></p>
<p><button type="submit" style="width:100%;padding:12px">Sign in</button></p></form></body></html>"""

DASH_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NextHome Admin</title><style>{{ css|safe }}</style></head><body>
<header><h1>NextHome <span>Admin</span></h1>
<div><small>{{ admin }} &middot; v{{ version }} &middot; up {{ uptime }}</small>
<form method="post" action="/admin/logout" style="display:inline;margin-left:12px"><button class="ghost">Sign out</button></form></div></header>
<nav id="tabs">
 <button data-t="overview">Overview</button><button data-t="accounts">Accounts</button><button data-t="meters">Meters</button>
 <button data-t="alerts">Alerts</button><button data-t="feedback">Feedback{% if stats.open_feedback %} ({{ stats.open_feedback }}){% endif %}</button><button data-t="system">System</button>
</nav>
<main>

<div class="tab" id="overview">
 <div class="stats">
  <div class="stat"><b>{{ stats.accounts }}</b><span>Accounts</span></div>
  <div class="stat"><b>{{ stats.meters }}</b><span>Meters</span></div>
  <div class="stat"><b>{{ stats.alerts_24h }}</b><span>Alerts in last 24 h</span></div>
  <div class="stat"><b>{{ stats.open_feedback }}</b><span>Open feedback</span></div>
  <div class="stat"><b class="{{ 'ok' if all_ok else 'bad' }}">{{ 'OK' if all_ok else 'CHECK' }}</b><span>System health</span></div>
 </div>
 <div class="grid2">
  <div class="card"><h3>New accounts, last 14 days</h3>{{ charts.signups|safe }}</div>
  <div class="card"><h3>Alerts sent, last 14 days</h3>{{ charts.alerts_day|safe }}</div>
  <div class="card"><h3>Accounts by type</h3>{{ charts.types|safe }}</div>
  <div class="card"><h3>Account status</h3>{{ charts.status|safe }}</div>
 </div>
</div>

<div class="tab" id="accounts">
 <section><h2>Accounts</h2>
 <input class="filter" type="text" placeholder="Search name, email, phone or meter" oninput="flt(this,'ptable')">
 {% if people %}<table id="ptable"><tr><th>Name</th><th>Email</th><th>Phone</th><th>Type</th><th>Meters</th><th>Alert level</th><th>Status</th><th>Joined</th><th></th></tr>
 {% for p in people %}<tr><td>{{ p.name }}</td><td>{{ p.email }}</td><td>{{ p.phone }}</td><td>{{ p.type }}</td><td>{{ p.meters }}</td>
 <td>{% if p.threshold is not none %}KSh {{ '%.0f'|format(p.threshold) }}{% else %}-{% endif %}</td>
 <td>{% if p.disabled %}<span class="pill off">disabled</span>{% else %}<span class="pill on">active</span>{% endif %}</td>
 <td>{{ p.created }}</td>
 <td><form method="post" action="/admin/accounts/{{ p.id }}/toggle"><button class="ghost">{{ 'Enable' if p.disabled else 'Disable' }}</button></form></td></tr>{% endfor %}
 </table>{% else %}<p class="empty">No accounts yet.</p>{% endif %}</section>
</div>

<div class="tab" id="meters">
 <div class="grid2">
  <div class="card"><h3>Average daily use per meter (kWh/day)</h3>{{ charts.meter_avg|safe }}</div>
  <div class="card"><h3>Daily use, last 30 days (kWh)</h3>{{ charts.usage|safe }}</div>
 </div>
 <section><h2>Meters</h2>
 <input class="filter" type="text" placeholder="Search meter number" oninput="flt(this,'mtable')">
 <table id="mtable"><tr><th>Meter</th><th>Owners</th><th>Tier</th><th>KSh/unit</th><th>Balance (KSh)</th><th>Avg kWh/day</th><th>Time left</th><th>History</th><th>Solar</th></tr>
 {% for m in meters %}<tr><td><b>{{ m.number }}</b></td><td>{{ m.owners }}</td>
 <td><span class="pill {{ 't1' if m.tier == 'DC1' else 't2' if m.tier == 'DC2' else 't3' }}">{{ m.tier }}</span></td>
 <td>{{ m.rate if m.rate else '-' }}</td><td>{{ '%.2f'|format(m.balance) }}</td>
 <td>{{ m.avg_daily if m.avg_daily else '-' }}</td>
 <td>{% if m.hours_left is not none %}{% if m.hours_left >= 48 %}{{ '%.0f'|format(m.hours_left / 24) }} days{% else %}{{ '%.0f'|format(m.hours_left) }} h{% endif %}{% else %}-{% endif %}</td>
 <td>{{ m.history_days }} days</td>
 <td>{% if m.solar %}<span class="pill on">{{ '%.0f'|format(m.solar.battery_percent) }}% battery</span>{% else %}<span class="pill">grid only</span>{% endif %}</td></tr>{% endfor %}
 </table></section>
</div>

<div class="tab" id="alerts">
 <div class="grid2"><div class="card"><h3>Alerts by type</h3>{{ charts.alert_kinds|safe }}</div></div>
 <section><h2>Alerts sent (latest 50)</h2>
 {% if alerts %}<table><tr><th>When (UTC)</th><th>Meter</th><th>Type</th><th>Message</th></tr>
 {% for a in alerts %}<tr><td>{{ a.sent_at }}</td><td>{{ a.meter_number }}</td><td>{{ 'threshold' if a.kind.startswith('threshold') else a.kind }}</td><td>{{ a.message }}</td></tr>{% endfor %}
 </table>{% else %}<p class="empty">No alerts have been sent yet.</p>{% endif %}</section>
</div>

<div class="tab" id="feedback">
 <div class="grid2"><div class="card"><h3>Feedback by type</h3>{{ charts.feedback|safe }}</div></div>
 <section><h2>User feedback</h2>
 {% if notes %}<table><tr><th>When</th><th>From</th><th>Type</th><th>Message</th><th></th></tr>
 {% for n in notes %}<tr><td>{{ n.created_at }}</td><td>{{ n.email }}</td><td>{{ n.category }}</td><td>{{ n.message }}</td>
 <td>{% if n.status == 'open' %}<form method="post" action="/admin/feedback/{{ n.id }}/resolve"><button class="ghost">Mark resolved</button></form>{% else %}<span class="pill on">resolved</span>{% endif %}</td></tr>{% endfor %}
 </table>{% else %}<p class="empty">No feedback yet.</p>{% endif %}</section>
</div>

<div class="tab" id="system">
 <section><h2>System health</h2><table>
 {% for h in health %}<tr><td class="{{ 'ok' if h.ok else 'bad' }}">{{ 'PASS' if h.ok else 'FAIL' }}</td><td>{{ h.label }}</td><td style="color:var(--muted)">{{ h.detail }}</td></tr>{% endfor %}
 <tr><td class="ok">INFO</td><td>Version</td><td style="color:var(--muted)">{{ version }} (up {{ uptime }})</td></tr>
 </table></section>
 <section><h2>Simulated meter demo panel</h2>
 <p class="note">The meter is simulated inside the server, so the balance keeps falling by itself. Switch on a 3 kW heater (15 minutes) to make usage jump,
 then open the app: the alert "You are using ... kW right now" appears. Switch it off to bring usage back down.</p>
 <form method="post" class="scen">
  <select name="meter" style="width:auto">{% for s in all_meters %}<option>{{ s }}</option>{% endfor %}</select>
  <button type="submit" formaction="/admin/load/on">Switch on heater (+3 kW)</button>
  <button type="submit" formaction="/admin/load/off">Switch it off</button>
 </form></section>
 <section><h2>Solar demo panel</h2>
 <p class="note">Sets a solar home to a ready-made situation for 10 minutes. Open the app (or the page /solar/M001) and watch the "Where your power comes from" bar change within a few seconds:
 solar first, then the battery, and the Kenya Power grid only when both fall short. "Run live" lets the simulated sun and battery take over again.</p>
 <form method="post" class="scen" id="scenform">
  <select name="meter" style="width:auto">{% for s in solar_meters %}<option>{{ s }}</option>{% endfor %}</select>
  {% for key, s in scenarios.items() %}<button type="submit" formaction="/admin/solar/{{ key }}">{{ s[0] }}</button>{% endfor %}
 </form></section>
</div>

</main>
<script>
function show(t){document.querySelectorAll('.tab').forEach(e=>e.classList.toggle('on',e.id===t));
 document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));history.replaceState(null,'','#'+t);}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>show(b.dataset.t));
function flt(inp,id){const q=inp.value.toLowerCase();document.querySelectorAll('#'+id+' tr').forEach((r,i)=>{if(i)r.style.display=r.innerText.toLowerCase().includes(q)?'':'none'});}
show((location.hash||'#overview').slice(1)||'overview');
</script>
</body></html>"""