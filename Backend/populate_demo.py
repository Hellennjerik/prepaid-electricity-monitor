"""
populate_demo.py - seeds the demo meters into the running Flask API.
Run order: start `python app.py` first, then `python populate_demo.py`.

  M001  Household A   REAL       DC2  solar home: the KPLC meter only sees grid use
  M002  Household B   REAL       DC3  all-grid home
  M003  Low income    SIMULATED  DC1

The real meters are built from token purchase history. household_A_purchases.csv and
household_B_purchases.csv must sit next to this file or in a data/ folder beside it.
Consumption between two purchases is the units bought divided by the days until the next
purchase, spread evenly over those days. It is an ESTIMATE from real purchases, and it
assumes the household buys a new token when the old one is nearly finished.
"""
import csv
import datetime as dt
import math
import os

import requests

import appliances_data

API_URL = "http://127.0.0.1:5000"
HERE = os.path.dirname(os.path.abspath(__file__))

DEMO_METERS = {
    "M001": dict(household="real_A", csv="household_A_purchases.csv"),
    "M002": dict(household="real_B", csv="household_B_purchases.csv", appliance_mix="rich"),
    "M003": dict(household="low_income"),
}

START_BALANCE = 1500.0   # simulated meters only: KSh on the meter 90 days ago


def check(res, step):
    if not res.ok:
        raise SystemExit(f"{step} failed ({res.status_code}): {res.text[:200]}")
    return res


# ---------------- real households: from purchase history ----------------

def find_csv(name):
    for folder in (HERE, os.path.join(HERE, "data"), os.getcwd()):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return path
    raise SystemExit(f"Cannot find {name}. Put it next to populate_demo.py or in a data/ folder beside it.")


def read_purchases(path):
    with open(path, newline="") as f:
        rows = [dict(t=dt.datetime.strptime(r["date_time"], "%Y-%m-%d %H:%M"),
                     amount=float(r["amount_ksh"]), units=float(r["units_kwh"]))
                for r in csv.DictReader(f)]
    rows.sort(key=lambda r: r["t"])
    if len(rows) < 4:
        raise SystemExit(f"{path}: need at least 4 purchases")
    return rows


def _overlap_days(a0, a1, b0, b1):
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds()) / 86400


def build_real_records(purchases, now=None):
    """
    Purchases -> (daily consumption_history rows, live balance in KSh, live kwh_used).
    Rows run from the day after the first purchase to the day before the last one, so every
    row covers a whole day. The live balance is the last purchase minus typical use since,
    never below a quarter of it (it is an estimate, and this keeps the demo from starting empty).
    """
    now = now or dt.datetime.now()
    intervals = []
    for p, nxt in zip(purchases, purchases[1:]):
        days = (nxt["t"] - p["t"]).total_seconds() / 86400
        intervals.append(dict(start=p["t"], end=nxt["t"], units=p["units"], rate=p["units"] / days,
                              ksh_per_unit=p["amount"] / p["units"]))

    first_day = purchases[0]["t"].date() + dt.timedelta(days=1)
    last_day = purchases[-1]["t"].date() - dt.timedelta(days=1)
    records, cumulative = [], 0.0
    day = first_day
    while day <= last_day:
        d0 = dt.datetime.combine(day, dt.time.min)
        d1 = d0 + dt.timedelta(days=1)
        cumulative += sum(iv["rate"] * _overlap_days(d0, d1, iv["start"], iv["end"]) for iv in intervals)
        t_end = d1 - dt.timedelta(minutes=1)
        iv = next(iv for iv in intervals if iv["start"] <= t_end < iv["end"])
        remaining = iv["units"] - iv["rate"] * (t_end - iv["start"]).total_seconds() / 86400
        records.append({"recorded_at": f"{day} 23:59:00",
                        "balance": round(max(remaining, 0.0) * iv["ksh_per_unit"], 2),
                        "kwh_used": round(cumulative, 3)})
        day += dt.timedelta(days=1)

    typical = sum(iv["rate"] for iv in intervals[-3:]) / len(intervals[-3:])      # units per day
    last = purchases[-1]
    since = (now - last["t"]).total_seconds() / 86400
    fraction_left = min(1.0, max(0.25, 1 - since / (last["units"] / typical)))
    live_balance = last["units"] * fraction_left * (last["amount"] / last["units"])
    since_rows = (now - dt.datetime.combine(last_day + dt.timedelta(days=1), dt.time.min)).total_seconds() / 86400
    live_kwh = cumulative + typical * max(0.0, since_rows)
    return records, round(live_balance, 2), round(live_kwh, 3)


# ---------------- simulated households ----------------

