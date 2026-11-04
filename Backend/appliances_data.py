"""
appliances_data.py
Appliance reference table + simulated Kenyan household presets for the
prepaid electricity monitor.

HOW TO READ THIS FILE
- APPLIANCES: what each appliance draws (watts) and when it typically runs.
- HOUSEHOLDS: three presets (low income / middle class / rich) listing which
  appliances a home owns, plus per-home overrides.
- load_at(): used by the household/meter simulator. Given a time, returns which
  appliances are ON and their watts. The SUM of these is what the meter sees.
- monthly_kwh() / analyze(): used by /analyze_appliances for the cost breakdown.

DATA SOURCE NOTE (cite honestly in the report)
Wattages are midpoints of typical ranges from a Google AI-generated summary of
common Kenyan household appliances. They are ASSUMPTIONS, not measurements.
Before submission, verify each value against manufacturer spec labels or the
US DOE / Energy Star appliance tables and cite those instead.

KNOWN AMBIGUITY (matters for step-change detection / NILM-lite)
These appliances have near-identical wattage, so a total-load jump alone cannot
tell them apart: kettle (2000) ~ front_load_washer (2000) ~ dishwasher (2100);
electric_cooker (2500) ~ water_boiler (2500). State this as a limitation.
"""

import datetime
import zlib

# ---------------- Tariff (billing side) ----------------
# Domestic bands gazetted 18 Sept 2026. A meter's band is set by its 3-month
# moving average of monthly kWh (automatic). The bands may be challenged in court,
# so write "as at October 2026" in the report.
# Base rates (KSh/kWh). DC2 and DC3 were checked against real token SMS (Jun-Nov 2026):
# the energy charge on tokens was 16.41-16.50 (DC2) and 18.54-18.57 (DC3). A Sept 2026
# explainer (254.ke) quoted 19.08 for DC3, but no real token showed it through 1 Nov 2026,
# so 18.57 is used; if a later token shows about 19.08, switch it back. DC1 is not yet
# verified against a real token.
TARIFF_BANDS = [          # (name, upper limit kWh/month, base rate KSh/kWh)
    ("DC1", 30, 12.23),
    ("DC2", 100, 16.45),
    ("DC3", float("inf"), 18.57),
]
# Pass-through charges change EVERY MONTH. Update these and the label together.
PASS_THROUGH_MONTH = "2026-09"
PASS_THROUGH = dict(fuel_cost=3.00, ferfa=1.1443, wrma=0.0148)   # KSh/kWh
ERC_LEVY = 0.08            # KSh/kWh
REP_LEVY_PCT = 0.05        # of the base rate
VAT = 0.16                 # applied to the whole subtotal here (an approximation)


def tier_for(avg_monthly_kwh):
    """Band for a 3-month average monthly consumption."""
    for name, upper, _ in TARIFF_BANDS:
        if avg_monthly_kwh <= upper:
            return name


def base_rate(tier):
    return next(rate for name, _, rate in TARIFF_BANDS if name == tier)


def all_in_rate(tier):
    """Estimated KSh per kWh on a token, after pass-throughs, levies and VAT."""
    base = base_rate(tier)
    subtotal = base + sum(PASS_THROUGH.values()) + ERC_LEVY + base * REP_LEVY_PCT
    return round(subtotal * (1 + VAT), 2)


def units_for_amount(ksh, tier):
    """kWh a purchase of `ksh` gives in this tier. Swap in your own
    calculate_kplc_units() here if you prefer its exact levy handling."""
    return round(ksh / all_in_rate(tier), 2)

