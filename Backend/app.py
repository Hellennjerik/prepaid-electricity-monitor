from flask import Flask, request, jsonify
from flask_cors import CORS
from database import get_connection, init_db
import datetime
import os
import random
import appliances_data

# ---------------- BILLING HELPERS ----------------
# KPLC puts a meter in a tariff band (DC1 / DC2 / DC3) by its 3-month moving average of
# monthly kWh. The server works that out from consumption_history, so the band, the price per
# unit and the predictions all follow the household's own usage instead of one fixed rate.
TIER_WINDOW_DAYS = 90        # KPLC's 3-month moving average
PREDICT_WINDOW_DAYS = 14     # two full weeks, so weekly habits (washing day, ironing) cancel out
MIN_HISTORY_DAYS = 7         # with less history than this, fall back to defaults
DEFAULT_TIER = "DC2"         # assumption for a meter with no history yet
FALLBACK_DAILY_KWH = 12.0    # the old 0.5 kW all-day assumption, only used with no history
LOW_BALANCE_KSH = 50.0       # simulated SMS fires when the balance crosses below this

# Fallback appliance wattage table (used when the app does not send watts)
APPLIANCE_WATTS = {
    "fridge": 150, "microwave": 1000, "pasi": 1500, "tv": 100,
    "instant_shower": 4000, "washing_machine": 500, "oven": 2000,
    "air_fryer": 1400, "kettle": 1500, "water_pump": 750,
    "lights": 50, "phone_charger": 10, "wifi_router": 15,
}

app = Flask(__name__)
CORS(app)

# Initialize database on startup
init_db()


def ensure_schema():
    """Add the hours_per_day column to user_appliances if this database does not have it yet."""
    conn = get_connection()
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(user_appliances)")]
    if "hours_per_day" not in columns:
        conn.execute("ALTER TABLE user_appliances ADD COLUMN hours_per_day REAL")
        conn.commit()
    conn.close()


ensure_schema()


def daily_kwh_series(conn, meter_number):
    """
    Completed days only, oldest first: [(date, kwh)].
    consumption_history.kwh_used is a running total, so one day's usage is the difference
    between that day's last reading and the previous day's. Today is left out (still in progress).
    """
    today = datetime.date.today().isoformat()
    rows = conn.execute(
        "SELECT date(recorded_at) AS day, MAX(kwh_used) AS cumulative "
        "FROM consumption_history WHERE meter_number = ? AND date(recorded_at) < ? "
        "GROUP BY date(recorded_at) ORDER BY day",
        (meter_number, today),
    ).fetchall()
    return [(cur["day"], max(0.0, cur["cumulative"] - prev["cumulative"]))
            for prev, cur in zip(rows, rows[1:])]


def billing_info(conn, meter_number):
    """Tariff band, KSh/kWh and recent usage for a meter, worked out from its history."""
    series = daily_kwh_series(conn, meter_number)
    window = series[-TIER_WINDOW_DAYS:]
    if len(window) >= MIN_HISTORY_DAYS:
        avg_monthly = sum(kwh for _, kwh in window) * 30 / len(window)
        tier = appliances_data.tier_for(avg_monthly)
    else:
        avg_monthly, tier = None, DEFAULT_TIER

    recent = series[-PREDICT_WINDOW_DAYS:]
    avg_daily = sum(kwh for _, kwh in recent) / len(recent) if len(recent) >= 3 else None
    return {
        "tier": tier,
        "rate": appliances_data.all_in_rate(tier),
        "avg_monthly_kwh": round(avg_monthly, 1) if avg_monthly is not None else None,
        "avg_daily_kwh": round(avg_daily, 3) if avg_daily is not None else None,
        "history_days": len(series),
    }


def send_low_balance_sms(balance):
    print("\n" + "=" * 60)
    print("📱 [SIMULATED SMS SENT TO USER]")
    print(f"⚠️ ALERT: Your KPLC balance is low! (KSh {balance:.2f} remaining).")
    print("Please buy tokens now to avoid disconnection.")
    print("=" * 60 + "\n")


# ---------------- AUTH ENDPOINTS ----------------

@app.route("/register", methods=["POST"])
@app.route("/login", methods=["POST"])
def legacy_auth_removed():
    # The old endpoints stored passwords in plain text. Use /auth/register and /auth/login.
    return jsonify({"error": "Moved to /auth/register and /auth/login"}), 410


# ---------------- METER ENDPOINTS ----------------

@app.route("/meter_status/<meter_number>", methods=["GET"])
def meter_status(meter_number):
    conn = get_connection()
    meter = conn.execute(
        "SELECT * FROM meters WHERE meter_number = ?", (meter_number,)
    ).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    info = billing_info(conn, meter_number)
    conn.close()

    out = dict(meter)
    out["balance"] = round(out["balance"], 2)
    out["kwh_used"] = round(out["kwh_used"], 4)
    out["tier"] = info["tier"]                  # new: e.g. "DC2"
    out["rate_per_kwh"] = info["rate"]          # new: KSh per unit on a token
    return jsonify(out), 200


