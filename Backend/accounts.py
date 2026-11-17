"""
accounts.py - sign-up, login and meters for NextHome Energy.

Sign-up asks for a name, email, phone, meter number and a password (and, for a meter with no
usage history yet, three answers the server turns into an estimated history). Login is the meter
number OR the email, plus the password. Every meter number and every email belongs to one account.
There are no email codes. Passwords are stored hashed. After login the app sends the session token as
   Authorization: Bearer <token>.

Names, emails and phone numbers are typed into the app and stored in the database. None are in the code.
"""
import datetime
import hashlib
import re
import secrets

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_connection
import appliances_data

bp = Blueprint("accounts", __name__)

SESSION_DAYS = 30
ACCOUNT_TYPES = {"home", "work"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GENDERS = {"female", "male", "other", "prefer_not_to_say"}
METER_RE = re.compile(r"^[A-Za-z0-9]{4,13}$")
HISTORY_DAYS = 14          # how many days of estimated history a new customer's answers are turned into


def init_account_tables():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT,
            phone TEXT NOT NULL,
            dob TEXT,
            gender TEXT,
            account_type TEXT DEFAULT 'home',
            password_hash TEXT,
            verified INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS account_meters (
            account_id INTEGER NOT NULL,
            meter_number TEXT NOT NULL,
            nickname TEXT,
            PRIMARY KEY (account_id, meter_number)
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        );
    """)
    info = {r["name"]: r for r in conn.execute("PRAGMA table_info(accounts)")}
    if info["email"]["notnull"] or info["password_hash"]["notnull"]:
        # An older database needed an email and a password. Rebuild the table without those rules.
        conn.executescript("""
            ALTER TABLE accounts RENAME TO accounts_old;
            CREATE TABLE accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT,
                phone TEXT NOT NULL,
                dob TEXT,
                gender TEXT,
                account_type TEXT DEFAULT 'home',
                password_hash TEXT,
                verified INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO accounts (id, full_name, email, phone, dob, gender, account_type, password_hash, verified, created_at)
                SELECT id, full_name, email, phone, dob, gender, account_type, password_hash, 1, created_at FROM accounts_old;
        """)
        for extra in ("alert_threshold", "disabled"):
            if extra in info or extra in [r["name"] for r in conn.execute("PRAGMA table_info(accounts_old)")]:
                conn.execute(f"ALTER TABLE accounts ADD COLUMN {extra} " + ("REAL DEFAULT 100" if extra == "alert_threshold" else "INTEGER DEFAULT 0"))
                conn.execute(f"UPDATE accounts SET {extra} = (SELECT {extra} FROM accounts_old o WHERE o.id = accounts.id)")
        conn.execute("DROP TABLE accounts_old")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
    if "alert_threshold" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN alert_threshold REAL DEFAULT 100")
    if "disabled" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN disabled INTEGER DEFAULT 0")
    # a meter number may belong to only one account
    conn.execute("DELETE FROM account_meters WHERE rowid NOT IN "
                 "(SELECT MIN(rowid) FROM account_meters GROUP BY meter_number)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_owner_per_meter ON account_meters (meter_number)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_account_per_email ON accounts (email)")
    conn.commit()
    conn.close()


# ---------------- helpers ----------------

def _now():
    return datetime.datetime.utcnow()


def _iso(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalise_phone(raw):
    """07xx / 01xx / 2547xx / +2547xx -> +254XXXXXXXXX, or None if it is not a Kenyan mobile number."""
    digits = re.sub(r"[\s\-()]", "", raw or "")
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    return "+" + digits if re.fullmatch(r"254[17]\d{8}", digits) else None


def parse_threshold(raw):
    """KSh balance at or below which the user wants an SMS alert. Empty means the default of KSh 100."""
    if raw is None or str(raw).strip() == "":
        return 100.0, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "Alert level must be a number of shillings, e.g. 100"
    if not 0 <= value <= 10000:
        return None, "Alert level must be between KSh 0 and KSh 10,000"
    return value, None


def clean_meter(raw):
    meter = (raw or "").strip().upper()
    return meter if METER_RE.match(meter) else None


def new_session(conn, account_id):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?,?,?)",
                 (_hash(token), account_id, _iso(_now() + datetime.timedelta(days=SESSION_DAYS))))
    conn.commit()
    return token


def current_account(conn):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    return conn.execute(
        "SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND COALESCE(a.disabled, 0) = 0",
        (_hash(header[7:]), _iso(_now()))).fetchone()


def profile(conn, acct):
    meters = conn.execute("SELECT meter_number, nickname FROM account_meters WHERE account_id = ? "
                          "ORDER BY rowid", (acct["id"],)).fetchall()
    return {"id": acct["id"], "full_name": acct["full_name"], "email": acct["email"] or "",
            "phone": acct["phone"], "dob": acct["dob"], "gender": acct["gender"],
            "account_type": acct["account_type"],
            "alert_threshold": acct["alert_threshold"] if acct["alert_threshold"] is not None else 100.0,
            "meters": [{"meter_number": m["meter_number"], "nickname": m["nickname"]} for m in meters]}


def link_meter(conn, account_id, meter, nickname=None):
    conn.execute("INSERT OR IGNORE INTO meters (meter_number, balance) VALUES (?, 0.0)", (meter,))
    conn.execute("INSERT OR IGNORE INTO account_meters (account_id, meter_number, nickname) VALUES (?,?,?)",
                 (account_id, meter, nickname))


def owner_of(conn, meter):
    return conn.execute("SELECT account_id FROM account_meters WHERE meter_number = ?", (meter,)).fetchone()


def phones_for_meter(conn, meter):
    """Phone numbers of the accounts that own this meter (used for SMS alerts)."""
    return [r["phone"] for r in conn.execute(
        "SELECT a.phone FROM accounts a JOIN account_meters m ON m.account_id = a.id "
        "WHERE m.meter_number = ? AND COALESCE(a.disabled, 0) = 0", (meter,))]


def seed_from_answers(conn, meter, balance, topup, days):
    """
    A new customer tells us the balance now, the size of their last top-up and how many days it lasted.
    That is turned into HISTORY_DAYS days of estimated usage, so predictions work from the first day.
    The numbers are estimates, and the dashboard says so until real readings replace them.
    """
    daily_spend = topup / days
    rate = appliances_data.all_in_rate("DC2")
    kwh_day = min(40.0, max(0.5, daily_spend / rate))
    today = _now().date()
    conn.execute("DELETE FROM consumption_history WHERE meter_number = ?", (meter,))
    cumulative = 0.0
    for i in range(HISTORY_DAYS, -1, -1):          # oldest first, today last
        day = today - datetime.timedelta(days=i)
        cumulative += kwh_day if i < HISTORY_DAYS else 0.0
        conn.execute("INSERT INTO consumption_history (meter_number, balance, kwh_used, recorded_at) VALUES (?,?,?,?)",
                     (meter, round(balance + daily_spend * i, 2), round(cumulative, 3), f"{day} 23:59:00"))
    conn.execute("UPDATE meters SET balance = ?, kwh_used = ?, last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
                 (round(balance, 2), round(cumulative, 3), meter))


def history_count(conn, meter):
    return conn.execute("SELECT COUNT(*) AS n FROM consumption_history WHERE meter_number = ?", (meter,)).fetchone()["n"]


def number(d, key):
    """A non-negative number from the request, None if missing, False if it is not a number."""
    raw = d.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False
    return value if 0 <= value <= 1_000_000 else False


# ---------------- endpoints ----------------

@bp.route("/auth/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    who = (d.get("identifier") or d.get("meter_number") or d.get("email") or "").strip()
    password = d.get("password") or ""
    if not who or not password:
        return jsonify({"error": "Enter your meter number or email, and your password"}), 400
    conn = get_connection()
    try:
        if "@" in who:
            acct = conn.execute("SELECT * FROM accounts WHERE email = ?", (who.lower(),)).fetchone()
        else:
            owner = owner_of(conn, who.upper())
            acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (owner["account_id"],)).fetchone() if owner else None
        if not acct or not acct["password_hash"] or not check_password_hash(acct["password_hash"], password):
            return jsonify({"error": "Wrong meter number, email or password"}), 401
        if acct["disabled"]:
            return jsonify({"error": "This account has been disabled. Please contact support."}), 403
        return jsonify({"token": new_session(conn, acct["id"]), "user": profile(conn, acct)}), 200
    finally:
        conn.close()


@bp.route("/auth/register", methods=["POST"])
def register():
    d = request.get_json(silent=True) or {}
    meter = clean_meter(d.get("meter_number"))
    name = (d.get("full_name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    phone = normalise_phone(d.get("phone"))
    password = d.get("password") or ""
    gender = (d.get("gender") or "prefer_not_to_say").lower()
    dob = (d.get("dob") or "").strip()
    account_type = (d.get("account_type") or "home").lower()
    threshold, terr = parse_threshold(d.get("alert_threshold"))
    balance, topup, days = number(d, "current_balance"), number(d, "last_topup_ksh"), number(d, "last_topup_days")
    if len(name) < 2:
        return jsonify({"error": "Please enter your full name"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address"}), 400
    if not phone:
        return jsonify({"error": "Enter a Kenyan phone number, e.g. 0712345678"}), 400
    if not meter:
        return jsonify({"error": "Enter your meter number (letters and digits only)"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if gender not in GENDERS:
        return jsonify({"error": "Invalid gender"}), 400
    if account_type not in ACCOUNT_TYPES:
        return jsonify({"error": "Account type must be home or work"}), 400
    if dob:
        try:
            born = datetime.date.fromisoformat(dob)
        except ValueError:
            return jsonify({"error": "Date of birth must look like 1999-04-27"}), 400
        if not (16 <= (datetime.date.today() - born).days / 365.25 <= 110):
            return jsonify({"error": "Please check your date of birth"}), 400
    if terr:
        return jsonify({"error": terr}), 400
    if False in (balance, topup, days):
        return jsonify({"error": "Balance, top-up and days must be numbers"}), 400
    if topup is not None and (balance is None or not days or days < 1 or days > 90):
        return jsonify({"error": "Give your balance now, your last top-up and how many days (1 to 90) it lasted"}), 400
    conn = get_connection()
    try:
        if owner_of(conn, meter):
            return jsonify({"error": "This meter number already has an account. Please log in."}), 409
        if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            return jsonify({"error": "An account with this email already exists. Please log in."}), 409
        has_history = history_count(conn, meter) >= 7
        if not has_history and topup is None:
            return jsonify({"error": "This meter is new. Please answer the three usage questions.",
                            "needs_history": True}), 422
        acct_id = conn.execute(
            "INSERT INTO accounts (full_name, email, phone, dob, gender, account_type, password_hash, alert_threshold) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (name, email, phone, dob or None, gender, account_type, generate_password_hash(password), threshold)).lastrowid
        link_meter(conn, acct_id, meter, "My home" if account_type == "home" else "My workplace")
        seeded = False
        if not has_history:
            seed_from_answers(conn, meter, balance, topup, days)
            seeded = True
        conn.commit()
        acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (acct_id,)).fetchone()
        return jsonify({"message": "Account created", "token": new_session(conn, acct_id),
                        "user": profile(conn, acct), "history_estimated": seeded}), 201
    finally:
        conn.close()


@bp.route("/me", methods=["GET", "PUT"])
def me():
    conn = get_connection()
    try:
        acct = current_account(conn)
        if not acct:
            return jsonify({"error": "Please log in again"}), 401
        if request.method == "PUT":
            d = request.get_json(silent=True) or {}
            phone = normalise_phone(d["phone"]) if d.get("phone") else acct["phone"]
            name = (d.get("full_name") or acct["full_name"]).strip()
            if not phone:
                return jsonify({"error": "Enter a Kenyan phone number, e.g. 0712345678"}), 400
            threshold = acct["alert_threshold"] if acct["alert_threshold"] is not None else 100.0
            if d.get("alert_threshold") is not None:
                threshold, terr = parse_threshold(d.get("alert_threshold"))
                if terr:
                    return jsonify({"error": terr}), 400
            conn.execute("UPDATE accounts SET phone = ?, full_name = ?, alert_threshold = ? WHERE id = ?",
                         (phone, name, threshold, acct["id"]))
            conn.commit()
            acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (acct["id"],)).fetchone()
        return jsonify(profile(conn, acct)), 200
    finally:
        conn.close()


@bp.route("/me/meters", methods=["POST"])
def add_meter():
    d = request.get_json(silent=True) or {}
    meter = clean_meter(d.get("meter_number"))
    if not meter:
        return jsonify({"error": "Enter your meter number (letters and digits only)"}), 400
    conn = get_connection()
    try:
        acct = current_account(conn)
        if not acct:
            return jsonify({"error": "Please log in again"}), 401
        owner = owner_of(conn, meter)
        if owner and owner["account_id"] != acct["id"]:
            return jsonify({"error": "That meter number belongs to another account"}), 409
        link_meter(conn, acct["id"], meter, (d.get("nickname") or "").strip() or None)
        conn.commit()
        return jsonify(profile(conn, acct)), 200
    finally:
        conn.close()