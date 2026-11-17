r"""
test_suite.py - unit tests and black-box tests for NextHome Energy.

    1. Start the server on a TEST copy of the project:   python app.py
    2. In another terminal:                              python test_suite.py [http://127.0.0.1:5000]

Every test prints PASS or FAIL and the results are saved to test_results.csv and test_results.md
(these are the tables used in Chapter 5 and Appendix F of the documentation).
Unit tests call the functions directly; black-box tests only use the public HTTP API, like the phone app does.
"""
import csv
import datetime
import os
import statistics
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@nexthome.test")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
RESULTS = []


def record(tid, kind, what, expected, actual, ok):
    RESULTS.append((tid, kind, what, str(expected), str(actual), "PASS" if ok else "FAIL"))
    print(f"[{'PASS' if ok else 'FAIL'}] {tid}  {what}")


def check_eq(tid, kind, what, expected, actual):
    record(tid, kind, what, expected, actual, expected == actual)


# --------------------------------------------------------------------------- UNIT TESTS
def unit_tests():
    import appliances_data as ad
    from insights import solar_flow, spike_info
    from accounts import normalise_phone, parse_threshold, clean_meter, number

    check_eq("UT-01", "Unit", "Tariff band for 25 kWh/month", "DC1", ad.tier_for(25))
    check_eq("UT-02", "Unit", "Band boundary: 30 kWh/month is still DC1", "DC1", ad.tier_for(30))
    check_eq("UT-03", "Unit", "Band boundary: 31 kWh/month is DC2", "DC2", ad.tier_for(31))
    check_eq("UT-04", "Unit", "Band boundary: 100 kWh/month is still DC2", "DC2", ad.tier_for(100))
    check_eq("UT-05", "Unit", "Band boundary: 101 kWh/month is DC3", "DC3", ad.tier_for(101))
    check_eq("UT-06", "Unit", "All-in rate DC1 (KSh/kWh)", 19.81, round(ad.all_in_rate("DC1"), 2))
    check_eq("UT-07", "Unit", "All-in rate DC2 (KSh/kWh)", 24.95, round(ad.all_in_rate("DC2"), 2))
    check_eq("UT-08", "Unit", "All-in rate DC3 (KSh/kWh)", 27.54, round(ad.all_in_rate("DC3"), 2))
    check_eq("UT-09", "Unit", "KSh 1000 buys fewer units in DC3 than DC1", True,
             ad.units_for_amount(1000, "DC3") < ad.units_for_amount(1000, "DC1"))
    check_eq("UT-10", "Unit", "Phone 0712345678 is normalised", "+254712345678", normalise_phone("0712345678"))
    check_eq("UT-11", "Unit", "Phone +254 112 345 678 is accepted", "+254112345678", normalise_phone("+254 112 345 678"))
    check_eq("UT-12", "Unit", "Non-Kenyan mobile prefix 0612345678 is rejected", None, normalise_phone("0612345678"))
    check_eq("UT-13", "Unit", "Empty alert level defaults to KSh 100", (100.0, None), parse_threshold(""))
    check_eq("UT-14", "Unit", "Negative alert level is rejected", True, parse_threshold("-5")[1] is not None)
    check_eq("UT-15", "Unit", "Alert level 'abc' is rejected", True, parse_threshold("abc")[1] is not None)
    check_eq("UT-16", "Unit", "Meter number 'm001' is cleaned to M001", "M001", clean_meter(" m001 "))
    check_eq("UT-17", "Unit", "Meter number with symbols 'M-01!' is rejected", None, clean_meter("M-01!"))
    check_eq("UT-18", "Unit", "Non-numeric history answer 'abc' is rejected", False, number({"x": "abc"}, "x"))

    f = solar_flow(1.4, 0.4, 72)
    check_eq("UT-19", "Unit", "Sunny, small load: solar covers 100%, grid 0%", (100, 0, 0), (f["solar_pct"], f["battery_pct"], f["grid_pct"]))
    f = solar_flow(0.0, 1.0, 60)
    check_eq("UT-20", "Unit", "Night, battery above reserve: battery covers 100%", (0, 100, 0), (f["solar_pct"], f["battery_pct"], f["grid_pct"]))
    f = solar_flow(0.0, 1.0, 20)
    check_eq("UT-21", "Unit", "Night, battery at 20% reserve: grid covers 100%", (0, 0, 100), (f["solar_pct"], f["battery_pct"], f["grid_pct"]))
    f = solar_flow(0.5, 1.5, 10)
    check_eq("UT-22", "Unit", "Cloudy, low battery: solar 33% + grid 67%", (33, 0, 67), (f["solar_pct"], f["battery_pct"], f["grid_pct"]))
    f = solar_flow(2.0, 0.5, 50)
    check_eq("UT-23", "Unit", "Spare solar is reported (1.5 kW) and grid is 0", (1.5, 0.0), (f["spare_solar_kw"], f["grid_kw"]))
    check_eq("UT-24", "Unit", "No load gives mode 'idle'", "idle", solar_flow(1.0, 0.0, 50)["mode"])

    today = datetime.date.today()
    usual = [((today - datetime.timedelta(days=30 - i)).isoformat(), 3.0) for i in range(27)]
    jump = [((today - datetime.timedelta(days=3 - i)).isoformat(), 4.5) for i in range(3)]
    sp = spike_info(usual + jump)
    check_eq("UT-25", "Unit", "Spike detector reports +50% when usage rises from 3.0 to 4.5 kWh/day", 50,
             sp["pct_change"] if sp else None)
    sp2 = spike_info(usual)
    check_eq("UT-26", "Unit", "Spike detector reports 0% change for steady usage (below the 30% alert level)", 0,
             sp2["pct_change"] if sp2 else None)


