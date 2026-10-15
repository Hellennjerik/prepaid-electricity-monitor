from flask import Flask, request, jsonify
from flask_cors import CORS
from database import get_connection, init_db
import random

# ---- Appliance wattage lookup table ----
APPLIANCE_WATTS = {
    "fridge": 150,
    "microwave": 1000,
    "pasi": 1500,
    "tv": 100,
    "instant_shower": 4000,
    "washing_machine": 500,
    "oven": 2000,
    "air_fryer": 1400,
    "kettle": 1500,
    "water_pump": 750,
    "lights": 50,
    "phone_charger": 10,
    "wifi_router": 15,
}

app = Flask(__name__)
CORS(app)

# Initialize database on startup
init_db()


# ---------------- AUTH ENDPOINTS ----------------

@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    phone = data.get("phone", "")
    meter_number = data.get("meter_number")

    if not username or not password or not meter_number:
        return jsonify({"error": "Missing fields"}), 400

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, password, phone, meter_number) VALUES (?, ?, ?, ?)",
            (username, password, phone, meter_number)
        )
        conn.execute(
            "INSERT OR IGNORE INTO meters (meter_number, balance) VALUES (?, ?)",
            (meter_number, 0.0)
        )
        conn.commit()
        return jsonify({"message": "User registered successfully"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        conn.close()


@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, password)
    ).fetchone()
    conn.close()

    if user:
        return jsonify({
            "message": "Login successful",
            "user": {
                "username": user["username"],
                "meter_number": user["meter_number"]
            }
        }), 200
    return jsonify({"error": "Invalid credentials"}), 401


# ---------------- METER ENDPOINTS ----------------

@app.route("/meter_status/<meter_number>", methods=["GET"])
def meter_status(meter_number):
    conn = get_connection()
    meter = conn.execute(
        "SELECT * FROM meters WHERE meter_number = ?", (meter_number,)
    ).fetchone()
    conn.close()

    if meter:
        return jsonify(dict(meter)), 200
    return jsonify({"error": "Meter not found"}), 404


@app.route("/update_meter", methods=["POST"])
def update_meter():
    """Called by the virtual meter every second."""
    data = request.json
    meter_number = data.get("meter_number")
    balance = data.get("balance")
    kwh_used = data.get("kwh_used")

    conn = get_connection()
    conn.execute(
        "UPDATE meters SET balance = ?, kwh_used = ?, last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
        (balance, kwh_used, meter_number)
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Meter updated"}), 200


# ---------------- TOKEN ENDPOINTS ----------------

@app.route("/buy_token", methods=["POST"])
def buy_token():
    """User buys a token from the mobile app."""
    data = request.json
    meter_number = data.get("meter_number")
    amount = data.get("amount")

    if not meter_number or not amount:
        return jsonify({"error": "Missing fields"}), 400

    # Generate a mock 20-digit STS token
    token_string = "".join([str(random.randint(0, 9)) for _ in range(17)]) + f"{int(amount):03d}"

    conn = get_connection()
    conn.execute(
        "INSERT INTO tokens (token_string, amount, meter_number) VALUES (?, ?, ?)",
        (token_string, amount, meter_number)
    )
    conn.commit()
    conn.close()

    return jsonify({
        "message": "Token generated",
        "token": token_string,
        "amount": amount
    }), 201


@app.route("/apply_token", methods=["POST"])
def apply_token():
    """Virtual meter sends a token to be applied."""
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
    """Called by the solar simulator."""
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
    """Save the user's appliance list."""
    data = request.json
    meter_number = data.get("meter_number")
    appliances = data.get("appliances", [])
    if not meter_number:
        return jsonify({"error": "Missing meter_number"}), 400

    conn = get_connection()
    conn.execute("DELETE FROM user_appliances WHERE meter_number = ?", (meter_number,))
    for app in appliances:
        name = app.get("name", "").lower()
        frequency = app.get("frequency", "sometimes")
        watts = APPLIANCE_WATTS.get(name, 100)
        conn.execute("""
            INSERT INTO user_appliances (meter_number, appliance_name, frequency, watts)
            VALUES (?, ?, ?, ?)
        """, (meter_number, name, frequency, watts))
    conn.commit()
    conn.close()
    return jsonify({"message": f"Saved {len(appliances)} appliances"}), 200


@app.route("/analyze_appliances/<meter_number>", methods=["GET"])
def analyze_appliances(meter_number):
    """Return breakdown of estimated consumption per appliance."""
    conn = get_connection()
    apps = conn.execute(
        "SELECT * FROM user_appliances WHERE meter_number = ?", (meter_number,)
    ).fetchall()
    conn.close()
    if not apps:
        return jsonify({"error": "No appliances registered"}), 404

    freq_hours = {"rarely": 0.5, "sometimes": 1.5, "often": 3.0, "always": 8.0}
    RATE = 25.0
    breakdown = []
    total_kwh = 0

    for app in apps:
        hours = freq_hours.get(app["frequency"], 1.0)
        kwh_per_day = (app["watts"] / 1000.0) * hours
        ksh_per_month = kwh_per_day * 30 * RATE
        total_kwh += kwh_per_day
        breakdown.append({
            "appliance": app["appliance_name"],
            "watts": app["watts"],
            "frequency": app["frequency"],
            "kwh_per_day": round(kwh_per_day, 3),
            "ksh_per_month": round(ksh_per_month, 2),
        })

    breakdown.sort(key=lambda x: x["kwh_per_day"], reverse=True)
    for item in breakdown:
        item["percent"] = round((item["kwh_per_day"] / total_kwh * 100) if total_kwh > 0 else 0, 1)

    return jsonify({
        "meter_number": meter_number,
        "total_kwh_per_day": round(total_kwh, 3),
        "estimated_monthly_cost": round(total_kwh * 30 * RATE, 2),
        "breakdown": breakdown,
    }), 200


# ---------------- PREDICTION ENDPOINTS ----------------

@app.route("/log_history", methods=["POST"])
def log_history():
    """Called by the virtual meter to log readings for prediction."""
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
    """Predict when tokens and solar will run out."""
    conn = get_connection()
    meter = conn.execute("SELECT * FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    solar = conn.execute("""
        SELECT * FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1
    """, (meter_number,)).fetchone()
    conn.close()

    rate_kwh_per_hour = 0.5
    RATE = 25.0
    remaining_kwh = meter["balance"] / RATE
    hours_until_tokens = round(remaining_kwh / rate_kwh_per_hour, 1)

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
            "balance_ksh": meter["balance"],
            "remaining_kwh": round(remaining_kwh, 3),
            "hours_until_empty": hours_until_tokens,
        },
        "solar": {
            "battery_percent": solar["battery_percent"] if solar else 0,
            "hours_until_empty": hours_until_solar,
        },
    }), 200


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)