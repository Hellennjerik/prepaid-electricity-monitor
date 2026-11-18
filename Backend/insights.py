"""
insights.py - weekly/monthly reports, the "Will it last?" planner and smart alerts.

Alerts (sent as SMS to the phone of every verified account that owns the meter):
  low_credit  - predicted to run out in under LOW_HOURS hours
  spike       - usage over the last SPIKE_DAYS days is SPIKE_PCT% above the household's own normal
Each kind is sent at most once every COOLDOWN_HOURS per meter, so nobody gets spammed.
"""
import datetime
import os
import time

import requests
from flask import Blueprint, jsonify, request

import appliances_data
from database import get_connection
import notify

bp = Blueprint("insights", __name__)

LOW_HOURS, SPIKE_DAYS, SPIKE_PCT, COOLDOWN_HOURS, BASELINE_DAYS = 24.0, 3, 30.0, 12, 14
LOAD_FACTOR, LOAD_MIN_EXTRA_KW = 2.0, 0.5     # "usage right now" alert: at least double the usual and 0.5 kW more

_helpers = {}


def init(billing_info, daily_kwh_series, phones_for_meter, advance=None):
    _helpers.update(billing_info=billing_info, daily_kwh_series=daily_kwh_series, phones=phones_for_meter,
                    advance=advance or (lambda meter_number: 0.0))
    conn = get_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS alert_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "meter_number TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, "
                 "sent_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()


def _utc():
    return datetime.datetime.utcnow()


def _stamp(t):
    return t.strftime("%Y-%m-%d %H:%M:%S")


# ---------------- reports ----------------

def _bucket(series, key_fn, label_fn):
    buckets = {}
    for day, kwh in series:
        d = datetime.date.fromisoformat(day)
        b = buckets.setdefault(key_fn(d), {"label": label_fn(d), "kwh": 0.0, "days": 0})
        b["kwh"] += kwh
        b["days"] += 1
    return [buckets[k] for k in sorted(buckets)]


@bp.route("/reports/<meter_number>", methods=["GET"])
def reports(meter_number):
    """?period=daily|weekly|monthly  - kWh and cost per bucket, oldest first. Cost uses the meter's current rate."""
    period = request.args.get("period", "daily")
    if period not in ("daily", "weekly", "monthly"):
        return jsonify({"error": "period must be daily, weekly or monthly"}), 400
    conn = get_connection()
    if not conn.execute("SELECT 1 FROM meters WHERE meter_number = ?", (meter_number,)).fetchone():
        conn.close()
        return jsonify({"error": "Meter not found"}), 404
    info = _helpers["billing_info"](conn, meter_number)
    series = _helpers["daily_kwh_series"](conn, meter_number)
    conn.close()

    if period == "daily":
        buckets = _bucket(series[-14:], lambda d: d, lambda d: d.strftime("%a %d"))
    elif period == "weekly":
        monday = lambda d: d - datetime.timedelta(days=d.weekday())
        buckets = _bucket(series[-8 * 7:], monday, lambda d: f"{monday(d):%d %b}")[-8:]
    else:
        buckets = _bucket(series, lambda d: (d.year, d.month), lambda d: d.strftime("%b"))[-6:]

    rate = info["rate"]
    total = sum(b["kwh"] for b in buckets)
    days = sum(b["days"] for b in buckets)
    return jsonify({
        "meter_number": meter_number, "period": period, "tier": info["tier"], "rate_per_kwh": rate,
        "total_kwh": round(total, 2), "total_cost_ksh": round(total * rate, 2),
        "avg_kwh_per_day": round(total / days, 2) if days else 0,
        "buckets": [{"label": b["label"], "kwh": round(b["kwh"], 2), "cost_ksh": round(b["kwh"] * rate, 2),
                     "days": b["days"], "partial": b["days"] < (7 if period == "weekly" else 28 if period == "monthly" else 1)}
                    for b in buckets],
    }), 200


# ---------------- "Will it last?" planner ----------------