# kind="cycling":   always plugged in, compressor/thermostat cycles; uses duty
# kind="scheduled": runs in daily windows -> ("HH:MM" start, duration minutes)
# per_person=True:  each occupant uses it back-to-back (e.g. shower)
# days=None means every day; otherwise weekday numbers (Mon=0 ... Sun=6)
APPLIANCES = {
    # ---- Always-on / cycling ----
    "fridge_single":  dict(label="Single-door fridge", watts=110, range=(90, 130), kind="cycling", duty=0.42),
    "fridge_double":  dict(label="Double-door fridge-freezer", watts=200, range=(150, 250), kind="cycling", duty=0.42),
    "chest_freezer":  dict(label="Chest freezer", watts=275, range=(200, 350), kind="cycling", duty=0.375),
    "wifi_router":    dict(label="Wi-Fi router", watts=18, range=(12, 25), kind="cycling", duty=1.0),
    "cctv_system":    dict(label="CCTV recorder + cameras", watts=65, range=(40, 90), kind="cycling", duty=1.0),
    "electric_fence": dict(label="Electric fence energizer", watts=25, range=(15, 35), kind="cycling", duty=1.0),

    # ---- Entertainment, ICT, lighting ----
    "pay_tv_decoder": dict(label="Pay-TV decoder", watts=15, range=(10, 20), kind="scheduled",
                           windows=[("06:00", 120), ("15:00", 600)]),
    "tv_32":          dict(label="32\" TV", watts=45, range=(35, 55), kind="scheduled", windows=[("18:00", 360)]),
    "tv_65":          dict(label="65\" LED TV", watts=150, range=(120, 180), kind="scheduled", windows=[("18:00", 360)]),
    "tv_85":          dict(label="85\" QLED TV", watts=325, range=(250, 400), kind="scheduled", windows=[("18:00", 300)]),
    "laptop":         dict(label="Laptop", watts=65, range=(45, 90), kind="scheduled",
                           windows=[("14:00", 120), ("19:00", 180)]),
    "phone_charger":  dict(label="Phone charger", watts=30, range=(18, 65), kind="scheduled", windows=[("21:00", 90)]),
    "led_lighting":   dict(label="LED lighting (whole house)", watts=80, range=(50, 90), kind="scheduled",
                           windows=[("18:30", 300)]),
    "floodlight":     dict(label="Outdoor security floodlight", watts=75, range=(50, 100), kind="scheduled",
                           windows=[("18:00", 720)]),
    "ceiling_fan":    dict(label="Ceiling fan", watts=70, range=(50, 90), kind="scheduled",
                           windows=[("13:00", 240), ("20:00", 240)]),

    # ---- Heat-producing: the big bill drivers ----
    "instant_shower": dict(label="Instant shower", watts=5000, range=(4500, 5500), kind="scheduled",
                           windows=[("06:00", 20)], per_person=True),
    "water_boiler":   dict(label="Storage water boiler (timer)", watts=2500, range=(2000, 3000), kind="scheduled",
                           windows=[("04:30", 150)]),
    "kettle":         dict(label="Electric kettle", watts=2000, range=(1800, 2200), kind="scheduled",
                           windows=[("06:30", 10), ("16:00", 10)]),
    "iron_box":       dict(label="Dry iron box", watts=1250, range=(1000, 1500), kind="scheduled",
                           windows=[("20:00", 30)], days=[6, 0, 1, 2, 3]),
    "electric_cooker": dict(label="Electric cooker (hotplates)", watts=2500, range=(2000, 3000), kind="scheduled",
                            windows=[("18:00", 60)]),
    "pressure_cooker": dict(label="Electric pressure cooker", watts=1050, range=(900, 1200), kind="scheduled",
                            windows=[("17:30", 60)]),
    "rice_cooker":    dict(label="Rice cooker", watts=550, range=(400, 700), kind="scheduled", windows=[("13:00", 40)]),
    "microwave":      dict(label="Microwave", watts=1250, range=(1000, 1500), kind="scheduled",
                           windows=[("07:00", 5), ("13:00", 5), ("19:30", 5)]),
    "toaster":        dict(label="Toaster", watts=1400, range=(1200, 1600), kind="scheduled", windows=[("07:15", 10)]),
    "blender":        dict(label="Blender", watts=900, range=(600, 1200), kind="scheduled", windows=[("07:30", 5)]),
    "air_fryer":      dict(label="Air fryer", watts=1850, range=(1500, 2200), kind="scheduled", windows=[("18:30", 30)]),
    "dishwasher":     dict(label="Dishwasher", watts=2100, range=(1800, 2400), kind="scheduled", windows=[("21:00", 60)]),
    "front_load_washer": dict(label="Front-load washing machine", watts=2000, range=(1800, 2200), kind="scheduled",
                              windows=[("10:00", 60)], days=[5]),
    "twin_tub_washer": dict(label="Twin-tub washing machine", watts=375, range=(300, 450), kind="scheduled",
                            windows=[("09:00", 90)], days=[5, 6]),
    "water_pump":     dict(label="Water booster pump", watts=560, range=(370, 750), kind="scheduled",
                           windows=[("06:00", 30), ("18:00", 30)]),
}

# Typical home load by hour of day (relative), used only for REAL households whose appliances
# we do not know: morning and evening peaks, quiet at night. An assumption, not a measurement.
_SHAPE_RAW = [0.55, 0.50, 0.50, 0.50, 0.55, 0.70, 1.20, 1.50, 1.20, 0.90, 0.80, 0.80,
              0.90, 0.90, 0.80, 0.80, 0.90, 1.20, 1.80, 2.00, 1.90, 1.60, 1.10, 0.75]
