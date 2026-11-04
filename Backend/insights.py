"""
insights.py - weekly/monthly reports, the "Will it last?" planner and smart alerts.

Alerts (sent as SMS to the phone of every verified account that owns the meter):
  low_credit  - predicted to run out in under LOW_HOURS hours
  spike       - usage over the last SPIKE_DAYS days is SPIKE_PCT% above the household's own normal
Each kind is sent at most once every COOLDOWN_HOURS per meter, so nobody gets spammed.
"""
import datetime

from flask import Blueprint, jsonify, request

import appliances_data
from database import get_connection
import notify

bp = Blueprint("insights", __name__)

LOW_HOURS, SPIKE_DAYS, SPIKE_PCT, COOLDOWN_HOURS, BASELINE_DAYS = 24.0, 3, 30.0, 12, 14

_helpers = {}


def init(billing_info, daily_kwh_series, phones_for_meter):
    _helpers.update(billing_info=billing_info, daily_kwh_series=daily_kwh_series, phones=phones_for_meter)
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
    sp = spike_info(series)
    if sp and sp["pct_change"] >= SPIKE_PCT:
        out.append({"kind": "spike", "level": "info", **sp,
                    "message": f"You are using {sp['pct_change']}% more electricity than usual "
                               f"({sp['recent_avg']} vs {sp['normal_avg']} kWh a day)."})
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
        return sent
    finally:
        conn.close()


@bp.route("/alerts/<meter_number>", methods=["GET"])
def alerts(meter_number):
    """What the app shows in its alert banner right now, plus the last 10 alerts that were sent."""
    conn = get_connection()
    try:
        now = current_alerts(conn, meter_number)
        if now is None:
            return jsonify({"error": "Meter not found"}), 404
        past = conn.execute("SELECT kind, message, sent_at FROM alert_log WHERE meter_number = ? "
                            "ORDER BY id DESC LIMIT 10", (meter_number,)).fetchall()
        return jsonify({"meter_number": meter_number, "active": now, "history": [dict(r) for r in past]}), 200
    finally:
        conn.close()