@bp.route("/planner/<meter_number>", methods=["GET"])
def planner(meter_number):
    """?amount=1000 - what this purchase buys, where the money goes, and how long it would last."""
    try:
        amount = float(request.args.get("amount", ""))
    except ValueError:
        return jsonify({"error": "amount must be a number"}), 400
    if not 10 <= amount <= 100000:
        return jsonify({"error": "Enter an amount between KSh 10 and KSh 100,000"}), 400
    conn = get_connection()
    meter = conn.execute("SELECT balance FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404
    info = _helpers["billing_info"](conn, meter_number)
    conn.close()

    tier, rate = info["tier"], info["rate"]
    units = appliances_data.units_for_amount(amount, tier)
    energy_share = appliances_data.base_rate(tier) / rate          # the rest is fuel, FERFA, levies and VAT
    daily = info["avg_daily_kwh"]
    return jsonify({
        "meter_number": meter_number, "tier": tier, "rate_per_kwh": rate,
        "amount_ksh": amount, "units_kwh": units,
        "energy_ksh": round(amount * energy_share, 2),
        "levies_and_taxes_ksh": round(amount * (1 - energy_share), 2),
        "levies_percent": round((1 - energy_share) * 100),
        "avg_daily_kwh": daily,
        "days_it_lasts": round(units / daily, 1) if daily else None,
        "days_with_current_balance": round((meter["balance"] / rate + units) / daily, 1) if daily else None,
        "basis": "history" if daily else "no history yet",
    }), 200


# ---------------- solar and grid sharing ----------------

BATTERY_RESERVE_PCT = 20.0     # a hybrid inverter keeps some charge in reserve and switches to the grid below it


def solar_flow(generation_kw, load_kw, battery_percent):
    """
    Where the home's power comes from right now, using the usual hybrid-inverter priority:
        1. solar first   2. battery next (above its reserve)   3. the Kenya Power grid for whatever is left
    Only the grid part passes through the prepaid meter and costs money.
    Spare solar (more than the home needs) charges the battery; if the battery is full it is not used.
    """
    generation_kw, load_kw = max(0.0, generation_kw or 0.0), max(0.0, load_kw or 0.0)
    solar_kw = min(generation_kw, load_kw)
    shortfall = load_kw - solar_kw
    battery_kw = shortfall if (battery_percent or 0) > BATTERY_RESERVE_PCT else 0.0
    grid_kw = shortfall - battery_kw
    spare = max(0.0, generation_kw - load_kw)
    pct = lambda kw: round(100.0 * kw / load_kw) if load_kw > 0 else 0  # noqa: E731
    if load_kw <= 0:
        mode, note = "idle", "Nothing is running right now."
    elif grid_kw <= 0 and battery_kw <= 0:
        mode, note = "solar", "Solar is covering the whole home. The grid is not being used."
    elif grid_kw <= 0 and solar_kw <= 0:
        mode, note = "battery", "Running on the battery. The grid is not being used."
    elif grid_kw <= 0:
        mode, note = "solar_battery", "Solar and the battery are covering the home. The grid is not being used."
    elif solar_kw <= 0 and battery_kw <= 0:
        mode, note = "grid", "The home is on Kenya Power. Battery is at its reserve and there is no sun."
    else:
        mode, note = "mixed", "Solar is not enough on its own, so Kenya Power covers the rest. Only that part uses your tokens."
    return {"mode": mode, "note": note, "solar_kw": round(solar_kw, 2), "battery_kw": round(battery_kw, 2),
            "grid_kw": round(grid_kw, 2), "spare_solar_kw": round(spare, 2),
            "solar_pct": pct(solar_kw), "battery_pct": pct(battery_kw), "grid_pct": pct(grid_kw)}


# ---------------- alerts ----------------

def spike_info(series):
    """Compare the last SPIKE_DAYS completed days with the BASELINE_DAYS before them."""
    if len(series) < SPIKE_DAYS + 7:
        return None
    recent = [k for _, k in series[-SPIKE_DAYS:]]
    base = [k for _, k in series[-(SPIKE_DAYS + BASELINE_DAYS):-SPIKE_DAYS]]
    recent_avg, base_avg = sum(recent) / len(recent), sum(base) / len(base)
    if base_avg <= 0:
        return None
    return {"recent_avg": round(recent_avg, 2), "normal_avg": round(base_avg, 2),
            "pct_change": round((recent_avg / base_avg - 1) * 100)}


def load_alert(load_kw, usual_kw):
    """Is the power being drawn right now well above this household's usual? Returns the alert details or None."""
    if not usual_kw or usual_kw <= 0 or load_kw <= 0:
        return None
    if load_kw >= LOAD_FACTOR * usual_kw and load_kw - usual_kw >= LOAD_MIN_EXTRA_KW:
        return {"load_kw": round(load_kw, 2), "usual_kw": round(usual_kw, 2),
                "pct_change": round((load_kw / usual_kw - 1) * 100)}
    return None


def current_alerts(conn, meter_number):
    meter = conn.execute("SELECT balance FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        return None
    info = _helpers["billing_info"](conn, meter_number)
    series = _helpers["daily_kwh_series"](conn, meter_number)
    out = []
    if info["avg_daily_kwh"]:
        hours = meter["balance"] / (info["avg_daily_kwh"] * info["rate"]) * 24
        if hours < LOW_HOURS:
            out.append({"kind": "low_credit", "level": "warning", "hours_left": round(hours, 1),
                        "message": f"Your credit may run out in about {hours:.0f} hours "
                                   f"(KSh {meter['balance']:.0f} left). Top up soon."})
    row = conn.execute("SELECT load_w FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if info["avg_daily_kwh"] and row and row["load_w"]:
        la = load_alert(row["load_w"] / 1000.0, info["avg_daily_kwh"] / 24.0)
        if la:
            out.append({"kind": "load_high", "level": "info", **la,
                        "message": f"You are using {la['load_kw']} kW right now, {la['pct_change']}% more than your usual "
                                   f"{la['usual_kw']} kW. Check for appliances left on."})
    sp = spike_info(series)
    if sp and sp["pct_change"] >= SPIKE_PCT:
        out.append({"kind": "spike", "level": "info", **sp,
                    "message": f"You are using {sp['pct_change']}% more electricity than usual "
                               f"({sp['recent_avg']} vs {sp['normal_avg']} kWh a day)."})
    return out


def threshold_alerts(conn, meter_number, only_account_id=None):
    """Alerts for owners whose own alert level (set at registration, changeable in Profile) has been reached."""
    meter = conn.execute("SELECT balance FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
    if not meter:
        return []
    rows = conn.execute(
        "SELECT a.id, a.phone, a.alert_threshold FROM accounts a JOIN account_meters m ON m.account_id = a.id "
        "WHERE m.meter_number = ? AND a.verified = 1 AND COALESCE(a.disabled, 0) = 0 "
        "AND COALESCE(a.alert_threshold, 100) > 0", (meter_number,)).fetchall()
    out = []
    for r in rows:
        if only_account_id is not None and r["id"] != only_account_id:
            continue
        level = r["alert_threshold"] if r["alert_threshold"] is not None else 100.0
        if meter["balance"] <= level:
            out.append({"kind": f"threshold_{r['id']}", "level": "warning", "phone": r["phone"],
                        "message": f"Your balance is KSh {meter['balance']:.0f}, at or below your alert level of "
                                   f"KSh {level:.0f}. Top up soon."})
    return out


def check_and_notify(meter_number):
    """Send an SMS for each current alert that has not been sent in the last COOLDOWN_HOURS."""
    conn = get_connection()
    try:
        sent = []
        for a in current_alerts(conn, meter_number) or []:
            recent = conn.execute("SELECT 1 FROM alert_log WHERE meter_number = ? AND kind = ? AND sent_at > ?",
                                  (meter_number, a["kind"], _stamp(_utc() - datetime.timedelta(hours=COOLDOWN_HOURS)))).fetchone()
            if recent:
                continue
            for phone in _helpers["phones"](conn, meter_number) or [None]:
                notify.send_sms(phone, f"NextHome Energy ({meter_number}): {a['message']}")
            conn.execute("INSERT INTO alert_log (meter_number, kind, message, sent_at) VALUES (?,?,?,?)",
                         (meter_number, a["kind"], a["message"], _stamp(_utc())))
            conn.commit()
            sent.append(a["kind"])
        for a in threshold_alerts(conn, meter_number):
            recent = conn.execute("SELECT 1 FROM alert_log WHERE meter_number = ? AND kind = ? AND sent_at > ?",
                                  (meter_number, a["kind"], _stamp(_utc() - datetime.timedelta(hours=COOLDOWN_HOURS)))).fetchone()
            if recent:
                continue
            notify.send_sms(a["phone"], f"NextHome Energy ({meter_number}): {a['message']}")
            conn.execute("INSERT INTO alert_log (meter_number, kind, message, sent_at) VALUES (?,?,?,?)",
                         (meter_number, a["kind"], a["message"], _stamp(_utc())))
            conn.commit()
            sent.append(a["kind"])
        return sent
    finally:
        conn.close()


@bp.route("/alerts/<meter_number>", methods=["GET"])
def alerts(meter_number):
    """What the app shows in its alert banner right now, plus the last 10 alerts that were sent."""
    _helpers["advance"](meter_number)
    conn = get_connection()
    try:
        now = current_alerts(conn, meter_number)
        if now is None:
            return jsonify({"error": "Meter not found"}), 404
        import accounts
        me = accounts.current_account(conn)
        if me:   # the signed-in user's own alert level also shows in the app banner
            for t in threshold_alerts(conn, meter_number, only_account_id=me["id"]):
                now.append({k: v for k, v in t.items() if k != "phone"})
        try:     # makes sure the SMS goes out even when no meter simulator is reporting usage
            check_and_notify(meter_number)
        except Exception as e:  # noqa: BLE001
            print("alert check failed:", e)
        past = conn.execute("SELECT kind, message, sent_at FROM alert_log WHERE meter_number = ? "
                            "ORDER BY id DESC LIMIT 10", (meter_number,)).fetchall()
        return jsonify({"meter_number": meter_number, "active": now, "history": [dict(r) for r in past]}), 200
    finally:
        conn.close()


# ---------------- appliance catalogue for the tick-list ----------------

# Appliances grouped by WHEN they normally run (this is what the tick-list shows):
CATEGORY = {
    "Runs all day": ["fridge_single", "fridge_double", "chest_freezer", "wifi_router", "cctv_system",
                     "electric_fence"],
    "Morning and evening": ["instant_shower", "water_boiler", "kettle", "toaster", "blender", "microwave",
                            "water_pump", "pay_tv_decoder"],
    "Evenings and night": ["led_lighting", "floodlight", "tv_32", "tv_65", "tv_85", "electric_cooker",
                           "pressure_cooker", "air_fryer", "dishwasher", "phone_charger"],
    "Daytime": ["laptop", "ceiling_fan", "rice_cooker"],
    "Weekly chores": ["iron_box", "front_load_washer", "twin_tub_washer"],
}
TIPS = {
    "instant_shower": "Showers are one of the biggest bills. Shorter showers or a solar water heater cut this fast.",
    "water_boiler": "Put the boiler on a timer and insulate the tank so it only heats when needed.",
    "electric_cooker": "Use lids and the right pot size, or cook bulk meals in a pressure cooker.",
    "iron_box": "Iron in one batch instead of a little every day.",
    "kettle": "Boil only the water you need.",
    "fridge_double": "Keep the door seals clean and the fridge away from the cooker.",
    "chest_freezer": "Keep it full and defrost regularly.",
    "water_pump": "Fix leaks and fill the tank in one go.",
    "floodlight": "Use a motion sensor or a timer instead of leaving it on all night.",
    "tv_85": "Bigger screens use more. Switch off at the wall instead of standby.",
}
_TYPICAL_HOUSEHOLD = {"occupants": 3, "overrides": {}}


@bp.route("/appliance_catalog", methods=["GET"])
def appliance_catalog():
    """Everything the user can tick, with typical watts and hours per day (no typing needed)."""
    out = []
    for category, keys in CATEGORY.items():
        for key in keys:
            spec = appliances_data.APPLIANCES[key]
            kwh_month = appliances_data.monthly_kwh(key, _TYPICAL_HOUSEHOLD)
            out.append({"key": key, "label": spec["label"], "category": category, "watts": spec["watts"],
                        "hours_per_day": round(kwh_month * 1000 / (spec["watts"] * 30), 2),
                        "tip": TIPS.get(key)})
    return jsonify({"appliances": out}), 200


@bp.route("/appliances/<meter_number>", methods=["GET"])
def saved_appliances(meter_number):
    conn = get_connection()
    rows = conn.execute("SELECT appliance_name FROM user_appliances WHERE meter_number = ?", (meter_number,)).fetchall()
    conn.close()
    return jsonify({"meter_number": meter_number, "keys": [r["appliance_name"] for r in rows]}), 200


# ---------------- weather for solar homes ----------------
# Free forecast from Open-Meteo (no sign-up). Needs internet on the server; if it is not
# reachable the app simply hides the weather line, nothing breaks. Location: Nairobi by default.
WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "-1.286"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "36.817"))
_weather_cache = {"at": 0.0, "data": None}
WEATHER_CACHE_SECONDS = 3 * 3600


def _outlook(code):
    if code in (0, 1):
        return "Sunny", "☀️"
    if code == 2:
        return "Partly cloudy", "⛅"
    if code in (3, 45, 48):
        return "Cloudy", "☁️"
    if code is not None and (51 <= code <= 67 or 80 <= code <= 82):
        return "Rainy", "🌧️"
    if code is not None and code >= 95:
        return "Thunderstorms", "⛈️"
    return "Mixed", "🌤️"


def _rating(sun_hours):
    return "good" if sun_hours >= 7 else "fair" if sun_hours >= 4 else "low"


def build_weather(raw):
    """Turn Open-Meteo's daily arrays into 3 simple days plus one line of advice."""
    daily = raw["daily"]
    days = []
    for i, date in enumerate(daily["time"][:3]):
        sun = (daily["sunshine_duration"][i] or 0) / 3600.0
        outlook, emoji = _outlook(daily["weather_code"][i])
        label = "Today" if i == 0 else "Tomorrow" if i == 1 else datetime.date.fromisoformat(date).strftime("%a")
        days.append({"date": date, "label": label, "outlook": outlook, "emoji": emoji,
                     "sunshine_hours": round(sun, 1), "solar": _rating(sun)})
    today, tomorrow = days[0], days[1] if len(days) > 1 else days[0]
    if today["solar"] == "good":
        advice = "Good sun today: run the iron, washing machine and water pump between 10am and 3pm."
    elif today["solar"] == "fair":
        advice = "Fair sun today: do heavy chores around midday, when your panels give the most."
    else:
        advice = "Low sun today: go easy on heavy appliances to save your battery."
    if tomorrow is not today and tomorrow["solar"] == "low":
        advice += f" Tomorrow looks {tomorrow['outlook'].lower()}, so keep some credit in reserve."
    elif tomorrow is not today and tomorrow["solar"] == "good":
        advice += " Tomorrow looks sunny, so you can save the heavy chores for then."
    return {"available": True, "days": days, "advice": advice}


@bp.route("/weather/<meter_number>", methods=["GET"])
def weather(meter_number):
    now = time.time()
    if _weather_cache["data"] is None or now - _weather_cache["at"] > WEATHER_CACHE_SECONDS:
        try:
            r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, params={
                "latitude": WEATHER_LAT, "longitude": WEATHER_LON, "timezone": "Africa/Nairobi",
                "forecast_days": 3, "daily": "weather_code,sunshine_duration"})
            r.raise_for_status()
            _weather_cache.update(at=now, data=build_weather(r.json()))
        except Exception as e:                      # offline, blocked, or an unexpected reply
            print(f"[weather unavailable] {type(e).__name__}: {e}")
            if _weather_cache["data"] is None:
                return jsonify({"available": False}), 200
    return jsonify(_weather_cache["data"]), 200