def build_records(history, start_balance=START_BALANCE):
    """
    Daily kWh rows -> consumption_history rows. Balance drains at the home's tier rate.
    Top-ups scale with the household: about a week of spending rounded up to the nearest
    KSh 500 (min 500), bought when the balance falls under about two days of spending.
    """
    avg = appliances_data.rolling_avg_monthly_kwh(history)
    rate = appliances_data.all_in_rate(appliances_data.tier_for(avg))
    daily_spend = avg / 30 * rate
    topup = max(500.0, math.ceil(daily_spend * 7 / 500) * 500.0)
    low_balance = max(50.0, 2 * daily_spend)

    balance, cumulative, records = start_balance, 0.0, []
    for day in history:
        balance -= day["kwh"] * rate
        cumulative += day["kwh"]
        if balance < low_balance:
            balance += topup
        records.append({
            "recorded_at": f"{day['date']} 23:59:00",
            "balance": round(balance, 2),
            "kwh_used": round(cumulative, 3),
        })
    return records, round(balance, 2), round(cumulative, 3)


# ---------------- appliances ----------------

def appliances_for(mix_key, frequency, target_monthly_kwh=None):
    """
    Appliance rows for /register_appliances: watts and average hours per day from the preset.
    With target_monthly_kwh the hours are scaled so the total matches a real household's use.
    """
    household = appliances_data.HOUSEHOLDS[mix_key]
    total = sum(appliances_data.monthly_kwh(k, household) for k in household["appliances"])
    scale = target_monthly_kwh / total if target_monthly_kwh else 1.0
    rows = []
    for key in household["appliances"]:
        watts = appliances_data._spec(key, household)["watts"]
        hours = appliances_data.monthly_kwh(key, household) * 1000 / (watts * 30) * scale
        rows.append({"name": key, "frequency": frequency, "watts": watts,
                     "hours_per_day": round(min(hours, 24.0), 4)})
    return rows


# ---------------- seeding ----------------

def seed_meter(meter, cfg):
    key = cfg["household"]
    household = appliances_data.HOUSEHOLDS[key]
    print(f"\nSeeding {meter}: {household['label']}")

    if household.get("real"):
        purchases = read_purchases(find_csv(cfg["csv"]))
        records, live_balance, live_kwh = build_real_records(purchases)
        print(f"  {len(purchases)} real purchases -> {len(records)} days of estimated consumption")
        check(requests.post(f"{API_URL}/seed_history", json={
            "meter_number": meter, "records": records}), "seed_history")
        # seed_history leaves the meter at the last day's balance; move it to where the
        # household is now (the last purchase minus typical use since).
        check(requests.post(f"{API_URL}/update_meter", json={
            "meter_number": meter, "balance": live_balance, "kwh_used": live_kwh}), "update_meter")
        mix = cfg.get("appliance_mix")
        target = household["real"]["avg_kwh_per_day"] * 30
        appliances = appliances_for(mix, "estimated", target) if mix else []
        if not appliances:
            print("  No appliance breakdown (this home's meter cannot tell us what is running).")
    else:
        print("  Generating 90 days of history from the appliance simulation...")
        history = appliances_data.generate_history(key, days=90)
        records, final_balance, final_kwh = build_records(history)
        check(requests.post(f"{API_URL}/seed_history", json={
            "meter_number": meter, "records": records}), "seed_history")
        appliances = appliances_for(key, "daily")

    check(requests.post(f"{API_URL}/register_appliances", json={
        "meter_number": meter, "appliances": appliances}), "register_appliances")
    status = check(requests.get(f"{API_URL}/meter_status/{meter}"), "meter_status").json()
    print(f"  Done: {status['tier']} at KSh {status['rate_per_kwh']}/unit, balance KSh {status['balance']}")


def create_demo_account():
    """A ready-made verified login that owns all three demo meters (needs the server started with DEV_MODE=1)."""
    res = requests.post(f"{API_URL}/dev/create_verified_user", json={
        "full_name": "Demo User", "email": "demo@nexthome.test", "phone": "0712345678",
        "gender": "prefer_not_to_say", "account_type": "home", "password": "demo12345",
        "dob": "2000-01-01", "meters": list(DEMO_METERS), "nicknames": ["Solar home", "Family house", "Small flat"]})
    if res.ok:
        print("\nDemo login created:  demo@nexthome.test  /  demo12345   (owns M001, M002, M003)")
    else:
        print("\nDemo login skipped (start the server with DEV_MODE=1 to create it).")


def seed_everything():
    for meter, cfg in DEMO_METERS.items():
        seed_meter(meter, cfg)
    create_demo_account()
    print("\nAll meters seeded. Register users with meter numbers M001, M002 and M003 in the app.")


if __name__ == "__main__":
    seed_everything()