DAILY_SHAPE = [x / (sum(_SHAPE_RAW) / 24) for x in _SHAPE_RAW]    # average of 1.0

# Presets. "overrides" replace fields (watts / windows) for that home only.
HOUSEHOLDS = {
    "low_income": dict(
        label="Low-income household (e.g. rural / informal settlement)",
        occupants=3,
        appliances=["led_lighting", "phone_charger", "tv_32", "pay_tv_decoder"],
        overrides={"led_lighting": dict(watts=30), "phone_charger": dict(watts=10)},
    ),
    "middle_class": dict(
        label="Middle-class household (e.g. Kilimani / South C)",
        occupants=3,
        appliances=["fridge_single", "wifi_router", "tv_32", "pay_tv_decoder", "laptop", "phone_charger",
                    "led_lighting", "kettle", "microwave"],
        overrides={"led_lighting": dict(watts=50),
                   "laptop": dict(windows=[("19:00", 180)]),
                   "kettle": dict(windows=[("06:30", 10)])},
    ),
    # Calibrated to household B's real all-grid use (about 355 kWh/month, DC3). The total is
    # real; which appliances make it up is an assumption, so the split is only an estimate.
    "rich": dict(
        label="High-consumption household (calibrated to a real all-grid home, about 355 kWh/month)",
        occupants=4,
        appliances=["fridge_double", "wifi_router", "cctv_system", "electric_fence", "pay_tv_decoder",
                    "tv_65", "laptop", "phone_charger", "led_lighting", "floodlight", "water_boiler",
                    "water_pump", "kettle", "microwave", "electric_cooker", "iron_box",
                    "front_load_washer"],
        overrides={"water_boiler": dict(windows=[("04:30", 45)]),
                   "electric_cooker": dict(windows=[("18:00", 20)]),
                   "floodlight": dict(windows=[("18:00", 360)])},
    ),
    # REAL households, built from token purchase history (see populate_demo.py). The meter only
    # sees grid electricity, so there is no appliance list: the live simulator replays the real
    # average use with a typical daily pattern.
    "real_A": dict(
        label="Household A (real: solar home, grid use only)",
        occupants=0, appliances=[],
        real=dict(avg_kwh_per_day=2.20),      # 295 units over 134 days, Jun-Oct 2026
    ),
    "real_B": dict(
        label="Household B (real: all-grid home)",
        occupants=0, appliances=[],
        real=dict(avg_kwh_per_day=11.8),      # 1781 units over 151 days, Jun-Nov 2026
    ),
}


def _spec(key, household):
    spec = dict(APPLIANCES[key])
    spec.update(household.get("overrides", {}).get(key, {}))
    return spec


def _hm(text):
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _total_minutes(spec, occupants):
    mins = sum(d for _, d in spec["windows"])
    return mins * (occupants if spec.get("per_person") else 1)


def _day_fraction(spec):
    days = spec.get("days")
    return 1.0 if days is None else len(days) / 7.0


def monthly_kwh(key, household):
    """Estimated kWh per 30-day month for one appliance in one household."""
    spec = _spec(key, household)
    if spec["kind"] == "cycling":
        wh_per_day = spec["watts"] * spec["duty"] * 24
    else:
        hours = _total_minutes(spec, household["occupants"]) / 60.0
        wh_per_day = spec["watts"] * hours * _day_fraction(spec)
    return wh_per_day * 30 / 1000.0


def estimated_monthly_kwh(household_key):
    """kWh per 30 days for any household: the real average, or the sum of its appliances."""
    household = HOUSEHOLDS[household_key]
    if household.get("real"):
        return household["real"]["avg_kwh_per_day"] * 30
    return sum(monthly_kwh(k, household) for k in household["appliances"])


def analyze(household_key, ksh_per_kwh=None):
    """Breakdown for /analyze_appliances: cost and share of the bill, biggest first.
    Uses the home's KPLC tier rate unless a rate is passed in."""
    household = HOUSEHOLDS[household_key]
    if ksh_per_kwh is None:
        total_kwh = sum(monthly_kwh(k, household) for k in household["appliances"])
        ksh_per_kwh = all_in_rate(tier_for(total_kwh))
    rows = []
    for key in household["appliances"]:
        kwh = monthly_kwh(key, household)
        rows.append(dict(key=key, label=APPLIANCES[key]["label"], kwh=round(kwh, 1),
                         cost_ksh=round(kwh * ksh_per_kwh)))
    total = sum(r["kwh"] for r in rows) or 1
    for r in rows:
        r["share_pct"] = round(100 * r["kwh"] / total)
    return sorted(rows, key=lambda r: r["kwh"], reverse=True)


