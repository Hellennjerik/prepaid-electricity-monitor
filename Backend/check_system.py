r"""
check_system.py - one command that tells you whether your whole backend is healthy.

    python Backend\check_system.py                 (server on this laptop)
    python Backend\check_system.py http://192.168.100.23:5000

Run it before every demo. It prints PASS or FAIL for each part and a final verdict.
Start the server first (python app.py) and load the demo data (python populate_demo.py).
"""
import os
import sqlite3
import sys

import requests

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
problems = []


def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}  {detail}")
    if not ok:
        problems.append(label)


def get(path, **kw):
    return requests.get(API + path, timeout=5, **kw)


print(f"\nChecking the server at {API}\n")

print("1. Is the server running?")
try:
    ok = get("/appliance_catalog").status_code == 200
except requests.exceptions.RequestException as e:
    print(f"  [FAIL] Cannot reach {API}. Start it with:  python app.py   ({type(e).__name__})")
    sys.exit(1)
check("server answers", ok)

print("\n2. Is the database healthy?")
db_path = next((p for p in (os.path.join(HERE, "kplc_meter.db"), "kplc_meter.db") if os.path.exists(p)), None)
if db_path is None:
    check("database file found (kplc_meter.db)", False, "- run the server once from this folder")
else:
    db = sqlite3.connect(db_path)
    print(f"  Database file: {os.path.abspath(db_path)}")
    for table in ("accounts", "account_meters", "meters", "consumption_history", "user_appliances", "alert_log"):
        try:
            n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            check(f"table {table}", True, f"({n} rows)")
        except sqlite3.OperationalError:
            check(f"table {table}", False, "(missing)")
    for m in ("M001", "M002", "M003"):
        n = db.execute("SELECT COUNT(*) FROM consumption_history WHERE meter_number=?", (m,)).fetchone()[0]
        check(f"{m} has usage history", n > 30, f"({n} days)  - if 0, run: python populate_demo.py")

print("\n3. Do the meters and features work?")
for m, tier in (("M001", "DC2"), ("M002", "DC3"), ("M003", "DC1")):
    r = get(f"/meter_status/{m}")
    check(f"{m} meter status", r.status_code == 200 and r.json().get("tier") == tier,
          f"({r.json().get('tier')}, KSh {r.json().get('balance')})" if r.status_code == 200 else f"(HTTP {r.status_code})")
r = get("/predict/M001")
check("prediction works", r.status_code == 200 and r.json()["tokens"]["hours_until_empty"] > 0,
      f"({r.json()['tokens']['hours_until_empty']} hours left)" if r.status_code == 200 else "")
for period in ("daily", "weekly", "monthly"):
    r = get(f"/reports/M002?period={period}")
    check(f"{period} report", r.status_code == 200 and len(r.json().get("buckets", [])) > 0)
r = get("/planner/M002?amount=1000")
check("'Will it last?' planner", r.status_code == 200 and r.json().get("units_kwh", 0) > 0)
r = get("/analyze_appliances/M002")
check("appliance analysis", r.status_code == 200 and len(r.json().get("breakdown", [])) > 0)

print("\n4. Does login work?")
r = requests.post(API + "/auth/login", json={"identifier": "ZZZ99999", "password": "nothing-here"}, timeout=5)
check("unknown meter or email is refused", r.status_code == 401)
r = requests.post(API + "/auth/login", json={"identifier": "", "password": ""}, timeout=5)
check("empty login is refused", r.status_code == 400)
r = requests.post(API + "/auth/register", json={"full_name": "X Y", "email": "bad", "phone": "0712345678",
                  "meter_number": "M001", "password": "longenough1"}, timeout=5)
check("sign-up checks the email", r.status_code == 400)

print("\n5. Admin console and feedback")
v = get("/version")
check("version endpoint", v.status_code == 200, f"(v{v.json().get('version')})" if v.status_code == 200 else "")
check("admin sign-in page", get("/admin/login").status_code == 200, "- open " + API + "/admin")
check("admin page is protected", requests.get(API + "/admin", timeout=5, allow_redirects=False).status_code == 302)
check("feedback needs a login", requests.post(API + "/feedback", json={"message": "test"}, timeout=5).status_code == 401)

print()
if problems:
    print(f"NOT READY - {len(problems)} problem(s): " + "; ".join(problems))
    sys.exit(1)
print("EVERYTHING OK - the backend is ready to demo.")