@app.route("/report_usage", methods=["POST"])
def report_usage():
    """
    The meter reports the energy it measured (kWh). The server owns the balance and the
    tariff: it charges the kWh at this meter's tier rate in a single atomic UPDATE, so a token
    bought on the phone can never be overwritten by the meter.
    """
    data = request.get_json(silent=True) or {}
    meter_number = data.get("meter_number")
    try:
        kwh_delta = float(data.get("kwh_delta", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "kwh_delta must be a number"}), 400
    if not meter_number or kwh_delta < 0:
        return jsonify({"error": "Missing meter_number or negative kwh_delta"}), 400

    conn = get_connection()
    old = conn.execute("SELECT balance FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not old:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    info = billing_info(conn, meter_number)
    conn.execute(
        "UPDATE meters SET balance = MAX(0, balance - ?), kwh_used = kwh_used + ?, "
        "last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
        (kwh_delta * info["rate"], kwh_delta, meter_number),
    )
    conn.commit()
    new = conn.execute("SELECT balance, kwh_used FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    conn.close()

    # SMS alerts: "under 24 hours left" and "using more than usual" (each at most every 12 hours)
    insights.check_and_notify(meter_number)

    return jsonify({
        "balance": round(new["balance"], 2),
        "kwh_used": round(new["kwh_used"], 4),
        "tier": info["tier"],
        "rate": info["rate"],
    }), 200


@app.route("/update_meter", methods=["POST"])
def update_meter():
    """Older endpoint: the caller sends the whole balance. meter_gui.py now uses /report_usage."""
    data = request.json
    meter_number = data.get("meter_number")
    balance = data.get("balance")
    kwh_used = data.get("kwh_used")

    conn = get_connection()
    old_meter = conn.execute("SELECT balance FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    old_balance = old_meter["balance"] if old_meter else 0.0

    conn.execute(
        "UPDATE meters SET balance = ?, kwh_used = ?, last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
        (balance, kwh_used, meter_number)
    )
    conn.commit()
    conn.close()

    if balance < LOW_BALANCE_KSH and old_balance >= LOW_BALANCE_KSH:
        send_low_balance_sms(balance)

    return jsonify({"message": "Meter updated"}), 200


# ---------------- TOKEN ENDPOINTS ----------------

@app.route("/buy_token", methods=["POST"])
def buy_token():
    data = request.json
    meter_number = data.get("meter_number")
    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Amount must be a number"}), 400

    if not meter_number or amount <= 0:
        return jsonify({"error": "Missing fields"}), 400

    conn = get_connection()
    meter = conn.execute("SELECT 1 FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    # Units depend on the meter's tariff band: the same KSh buys fewer units in a higher band.
    info = billing_info(conn, meter_number)
    units = appliances_data.units_for_amount(amount, info["tier"])
    # 17 random digits + last 3 digits of the amount = a 20-digit token
    token_string = "".join([str(random.randint(0, 9)) for _ in range(17)]) + f"{int(amount) % 1000:03d}"

    conn.execute(
        "INSERT INTO tokens (token_string, amount, meter_number, status) VALUES (?, ?, ?, 'used')",
        (token_string, amount, meter_number)
    )
    conn.execute(
        "UPDATE meters SET balance = balance + ? WHERE meter_number = ?",
        (amount, meter_number)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "message": "Token generated and auto-applied",
        "token": token_string,
        "amount": amount,
        "units": units,
        "tier": info["tier"],
        "rate_per_kwh": info["rate"],
    }), 201


@app.route("/apply_token", methods=["POST"])
def apply_token():
    data = request.json
    token_string = data.get("token_string")
    meter_number = data.get("meter_number")

    conn = get_connection()
    token = conn.execute(
        "SELECT * FROM tokens WHERE token_string = ? AND meter_number = ?",
        (token_string, meter_number)
    ).fetchone()

    if not token:
        conn.close()
        return jsonify({"error": "Invalid token"}), 400

    if token["status"] == "used":
        conn.close()
        return jsonify({"error": "Token already used"}), 400

    conn.execute("UPDATE tokens SET status = 'used' WHERE id = ?", (token["id"],))
    conn.execute(
        "UPDATE meters SET balance = balance + ? WHERE meter_number = ?",
        (token["amount"], meter_number)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "message": "Token applied",
        "amount_added": token["amount"]
    }), 200


# ---------------- SOLAR ENDPOINTS ----------------

@app.route("/update_solar", methods=["POST"])
def update_solar():
    data = request.json
    meter_number = data.get("meter_number")
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1",
        (meter_number,)
    ).fetchone()

    if existing:
        conn.execute("""
            UPDATE solar_state
            SET battery_percent = ?, generation_kw = ?, load_kw = ?,
                solar_kwh_today = ?, last_updated = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (data.get("battery_percent"), data.get("generation_kw"),
              data.get("load_kw"), data.get("solar_kwh_today"), existing["id"]))
    else:
        conn.execute("""
            INSERT INTO solar_state
            (meter_number, battery_percent, generation_kw, load_kw, solar_kwh_today)
            VALUES (?, ?, ?, ?, ?)
        """, (meter_number, data.get("battery_percent"),
              data.get("generation_kw"), data.get("load_kw"),
              data.get("solar_kwh_today")))

    conn.commit()
    conn.close()
    return jsonify({"message": "Solar updated"}), 200


@app.route("/solar_status/<meter_number>", methods=["GET"])
def solar_status(meter_number):
    conn = get_connection()
    solar = conn.execute("""
        SELECT * FROM solar_state WHERE meter_number = ?
        ORDER BY id DESC LIMIT 1
    """, (meter_number,)).fetchone()
    conn.close()
    if solar:
        return jsonify(dict(solar)), 200
    return jsonify({"error": "No solar data"}), 404


# ---------------- APPLIANCE ENDPOINTS ----------------

@app.route("/register_appliances", methods=["POST"])
def register_appliances():
    data = request.json
    meter_number = data.get("meter_number")
    appliances = data.get("appliances", [])
    if not meter_number:
        return jsonify({"error": "Missing meter_number"}), 400

    conn = get_connection()
    # CLEAR old appliances so we don't duplicate them
    conn.execute("DELETE FROM user_appliances WHERE meter_number = ?", (meter_number,))

    for app_data in appliances:
        name = app_data.get("name", "").lower()
        frequency = app_data.get("frequency", "sometimes")
        watts = app_data.get("watts", APPLIANCE_WATTS.get(name, 100))
        hours_per_day = app_data.get("hours_per_day")   # optional: average hours/day from the simulation

        conn.execute("""
            INSERT INTO user_appliances (meter_number, appliance_name, frequency, watts, hours_per_day)
            VALUES (?, ?, ?, ?, ?)
        """, (meter_number, name, frequency, watts, hours_per_day))
    conn.commit()
    conn.close()
    return jsonify({"message": f"Saved {len(appliances)} appliances"}), 200


@app.route("/analyze_appliances/<meter_number>", methods=["GET"])
def analyze_appliances(meter_number):
    conn = get_connection()
    apps = conn.execute(
        "SELECT * FROM user_appliances WHERE meter_number = ?", (meter_number,)
    ).fetchall()
    info = billing_info(conn, meter_number)
    conn.close()
    if not apps:
        return jsonify({"error": "No appliances registered"}), 404

    # Used only for appliances that were ticked in the app without an hours_per_day figure
    freq_hours = {"rarely": 0.5, "sometimes": 1.5, "often": 3.0, "always": 8.0, "daily": 2.0}
    rate = info["rate"]
    breakdown = []
    total_kwh = 0

    for app_data in apps:
        stored_hours = app_data["hours_per_day"]
        hours = stored_hours if stored_hours is not None else freq_hours.get(app_data["frequency"], 1.0)
        kwh_per_day = (app_data["watts"] / 1000.0) * hours
        ksh_per_month = kwh_per_day * 30 * rate
        total_kwh += kwh_per_day
        name = app_data["appliance_name"]
        breakdown.append({
            "appliance": name,
            "label": appliances_data.APPLIANCES.get(name, {}).get("label", name.replace("_", " ").title()),
            "watts": app_data["watts"],
            "frequency": app_data["frequency"],
            "hours_per_day": round(hours, 2),
            "kwh_per_day": round(kwh_per_day, 3),
            "ksh_per_month": round(ksh_per_month, 2),
        })

    breakdown.sort(key=lambda x: x["kwh_per_day"], reverse=True)
    for item in breakdown:
        item["percent"] = round((item["kwh_per_day"] / total_kwh * 100) if total_kwh > 0 else 0, 1)

    return jsonify({
        "meter_number": meter_number,
        "tier": info["tier"],
        "rate_per_kwh": rate,
        "total_kwh_per_day": round(total_kwh, 3),
        "estimated_monthly_cost": round(total_kwh * 30 * rate, 2),
        "breakdown": breakdown,
    }), 200


# ---------------- PREDICTION & SEEDING ENDPOINTS ----------------

@app.route("/log_history", methods=["POST"])
def log_history():
    data = request.json
    conn = get_connection()
    conn.execute("""
        INSERT INTO consumption_history (meter_number, balance, kwh_used)
        VALUES (?, ?, ?)
    """, (data.get("meter_number"), data.get("balance"), data.get("kwh_used")))
    conn.commit()
    conn.close()
    return jsonify({"message": "Logged"}), 200


@app.route("/predict/<meter_number>", methods=["GET"])
def predict(meter_number):
    conn = get_connection()
    meter = conn.execute("SELECT * FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    solar = conn.execute("""
        SELECT * FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1
    """, (meter_number,)).fetchone()
    info = billing_info(conn, meter_number)
    conn.close()

    # Time left = balance / (average recent daily spend), at this meter's tier rate.
    rate = info["rate"]
    if info["avg_daily_kwh"] is not None:
        daily_kwh, basis = info["avg_daily_kwh"], "history"
    else:
        daily_kwh, basis = FALLBACK_DAILY_KWH, "default"
    daily_spend = daily_kwh * rate
    remaining_kwh = meter["balance"] / rate
    if daily_spend > 0:
        hours_until_tokens = min(round(meter["balance"] / daily_spend * 24, 1), 9999.0)
    else:
        hours_until_tokens = 9999.0

    hours_until_solar = 99
    if solar:
        battery = solar["battery_percent"]
        net = solar["load_kw"] - solar["generation_kw"]
        if net > 0:
            battery_kwh = (battery / 100.0) * 5.0
            hours_until_solar = round(battery_kwh / net, 1)

    return jsonify({
        "meter_number": meter_number,
        "tokens": {
            "balance_ksh": round(meter["balance"], 2),
            "remaining_kwh": round(remaining_kwh, 3),
            "hours_until_empty": hours_until_tokens,
        },
        "solar": {
            "battery_percent": solar["battery_percent"] if solar else 0,
            "hours_until_empty": hours_until_solar,
        },
        "billing": {
            "tier": info["tier"],
            "rate_per_kwh": rate,
            "avg_daily_kwh": round(daily_kwh, 3),
            "avg_daily_spend_ksh": round(daily_spend, 2),
            "basis": basis,                    # "history" or "default" (no history yet)
            "window_days": PREDICT_WINDOW_DAYS,
        },
    }), 200


@app.route("/history/<meter_number>", methods=["GET"])
def history(meter_number):
    """
    Daily kWh and cost for the Reports bar chart: completed days only, oldest first.
    Optional ?days=N (default 7, max 90).
    """
    try:
        days = int(request.args.get("days", 7))
    except ValueError:
        days = 7
    days = max(1, min(days, TIER_WINDOW_DAYS))

    conn = get_connection()
    meter = conn.execute("SELECT 1 FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404
    info = billing_info(conn, meter_number)
    series = daily_kwh_series(conn, meter_number)[-days:]
    conn.close()

    rate = info["rate"]
    total_kwh = sum(kwh for _, kwh in series)
    return jsonify({
        "meter_number": meter_number,
        "tier": info["tier"],
        "rate_per_kwh": rate,
        "days": len(series),
        "total_kwh": round(total_kwh, 2),
        "total_cost_ksh": round(total_kwh * rate, 2),
        "avg_kwh_per_day": round(total_kwh / len(series), 2) if series else 0,
        "daily": [{
            "date": day,
            "weekday": datetime.date.fromisoformat(day).strftime("%a"),
            "kwh": round(kwh, 2),
            "cost_ksh": round(kwh * rate, 2),
        } for day, kwh in series],
    }), 200


@app.route("/seed_history", methods=["POST"])
def seed_history():
    data = request.get_json()
    meter, records = data["meter_number"], data["records"]
    if not records:
        return jsonify({"error": "No records"}), 400
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM consumption_history WHERE meter_number = ?", (meter,))
    for r in records:
        cur.execute(
            "INSERT INTO consumption_history (meter_number, balance, kwh_used, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (meter, r["balance"], r["kwh_used"], r["recorded_at"]),
        )
    last = records[-1]
    cur.execute(
        "INSERT INTO meters (meter_number, balance, kwh_used) VALUES (?, ?, ?) "
        "ON CONFLICT(meter_number) DO UPDATE SET balance = excluded.balance, "
        "kwh_used = excluded.kwh_used, last_updated = CURRENT_TIMESTAMP",
        (meter, last["balance"], last["kwh_used"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": f"Seeded {len(records)} days for {meter}"})


# ---------------- ACCOUNTS AND INSIGHTS ----------------
import accounts   # noqa: E402
import insights   # noqa: E402

accounts.init_account_tables()
insights.init(billing_info, daily_kwh_series, accounts.phones_for_meter)
app.register_blueprint(accounts.bp)
app.register_blueprint(insights.bp)


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("DEBUG") == "1")