# ---------------------------------------------------------------------- BLACK-BOX TESTS
def j(r):
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


def timed(fn, n=40):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    out.sort()
    return round(statistics.median(out)), round(out[int(len(out) * 0.95) - 1])


def blackbox_tests():
    stamp = str(int(time.time()))
    meter, meter2 = f"T{stamp}", f"U{stamp}"
    email = f"tester{stamp}@example.com"
    payload = {"full_name": "Test Person", "email": email, "phone": "0712345678", "gender": "female",
               "account_type": "home", "password": "Pass12345", "dob": "2000-01-01", "meter_number": meter,
               "current_balance": 800, "last_topup_ksh": 1000, "last_topup_days": 8}
    s = requests.Session()

    r = requests.get(f"{API}/version", timeout=10)
    check_eq("BB-01", "Black box", "Server is reachable (/version returns 200)", 200, r.status_code)

    r = requests.get(f"{API}/meter_status/M001", timeout=10)
    check_eq("BB-02", "Black box", "Seeded meter M001 returns its status", 200, r.status_code)
    r = requests.get(f"{API}/meter_status/NOPE999", timeout=10)
    check_eq("BB-03", "Black box", "Unknown meter returns 404", 404, r.status_code)

    ms, pr = j(requests.get(f"{API}/meter_status/M001", timeout=10)), j(requests.get(f"{API}/predict/M001", timeout=10))
    try:
        expected = min(round(ms["balance"] / (pr["billing"]["avg_daily_kwh"] * pr["billing"]["rate_per_kwh"]) * 24, 1), 9999.0)
        check_eq("BB-04", "Black box", "Prediction equals balance / (daily kWh x rate) x 24", expected, pr["tokens"]["hours_until_empty"])
    except Exception as e:  # noqa: BLE001
        record("BB-04", "Black box", "Prediction equals balance / (daily kWh x rate) x 24", "formula result", repr(e), False)

    rates = {m: j(requests.get(f"{API}/meter_status/{m}", timeout=10)).get("tier") for m in ("M001", "M002", "M003")}
    check_eq("BB-05", "Black box", "Three meters sit in three different bands", {"M001": "DC2", "M002": "DC3", "M003": "DC1"}, rates)

    r = requests.post(f"{API}/auth/register", json={**payload, "password": "short"}, timeout=10)
    check_eq("BB-06", "Black box", "Sign-up with a weak password is rejected", 400, r.status_code)
    r = requests.post(f"{API}/auth/login", json={"identifier": meter, "password": payload["password"]}, timeout=10)
    check_eq("BB-07", "Black box", "Login before an account exists is refused (401)", 401, r.status_code)
    r = requests.post(f"{API}/auth/register", json={**payload, "email": "not-an-email"}, timeout=10)
    check_eq("BB-08", "Black box", "Sign-up with a malformed email is rejected", 400, r.status_code)
    r = requests.post(f"{API}/auth/register", json={**payload, "last_topup_days": ""}, timeout=10)
    check_eq("BB-09", "Black box", "A top-up answer without its number of days is rejected", 400, r.status_code)
    r = requests.post(f"{API}/auth/register", json=payload, timeout=10)
    check_eq("BB-10", "Black box", "Sign-up for a new meter succeeds and returns a session", (201, True),
             (r.status_code, bool(j(r).get("token"))))
    token = j(r).get("token", "")
    H = {"Authorization": f"Bearer {token}"}
    days = j(requests.get(f"{API}/meter_status/{meter}", timeout=10)).get("history_days", 0)
    check_eq("BB-11", "Black box", "The history answers become at least 7 days of estimated history", True, days >= 7)
    r1 = requests.post(f"{API}/auth/login", json={"identifier": meter.lower(), "password": payload["password"]}, timeout=10)
    r2 = requests.post(f"{API}/auth/login", json={"identifier": email.upper(), "password": payload["password"]}, timeout=10)
    r3 = requests.post(f"{API}/auth/login", json={"identifier": meter, "password": "wrong-password"}, timeout=10)
    check_eq("BB-12", "Black box", "Login works with the meter number or the email, and a wrong password is refused",
             (200, 200, 401), (r1.status_code, r2.status_code, r3.status_code))
    r = requests.post(f"{API}/auth/register", json=payload, timeout=10)
    r_b = requests.post(f"{API}/auth/register", json={**payload, "meter_number": meter2}, timeout=10)
    check_eq("BB-13", "Black box", "A meter number or an email cannot have two accounts (409)", (409, 409),
             (r.status_code, r_b.status_code))

    check_eq("BB-14", "Black box", "Profile needs a login token", 401, requests.get(f"{API}/me", timeout=10).status_code)
    me = j(requests.get(f"{API}/me", headers=H, timeout=10))
    check_eq("BB-15", "Black box", "Profile shows the user's own name", "Test Person", me.get("full_name"))
    r = requests.put(f"{API}/me", headers=H, json={"alert_threshold": 250}, timeout=10)
    check_eq("BB-16", "Black box", "Alert level can be changed to KSh 250", 250.0, j(r).get("alert_threshold"))
    r = requests.put(f"{API}/me", headers=H, json={"alert_threshold": "abc"}, timeout=10)
    check_eq("BB-17", "Black box", "Non-numeric alert level is rejected", 400, r.status_code)
    r = requests.post(f"{API}/me/meters", headers=H, json={"meter_number": meter2}, timeout=10)
    check_eq("BB-18", "Black box", "A second meter can be added to the account", True, r.status_code in (200, 201))

    for name in ("daily", "weekly", "monthly"):
        r = requests.get(f"{API}/reports/M002", params={"period": name}, headers=H, timeout=15)
    rep = requests.get(f"{API}/reports/M002", headers=H, timeout=15)
    check_eq("BB-19", "Black box", "Usage reports endpoint answers for a seeded meter", 200, rep.status_code)
    r = requests.get(f"{API}/planner/M002", params={"amount": 1000}, headers=H, timeout=15)
    check_eq("BB-20", "Black box", "'Will it last?' planner answers", 200, r.status_code)
    r = requests.get(f"{API}/analyze_appliances/M003", timeout=15)
    check_eq("BB-21", "Black box", "Appliance analysis answers for a meter with appliances", 200, r.status_code)
    r = requests.get(f"{API}/appliance_catalog", timeout=15)
    groups = list((j(r).get("groups") or j(r).get("categories") or {}).keys()) if isinstance(j(r), dict) else []
    check_eq("BB-22", "Black box", "Appliance catalogue is grouped by usage pattern", True, len(groups) >= 4 or "Runs all day" in r.text)

    # money flow on a dedicated meter so the demo meters are not disturbed
    mn = "M003"
    orig = j(requests.get(f"{API}/meter_status/{mn}", timeout=10))
    requests.post(f"{API}/update_meter", json={"meter_number": mn, "balance": 500, "kwh_used": orig.get("kwh_used") or 0}, timeout=10)
    before = j(requests.get(f"{API}/meter_status/{mn}", timeout=10)).get("balance")
    r = requests.post(f"{API}/report_usage", json={"meter_number": mn, "kwh_delta": 2.0}, timeout=10)
    after = j(r).get("balance")
    check_eq("BB-23", "Black box", "Reported usage reduces the balance", True, before is not None and after is not None and after < before)
    r = requests.post(f"{API}/report_usage", json={"meter_number": mn, "kwh_delta": -1}, timeout=10)
    check_eq("BB-24", "Black box", "Negative usage is rejected", 400, r.status_code)
    r = requests.post(f"{API}/buy_token", json={"meter_number": mn, "amount": 300}, timeout=10)
    new_bal = j(requests.get(f"{API}/meter_status/{mn}", timeout=10)).get("balance")
    check_eq("BB-25", "Black box", "Buying KSh 300 of tokens adds KSh 300", round(after + 300, 2), new_bal)
    check_eq("BB-26", "Black box", "A token is a 20-digit number", 20, len(str(j(r).get("token", ""))))

    # alerts
    requests.post(f"{API}/update_meter", json={"meter_number": "M003", "balance": 3, "kwh_used": orig.get("kwh_used") or 0}, timeout=10)
    a = j(requests.get(f"{API}/alerts/M003", headers=H, timeout=15))
    kinds = [x.get("kind") for x in a.get("active", [])]
    check_eq("BB-27", "Black box", "Very low credit raises a low_credit alert", True, "low_credit" in kinds)
    requests.post(f"{API}/update_meter", json={"meter_number": "M003", "balance": orig.get("balance"), "kwh_used": orig.get("kwh_used") or 0}, timeout=10)

    # solar
    sol = j(requests.get(f"{API}/solar_status/M001", timeout=10))
    check_eq("BB-28", "Black box", "Solar status includes where the power comes from", True, "flow" in sol and "solar_pct" in sol["flow"])

    # feedback
    r = requests.post(f"{API}/feedback", json={"message": "Great app", "category": "idea"}, timeout=10)
    check_eq("BB-29", "Black box", "Feedback without a login is rejected", 401, r.status_code)
    r = requests.post(f"{API}/feedback", headers=H, json={"message": "Automated test note", "category": "idea"}, timeout=10)
    check_eq("BB-30", "Black box", "Feedback with a login is saved", 201, r.status_code)

    # admin
    r = requests.get(f"{API}/admin", allow_redirects=False, timeout=10)
    check_eq("BB-31", "Black box", "Admin page without sign-in redirects to the login page", True, r.status_code in (301, 302, 303))
    adm = requests.Session()
    r = adm.post(f"{API}/admin/login", data={"email": ADMIN_EMAIL, "password": "wrong"}, allow_redirects=False, timeout=10)
    check_eq("BB-32", "Black box", "Admin login with a wrong password does not sign in", True, r.status_code not in (301, 302, 303))
    r = adm.post(f"{API}/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, allow_redirects=False, timeout=10)
    check_eq("BB-33", "Black box", "Admin login with the right password signs in", True, r.status_code in (301, 302, 303))
    tabs_ok = all(adm.get(f"{API}/admin", params={"tab": t}, timeout=15).status_code == 200
                  for t in ("overview", "accounts", "meters", "alerts", "feedback", "system"))
    check_eq("BB-34", "Black box", "All six admin tabs open", True, tabs_ok)
    page = adm.get(f"{API}/admin", params={"tab": "overview"}, timeout=15).text
    check_eq("BB-35", "Black box", "Admin overview contains charts (SVG)", True, "<svg" in page)
    acct_id = me.get("id")
    if acct_id:
        adm.post(f"{API}/admin/accounts/{acct_id}/toggle", timeout=10)
        r = requests.post(f"{API}/auth/login", json={"identifier": meter, "password": payload["password"]}, timeout=10)
        check_eq("BB-36", "Black box", "A disabled account cannot log in", 403, r.status_code)
        adm.post(f"{API}/admin/accounts/{acct_id}/toggle", timeout=10)
        r = requests.post(f"{API}/auth/login", json={"identifier": meter, "password": payload["password"]}, timeout=10)
        check_eq("BB-37", "Black box", "A re-enabled account can log in again", 200, r.status_code)
    else:
        record("BB-36", "Black box", "A disabled account cannot log in", 403, "no account id", False)
        record("BB-37", "Black box", "A re-enabled account can log in again", 200, "no account id", False)

    # security: passwords are stored hashed
    try:
        import sqlite3
        db = sqlite3.connect(os.path.join(HERE, "kplc_meter.db"))
        stored = db.execute("SELECT password_hash FROM accounts WHERE email = ?", (email,)).fetchone()
        db.close()
        check_eq("BB-38", "Black box", "Password is stored as a hash, not as text", True,
                 bool(stored) and payload["password"] not in stored[0] and len(stored[0]) > 30)
    except Exception as e:  # noqa: BLE001
        record("BB-38", "Black box", "Password is stored as a hash, not as text", "hash", repr(e), False)

    # performance (non-functional)
    perf = {}
    for label, url in (("meter_status", f"/meter_status/M001"), ("predict", f"/predict/M001"),
                       ("reports", f"/reports/M002"), ("history", f"/history/M002")):
        p50, p95 = timed(lambda u=url: requests.get(API + u, headers=H, timeout=15))
        perf[label] = (p50, p95)
    worst = max(v[1] for v in perf.values())
    check_eq("PT-01", "Performance", f"95th-percentile response under 1000 ms (per endpoint, ms p50/p95: {perf})", True, worst < 1000)
    return perf


def save(perf):
    path_csv, path_md = os.path.join(HERE, "test_results.csv"), os.path.join(HERE, "test_results.md")
    with open(path_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Type", "Test", "Expected", "Actual", "Result"])
        w.writerows(RESULTS)
    passed = sum(1 for r in RESULTS if r[5] == "PASS")
    with open(path_md, "w", encoding="utf-8") as f:
        f.write(f"# Test results ({datetime.date.today()})\n\n{passed} of {len(RESULTS)} passed.\n\n")
        f.write("| ID | Type | Test | Expected | Actual | Result |\n|---|---|---|---|---|---|\n")
        for r in RESULTS:
            f.write("| " + " | ".join(x.replace("|", "/") for x in r) + " |\n")
    print(f"\n{passed} of {len(RESULTS)} tests passed. Saved test_results.csv and test_results.md")


if __name__ == "__main__":
    unit_tests()
    try:
        perf = blackbox_tests()
    except requests.exceptions.RequestException as e:
        print("\nCould not reach the server:", e)
        perf = {}
    save(perf)