def _cycling_is_on(key, spec, minute_of_day):
    """Deterministic on/off cycling: ON for duty% of every 30-minute period."""
    period = 30
    offset = zlib.crc32(key.encode()) % period
    return ((minute_of_day + offset) % period) < spec["duty"] * period


def load_at(household_key, minute_of_day, weekday):
    """
    What is running right now? Returns {appliance_key: watts}.
    The simulator sums these and sends the total to the meter. Windows may
    wrap past midnight. weekday: Mon=0 ... Sun=6.
    """
    household = HOUSEHOLDS[household_key]
    occupants = household["occupants"]
    real = household.get("real")
    if real:   # real household: replay its average use with a typical daily pattern
        watts = real["avg_kwh_per_day"] * 1000 / 24 * DAILY_SHAPE[minute_of_day // 60]
        return {"household": round(watts)}
    running = {}
    for key in household["appliances"]:
        spec = _spec(key, household)
        if spec["kind"] == "cycling":
            if spec["duty"] >= 1.0 or _cycling_is_on(key, spec, minute_of_day):
                running[key] = spec["watts"]
            continue
        days = spec.get("days")
        if days is not None and weekday not in days:
            continue
        for start, duration in spec["windows"]:
            slots = occupants if spec.get("per_person") else 1
            for i in range(slots):
                begin = _hm(start) + i * duration
                if (minute_of_day - begin) % 1440 < duration:
                    running[key] = spec["watts"]
    return running


def generate_history(household_key, days=90, end_date=None):
    """
    Back-fill `days` of daily consumption by running load_at() minute by minute.
    Returns oldest-first rows: {date, kwh, by_appliance}. Save these into
    consumption_history so /predict, Reports and the 3-month tier rule have data.
    The window ends the day before `end_date` (default: today).
    """
    end_date = end_date or datetime.date.today()
    history = []
    for offset in range(days, 0, -1):
        day = end_date - datetime.timedelta(days=offset)
        by_app = {}
        for minute in range(1440):
            for key, watts in load_at(household_key, minute, day.weekday()).items():
                by_app[key] = by_app.get(key, 0.0) + watts / 60.0   # watt-hours
        history.append(dict(date=day.isoformat(),
                            kwh=round(sum(by_app.values()) / 1000, 3),
                            by_appliance={k: round(v / 1000, 3) for k, v in by_app.items()}))
    return history


def rolling_avg_monthly_kwh(history, window_days=90):
    """3-month moving average, expressed as kWh per 30 days (the tier rule's input)."""
    recent = history[-window_days:]
    return sum(d["kwh"] for d in recent) * 30 / len(recent)


if __name__ == "__main__":
    expected = {"low_income": "DC1", "middle_class": "DC2", "rich": "DC3", "real_A": "DC2", "real_B": "DC3"}
    print(f"Pass-through charges as at {PASS_THROUGH_MONTH}; bands gazetted Sept 2026\n")
    for hk, hh in HOUSEHOLDS.items():
        hist = generate_history(hk)
        avg = rolling_avg_monthly_kwh(hist)
        tier = tier_for(avg)
        rate = all_in_rate(tier)
        flag = "OK" if tier == expected[hk] else f"MISMATCH (wanted {expected[hk]})"
        print(f"{hh['label']}")
        print(f"   90-day avg {avg:.0f} kWh/month -> {tier} {flag}; ~KSh {rate}/kWh; "
              f"KSh 1000 buys {units_for_amount(1000, tier)} kWh; monthly bill ~KSh {avg * rate:,.0f}")
        for r in analyze(hk)[:3]:
            print(f"   {r['label']:<32}{r['kwh']:>7} kWh  KSh {r['cost_ksh']:>6,}  {r['share_pct']}%")
        print()

    print("Same KSh 1000 in each band:",
          ", ".join(f"{t} {units_for_amount(1000, t)} kWh" for t, _, _ in TARIFF_BANDS))
    # Consistency check: the minute-by-minute simulation should match the formula.
    hh = HOUSEHOLDS["middle_class"]
    hist = generate_history("middle_class", days=28)
    sim = sum(d["kwh"] for d in hist) * 30 / 28
    print(f"Check (middle class): simulated {sim:.0f} vs formula "
          f"{sum(monthly_kwh(k, hh) for k in hh['appliances']):.0f} kWh/month")