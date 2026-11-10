"""
accounts.py - registration, email verification, login and meters for NextHome Energy.

Passwords are stored hashed (never in plain text). Verification codes are stored hashed too,
expire after 10 minutes and allow 5 wrong tries. After login the app sends the session token
as   Authorization: Bearer <token>.

Environment:  DEV_MODE=1  -> the register response also contains the code ("dev_code") and the
                             /dev/create_verified_user helper works. Leave it OFF on the cloud.
"""
import datetime
import hashlib
import os
import re
import secrets

from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_connection
import notify

bp = Blueprint("accounts", __name__)

CODE_MINUTES, MAX_TRIES, RESEND_SECONDS, SESSION_DAYS = 10, 5, 30, 30
GENDERS = {"female", "male", "other", "prefer_not_to_say"}
ACCOUNT_TYPES = {"home", "work"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
METER_RE = re.compile(r"^[A-Za-z0-9]{4,13}$")


def dev_mode():
    return os.environ.get("DEV_MODE") == "1"


def init_account_tables():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT NOT NULL,
            dob TEXT,
            gender TEXT,
            account_type TEXT DEFAULT 'home',
            password_hash TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS account_meters (
            account_id INTEGER NOT NULL,
            meter_number TEXT NOT NULL,
            nickname TEXT,
            PRIMARY KEY (account_id, meter_number)
        );
        CREATE TABLE IF NOT EXISTS verification_codes (
            email TEXT PRIMARY KEY,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            tries INTEGER DEFAULT 0,
            sent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        );
    """)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)")]
    if "alert_threshold" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN alert_threshold REAL DEFAULT 100")
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


def validate_profile(d, need_password=True):
    """Returns (clean_dict, error_message)."""
    name = (d.get("full_name") or "").strip()
    email = (d.get("email") or "").strip().lower()
    phone = normalise_phone(d.get("phone"))
    gender = (d.get("gender") or "prefer_not_to_say").lower()
    account_type = (d.get("account_type") or "home").lower()
    dob = (d.get("dob") or "").strip()
    meter = (d.get("meter_number") or "").strip().upper()
    password = d.get("password") or ""
    threshold, terr = parse_threshold(d.get("alert_threshold"))
    if terr:
        return None, terr
    if len(name) < 2:
        return None, "Please enter your full name"
    if not EMAIL_RE.match(email):
        return None, "Please enter a valid email address"
    if not phone:
        return None, "Enter a Kenyan phone number, e.g. 0712345678"
    if gender not in GENDERS:
        return None, "Invalid gender"
    if account_type not in ACCOUNT_TYPES:
        return None, "Account type must be home or work"
    if dob:
        try:
            born = datetime.date.fromisoformat(dob)
        except ValueError:
            return None, "Date of birth must look like 1999-04-27"
        if not (16 <= (datetime.date.today() - born).days / 365.25 <= 110):
            return None, "Please check your date of birth"
    if not METER_RE.match(meter):
        return None, "Enter your meter number (letters and digits only)"
    if need_password and len(password) < 8:
        return None, "Password must be at least 8 characters"
    return dict(full_name=name, email=email, phone=phone, gender=gender, account_type=account_type,
                dob=dob or None, meter_number=meter, password=password, alert_threshold=threshold), None


def issue_code(conn, email, name):
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = _now()
    conn.execute(
        "INSERT INTO verification_codes (email, code_hash, expires_at, tries, sent_at) VALUES (?,?,?,0,?) "
        "ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash, expires_at=excluded.expires_at, "
        "tries=0, sent_at=excluded.sent_at",
        (email, _hash(code + email), _iso(now + datetime.timedelta(minutes=CODE_MINUTES)), _iso(now)))
    conn.commit()
    notify.send_email(email, "Your NextHome Energy verification code",
                      f"Hi {name},\n\nYour verification code is {code}.\n"
                      f"It expires in {CODE_MINUTES} minutes. If you did not sign up, ignore this email.\n")
    return code


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
    row = conn.execute(
        "SELECT a.* FROM sessions s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND a.verified = 1 AND COALESCE(a.disabled, 0) = 0",
        (_hash(header[7:]), _iso(_now()))).fetchone()
    return row


def profile(conn, acct):
    meters = conn.execute("SELECT meter_number, nickname FROM account_meters WHERE account_id = ? "
                          "ORDER BY rowid", (acct["id"],)).fetchall()
    return {"id": acct["id"], "full_name": acct["full_name"], "email": acct["email"],
            "phone": acct["phone"], "dob": acct["dob"], "gender": acct["gender"],
            "account_type": acct["account_type"],
            "alert_threshold": acct["alert_threshold"] if acct["alert_threshold"] is not None else 100.0,
            "meters": [{"meter_number": m["meter_number"], "nickname": m["nickname"]} for m in meters]}


def link_meter(conn, account_id, meter, nickname=None):
    conn.execute("INSERT OR IGNORE INTO meters (meter_number, balance) VALUES (?, 0.0)", (meter,))
    conn.execute("INSERT OR IGNORE INTO account_meters (account_id, meter_number, nickname) VALUES (?,?,?)",
                 (account_id, meter, nickname))


def phones_for_meter(conn, meter):
    """Phone numbers of verified accounts that own this meter (used for SMS alerts)."""
    return [r["phone"] for r in conn.execute(
        "SELECT a.phone FROM accounts a JOIN account_meters m ON m.account_id = a.id "
        "WHERE m.meter_number = ? AND a.verified = 1", (meter,))]


# ---------------- endpoints ----------------

@bp.route("/auth/register", methods=["POST"])
def register():
    clean, err = validate_profile(request.get_json(silent=True) or {})
    if err:
        return jsonify({"error": err}), 400
    conn = get_connection()
    try:
        existing = conn.execute("SELECT id, verified FROM accounts WHERE email = ?", (clean["email"],)).fetchone()
        if existing and existing["verified"]:
            return jsonify({"error": "An account with this email already exists. Please log in."}), 409
        pw = generate_password_hash(clean["password"])
        if existing:      # signed up before but never verified: let them start again
            acct_id = existing["id"]
            conn.execute("UPDATE accounts SET full_name=?, phone=?, dob=?, gender=?, account_type=?, password_hash=?, "
                         "alert_threshold=? WHERE id=?",
                         (clean["full_name"], clean["phone"], clean["dob"], clean["gender"],
                          clean["account_type"], pw, clean["alert_threshold"], acct_id))
            conn.execute("DELETE FROM account_meters WHERE account_id = ?", (acct_id,))
        else:
            acct_id = conn.execute(
                "INSERT INTO accounts (full_name, email, phone, dob, gender, account_type, password_hash, alert_threshold) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (clean["full_name"], clean["email"], clean["phone"], clean["dob"], clean["gender"],
                 clean["account_type"], pw, clean["alert_threshold"])).lastrowid
        link_meter(conn, acct_id, clean["meter_number"], "My home" if clean["account_type"] == "home" else "My workplace")
        conn.commit()
        code = issue_code(conn, clean["email"], clean["full_name"])
    finally:
        conn.close()
    out = {"message": "Account created. We sent a 6-digit code to your email.", "email": clean["email"]}
    if dev_mode():
        out["dev_code"] = code
    return jsonify(out), 201


@bp.route("/auth/verify", methods=["POST"])
def verify():
    d = request.get_json(silent=True) or {}
    email, code = (d.get("email") or "").strip().lower(), (d.get("code") or "").strip()
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM verification_codes WHERE email = ?", (email,)).fetchone()
        acct = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        if not row or not acct:
            return jsonify({"error": "No pending verification for this email"}), 404
        if row["expires_at"] < _iso(_now()):
            return jsonify({"error": "That code has expired. Tap resend for a new one."}), 410
        if row["tries"] >= MAX_TRIES:
            return jsonify({"error": "Too many wrong tries. Tap resend for a new code."}), 429
        if _hash(code + email) != row["code_hash"]:
            conn.execute("UPDATE verification_codes SET tries = tries + 1 WHERE email = ?", (email,))
            conn.commit()
            return jsonify({"error": "Wrong code"}), 400
        conn.execute("UPDATE accounts SET verified = 1 WHERE id = ?", (acct["id"],))
        conn.execute("DELETE FROM verification_codes WHERE email = ?", (email,))
        conn.commit()
        acct = conn.execute("SELECT * FROM accounts WHERE id = ?", (acct["id"],)).fetchone()
        return jsonify({"token": new_session(conn, acct["id"]), "user": profile(conn, acct)}), 200
    finally:
        conn.close()


@bp.route("/auth/resend", methods=["POST"])
def resend():
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip().lower()
    conn = get_connection()
    try:
        acct = conn.execute("SELECT * FROM accounts WHERE email = ? AND verified = 0", (email,)).fetchone()
        row = conn.execute("SELECT sent_at FROM verification_codes WHERE email = ?", (email,)).fetchone()
        if not acct:
            return jsonify({"message": "If that email is waiting for verification, a new code was sent."}), 200
        if row and row["sent_at"] > _iso(_now() - datetime.timedelta(seconds=RESEND_SECONDS)):
            return jsonify({"error": f"Please wait {RESEND_SECONDS} seconds before asking again"}), 429
        code = issue_code(conn, email, acct["full_name"])
    finally:
        conn.close()
    out = {"message": "A new code was sent."}
    if dev_mode():
        out["dev_code"] = code
    return jsonify(out), 200


@bp.route("/auth/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    email, password = (d.get("email") or "").strip().lower(), d.get("password") or ""
    conn = get_connection()
    try:
        acct = conn.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
        if not acct or not check_password_hash(acct["password_hash"], password):
            return jsonify({"error": "Invalid email or password"}), 401
        if not acct["verified"]:
            return jsonify({"error": "Please verify your email first", "needs_verification": True}), 403
        if acct["disabled"]:
            return jsonify({"error": "This account has been disabled. Please contact support."}), 403
        return jsonify({"token": new_session(conn, acct["id"]), "user": profile(conn, acct)}), 200
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
    meter = (d.get("meter_number") or "").strip().upper()
    if not METER_RE.match(meter):
        return jsonify({"error": "Enter your meter number (letters and digits only)"}), 400
    conn = get_connection()
    try:
        acct = current_account(conn)
        if not acct:
            return jsonify({"error": "Please log in again"}), 401
        link_meter(conn, acct["id"], meter, (d.get("nickname") or "").strip() or None)
        conn.commit()
        return jsonify(profile(conn, acct)), 200
    finally:
        conn.close()


@bp.route("/dev/create_verified_user", methods=["POST"])
def dev_create_verified_user():
    """Demo helper (DEV_MODE=1 only): a ready-to-use verified account with the given meters."""
    if not dev_mode():
        return jsonify({"error": "Not available"}), 404
    d = request.get_json(silent=True) or {}
    meters = [m.upper() for m in d.get("meters", [])]
    clean, err = validate_profile({**d, "meter_number": meters[0] if meters else ""})
    if err:
        return jsonify({"error": err}), 400
    conn = get_connection()
    try:
        conn.execute("DELETE FROM account_meters WHERE account_id IN (SELECT id FROM accounts WHERE email = ?)",
                     (clean["email"],))
        conn.execute("DELETE FROM accounts WHERE email = ?", (clean["email"],))
        acct_id = conn.execute(
            "INSERT INTO accounts (full_name, email, phone, dob, gender, account_type, password_hash, verified) "
            "VALUES (?,?,?,?,?,?,?,1)",
            (clean["full_name"], clean["email"], clean["phone"], clean["dob"], clean["gender"],
             clean["account_type"], generate_password_hash(clean["password"]))).lastrowid
        for i, m in enumerate(meters):
            link_meter(conn, acct_id, m, (d.get("nicknames") or [])[i] if i < len(d.get("nicknames") or []) else None)
        conn.commit()
        return jsonify({"message": "ok", "email": clean["email"], "meters": meters}), 201
    finally:
        conn.close()