from flask import Flask, request, jsonify
from flask_cors import CORS
from database import get_connection, init_db
import random

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


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)