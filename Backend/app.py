from flask import Flask, request, jsonify
from flask_cors import CORS
from database import get_connection, init_db
import datetime
import os
import random
import time
import appliances_data
import solar_model

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
    """Add columns that older databases do not have yet (hours_per_day, and the simulated-meter columns)."""
    conn = get_connection()
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(user_appliances)")]
    if "hours_per_day" not in columns:
        conn.execute("ALTER TABLE user_appliances ADD COLUMN hours_per_day REAL")
    have = [row["name"] for row in conn.execute("PRAGMA table_info(meters)")]
    for name, kind in (("sim_profile", "TEXT"), ("sim_clock", "TEXT"), ("sim_at", "REAL"), ("ext_at", "REAL"),
                       ("load_w", "REAL DEFAULT 0"), ("boost_w", "REAL DEFAULT 0"), ("boost_until", "REAL"),
                       ("sol_hold_until", "REAL"), ("sol_ext_at", "REAL")):
        if name not in have:
            conn.execute(f"ALTER TABLE meters ADD COLUMN {name} {kind}")
    conn.execute("UPDATE meters SET sim_profile = CASE meter_number WHEN 'M001' THEN 'real_A' WHEN 'M002' THEN 'real_B' "
                 "WHEN 'M003' THEN 'low_income' END WHERE sim_profile IS NULL AND meter_number IN ('M001','M002','M003')")
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


# ---------------- BUILT-IN SIMULATED METER ----------------
# There is no real Kenya Power meter, so the server contains a simulated one. Each meter has a household
# profile. Whenever the app (or anything else) asks about a meter, the server first works out how much
# electricity the household would have used since the last time, using the appliances that would be on at that
# time of day, and charges it at the meter's rate. So the balance falls while the meter "runs", rises when a
# token is applied, and keeps moving with no program left running on anybody's laptop.
SIM_SPEED = float(os.environ.get("SIM_SPEED", "1"))                    # simulated seconds per real second (1 = real time)
SIM_MAX_CATCHUP_S = float(os.environ.get("SIM_MAX_CATCHUP_MIN", "120")) * 60   # longest gap that is charged in one go
SIM_EXTERNAL_HOLD_S = 120                                               # pause the built-in meter while meter_gui.py reports
DEFAULT_SIM_PROFILE = "low_income"                                      # for meters that are not one of the demo homes
NAIROBI = datetime.timezone(datetime.timedelta(hours=3))


def sim_step(profile, clock, boost_w=0.0):
    """Power in watts of a simulated household at a given clock time (pure function, easy to test)."""
    running = appliances_data.load_at(profile, clock.hour * 60 + clock.minute, clock.weekday())
    return sum(running.values()) + boost_w


def advance_sim(meter_number):
    """Charge the built-in meter for the time that has passed. Returns the kWh added (0 if nothing was due).
    A home with solar is simulated too: the sun, the battery and the grid share the load, and only the grid part is charged."""
    now = time.time()
    conn = get_connection()
    try:
        m = conn.execute("SELECT balance, sim_profile, sim_clock, sim_at, ext_at, boost_w, boost_until, sol_hold_until, sol_ext_at "
                         "FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
        if not m:
            return 0.0
        if m["ext_at"] and now - m["ext_at"] < SIM_EXTERNAL_HOLD_S:
            return 0.0                      # a laptop meter is reporting; do not count twice
        profile = m["sim_profile"] if m["sim_profile"] in appliances_data.HOUSEHOLDS else DEFAULT_SIM_PROFILE
        last = m["sim_at"]
        if last is None or not m["sim_clock"]:
            conn.execute("UPDATE meters SET sim_at = ?, sim_clock = ?, sim_profile = ? WHERE meter_number = ?",
                         (now, datetime.datetime.now(NAIROBI).replace(tzinfo=None).isoformat(), profile, meter_number))
            conn.commit()
            return 0.0
        real_elapsed = min(max(now - last, 0.0), SIM_MAX_CATCHUP_S)
        if real_elapsed < 2.0:
            return 0.0
        # claim the interval, so two requests at the same moment cannot charge it twice
        claimed = conn.execute("UPDATE meters SET sim_at = ? WHERE meter_number = ? AND sim_at = ?",
                               (now, meter_number, last))
        conn.commit()
        if claimed.rowcount != 1:
            return 0.0
        clock = datetime.datetime.fromisoformat(m["sim_clock"])
        rate = billing_info(conn, meter_number)["rate"]

        solar = conn.execute("SELECT * FROM solar_state WHERE meter_number = ? ORDER BY id DESC LIMIT 1", (meter_number,)).fetchone()
        sol_hold = bool(solar) and ((m["sol_hold_until"] or 0) > now or (m["sol_ext_at"] and now - m["sol_ext_at"] < SIM_EXTERNAL_HOLD_S))
        if solar:
            battery_kwh = (solar["battery_percent"] if solar["battery_percent"] is not None else 65.0) / 100.0 * solar_model.BATTERY_KWH
            held_gen = solar["generation_kw"] or 0.0
            made_today = solar["solar_kwh_today"] or 0.0
            last_gen = held_gen; last_home = solar["load_kw"] or 0.0

        balance, kwh_total, load_w, done = float(m["balance"]), 0.0, 0.0, 0.0
        remaining = real_elapsed * SIM_SPEED
        while remaining > 0:
            step = min(60.0, remaining)
            hours = step / 3600.0
            boost = float(m["boost_w"] or 0) if m["boost_until"] and (last + done / SIM_SPEED) < m["boost_until"] else 0.0
            home_w = sim_step(profile, clock, boost)
            if solar:
                hour = clock.hour + clock.minute / 60.0
                gen = held_gen if sol_hold else solar_model.generation_kw(hour)
                flow = solar_model.solar_tick(gen, home_w / 1000.0, battery_kwh, hours)
                grid_kw = flow["grid_kw"] if balance > 0 else 0.0          # no credit: the grid is cut off
                if not sol_hold:
                    battery_kwh = flow["battery_kwh"]
                    if clock.date() != (clock + datetime.timedelta(seconds=step)).date():
                        made_today = 0.0
                    made_today += gen * hours
                last_gen, last_home = gen, home_w / 1000.0
                load_w = grid_kw * 1000.0                                     # what the prepaid meter sees
                kwh = grid_kw * hours
            else:
                load_w = home_w if balance > 0 else 0.0                       # no credit: the supply is cut
                kwh = load_w / 1000.0 * hours
            balance = max(0.0, balance - kwh * rate)
            kwh_total += kwh
            clock += datetime.timedelta(seconds=step)
            remaining -= step; done += step
        cost = kwh_total * rate
        conn.execute("UPDATE meters SET balance = MAX(0, balance - ?), kwh_used = kwh_used + ?, sim_clock = ?, load_w = ?, "
                     "last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
                     (cost, kwh_total, clock.isoformat(), load_w, meter_number))
        if solar and not sol_hold:
            conn.execute("UPDATE solar_state SET battery_percent = ?, generation_kw = ?, load_kw = ?, solar_kwh_today = ?, "
                         "last_updated = CURRENT_TIMESTAMP WHERE id = ?",
                         (round(battery_kwh / solar_model.BATTERY_KWH * 100.0, 1), round(last_gen, 2), round(last_home, 2),
                          round(made_today, 3), solar["id"]))
        # keep a reading every half hour so the daily usage history keeps growing
        recent = conn.execute("SELECT 1 FROM consumption_history WHERE meter_number = ? AND recorded_at > datetime('now', '-30 minutes')",
                              (meter_number,)).fetchone()
        if not recent:
            now_row = conn.execute("SELECT balance, kwh_used FROM meters WHERE meter_number = ?", (meter_number,)).fetchone()
            conn.execute("INSERT INTO consumption_history (meter_number, balance, kwh_used) VALUES (?,?,?)",
                         (meter_number, now_row["balance"], now_row["kwh_used"]))
        conn.commit()
    finally:
        conn.close()
    insights.check_and_notify(meter_number)
    return kwh_total


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
    advance_sim(meter_number)
    conn = get_connection()
    meter = conn.execute(
        "SELECT * FROM meters WHERE meter_number = ?", (meter_number,)
    ).fetchone()
    if not meter:
        conn.close()
        return jsonify({"error": "Meter not found"}), 404

    info = billing_info(conn, meter_number)
    last_token = conn.execute("SELECT amount FROM tokens WHERE meter_number = ? ORDER BY id DESC LIMIT 1", (meter_number,)).fetchone()
    conn.close()

    out = dict(meter)
    for hidden in ("sim_profile", "sim_clock", "sim_at", "ext_at", "boost_w", "boost_until", "sol_hold_until", "sol_ext_at"):
        out.pop(hidden, None)
    out["load_w"] = round(out.get("load_w") or 0)
    out["units_left"] = round(out["balance"] / info["rate"], 2) if info["rate"] else 0.0   # what a real meter shows as "balance credit"
    out["last_charge_ksh"] = float(last_token["amount"]) if last_token else None
    out["balance"] = round(out["balance"], 2)
    out["kwh_used"] = round(out["kwh_used"], 6)
    out["tier"] = info["tier"]                  # new: e.g. "DC2"
    out["rate_per_kwh"] = info["rate"]          # new: KSh per unit on a token
    out["history_days"] = info["history_days"]  # how many full days of usage we have learned from
    return jsonify(out), 200


METER_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prepaid Meter __M__</title>
<style>
body{margin:0;background:#1a1d22;color:#e5e7eb;font-family:Arial,Helvetica,sans-serif;padding:16px;text-align:center}
.meter{max-width:380px;margin:0 auto;background:#eceae4;border-radius:18px;padding:16px 16px 22px;box-shadow:0 6px 24px #0008;color:#222}
.corner{position:fixed;top:10px;right:14px;font-size:15px;font-weight:bold;color:#22d3ee;border:1px solid #22d3ee;border-radius:4px;padding:3px 9px;background:#1a1d22}
.brand{font-weight:bold;letter-spacing:3px;font-size:15px;margin:2px 0 8px}
.lcd{background:#b9c19a;border:6px solid #8d8f87;border-radius:8px;height:116px;position:relative;box-shadow:inset 0 2px 6px #0005;color:#262b1e;font-family:"Courier New",monospace}
.code{position:absolute;left:10px;top:6px;font-size:17px;opacity:.75}
.val{position:absolute;right:62px;bottom:12px;font-size:50px;font-weight:bold;letter-spacing:2px;line-height:1}
.unit{position:absolute;right:10px;bottom:14px;font-size:15px;font-weight:bold}
.row{display:flex;align-items:center;gap:8px;margin:10px 4px 0;font-size:12px;text-align:left}
.led{width:14px;height:14px;border-radius:50%;background:#2d3;box-shadow:0 0 8px #2d3}
.led.low{background:#e22;box-shadow:0 0 10px #e22;animation:b 1s infinite}@keyframes b{50%{opacity:.25}}
.flap{background:#d9dcd3;border:1px solid #b5b8ad;border-radius:8px;margin:12px 0 10px;padding:8px 10px;font-size:12px;text-align:left;display:grid;grid-template-columns:1fr 1fr;gap:4px 10px}
.flap b{display:inline-block;width:34px}
.keys{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;max-width:250px;margin:10px auto 0}
.keys button{height:46px;border:0;border-radius:23px;background:#a9b5c4;color:#223;font-size:20px;box-shadow:0 3px #7e8998;cursor:pointer}
.keys button:active{transform:translateY(2px);box-shadow:0 1px #7e8998}
.status{font-size:13px;margin-top:14px;min-height:18px}
.home{max-width:380px;margin:14px auto 0;background:#262a31;border-radius:12px;padding:12px;color:#e5e7eb}
.ht{font-weight:bold;font-size:14px;margin-bottom:4px}.hl{font-size:13px;color:#9ca3af;margin-bottom:8px}
.hb button{margin:0 4px;padding:9px 14px;border:0;border-radius:8px;font-size:14px;cursor:pointer;background:#f59e0b;color:#222}.hb button+button{background:#4b5563;color:#fff}
.hint{color:#9ca3af;font-size:12px;margin-top:14px;max-width:380px;margin-left:auto;margin-right:auto}
</style></head><body>
<div class="corner">__M__</div>
<div class="meter">
 <div class="brand">PREPAID METER</div>
 <div class="lcd"><div class="code" id="code">801</div><div class="val" id="val">-</div><div class="unit" id="unit">kWh</div></div>
 <div class="row"><div class="led" id="led"></div><span id="ledtxt">Green: enough credit &nbsp; Red: low credit</span></div>
 <div class="flap">
  <div><b>800</b>Total energy consumption</div><div><b>801</b>Balance credit</div>
  <div><b>804</b>Meter number</div><div><b>805</b>SGC number</div>
  <div><b>812</b>Cancel alarm</div><div><b>817</b>Last charge amount</div>
 </div>
 <div class="keys" id="keys"></div>
 <div class="status" id="status" style="color:#555">Connecting...</div>
</div>
<div class="hint">Type a code (for example 801) and press <b>&crarr;</b> to read that value. Type a 20-digit token and press <b>&crarr;</b> to add credit. <b>&larr;</b> deletes. Simulated meter: the phone app reads the same server.</div>
<script>
const M="__M__";let page="801",buf="",D=null,flashUntil=0;
const names={"800":["kWh",d=>d.kwh_used.toFixed(2)],"801":["kWh",d=>d.units_left.toFixed(2)],"804":["",d=>M],"805":["",d=>"600001"],
 "812":["",d=>"--"],"817":["KSh",d=>d.last_charge_ksh==null?"0.00":d.last_charge_ksh.toFixed(2)]};
function draw(){
 const v=document.getElementById("val"),u=document.getElementById("unit"),c=document.getElementById("code");
 if(buf){c.textContent="";v.textContent=buf.length>9?"..."+buf.slice(-8):buf;u.textContent="";v.style.fontSize=buf.length>6?"30px":"50px";return;}
 v.style.fontSize="50px";c.textContent=page;
 if(!D){v.textContent="-";return;}
 const n=names[page];let t=n[1](D);v.textContent=t;u.textContent=n[0];if(t.length>9)v.style.fontSize="34px";
}
async function tick(){
 const st=document.getElementById("status");
 try{
  const r=await fetch("/meter_status/"+M,{cache:"no-store"});
  if(r.status===404){st.textContent="Meter "+M+" not found";st.style.color="#c00";return;}
  D=await r.json();
  const low=D.balance<=100;const led=document.getElementById("led");led.className="led"+(low?" low":"");
  if(Date.now()>flashUntil){st.textContent=D.balance<=0?"No credit: supply cut off":"Live: synced with the app";st.style.color=D.balance<=0?"#c00":"#276";}
  draw();
 }catch(e){st.textContent="Server not reachable";st.style.color="#c60";}
}
function say(t,c){const st=document.getElementById("status");st.textContent=t;st.style.color=c;flashUntil=Date.now()+4000;}
async function enter(){
 if(!buf)return;
 if(buf.length===20){
  say("Processing token...","#a60");const t=buf;buf="";draw();
  try{const r=await fetch("/apply_token",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({token_string:t,meter_number:M})});
   say(r.ok?"Token accepted":"Token rejected",r.ok?"#276":"#c00");setTimeout(tick,400);}catch(e){say("Server not reachable","#c60");}
  return;}
 if(names[buf]){page=buf;buf="";if(page==="812")say("Alarm cancelled","#276");draw();return;}
 say("Invalid entry","#c00");buf="";draw();
}
function press(k){
 if(k==="<"){buf=buf.slice(0,-1);}
 else if(k==="E"){enter();return;}
 else if(buf.length<20){buf+=k;}
 draw();
}
const keys=document.getElementById("keys");
["1","2","3","4","5","6","7","8","9","<","0","E"].forEach(k=>{const b=document.createElement("button");b.textContent=k==="<"?"←":k==="E"?"↵":k;b.onclick=()=>press(k);keys.appendChild(b);});
document.addEventListener("keydown",e=>{if(/^[0-9]$/.test(e.key))press(e.key);else if(e.key==="Backspace")press("<");else if(e.key==="Enter")press("E");});
tick();setInterval(tick,2000);
</script></body></html>"""


@app.route("/meter/<meter_number>", methods=["GET"])
def meter_screen(meter_number):
    """A web page that looks like the meter's display and shows the same balance as the app."""
    safe = "".join(c for c in meter_number.upper() if c.isalnum())[:20]
    return METER_PAGE.replace("__M__", safe), 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


HOME_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Home __M__</title>
<style>
body{margin:0;background:#1a1d22;color:#e5e7eb;font-family:Arial,Helvetica,sans-serif;text-align:center;padding:20px}
.card{max-width:380px;margin:0 auto;background:#262a31;border-radius:14px;padding:20px}
h1{font-size:20px;margin:4px 0 2px}.sub{color:#9ca3af;font-size:13px}
.big{font-size:44px;font-weight:bold;margin:16px 0 0;color:#f59e0b}.u{color:#9ca3af;font-size:13px;margin-bottom:14px}
button{margin:6px;padding:12px 18px;border:0;border-radius:10px;font-size:16px;cursor:pointer;background:#f59e0b;color:#222}button.off{background:#4b5563;color:#fff}
.st{font-size:14px;margin-top:12px;min-height:20px}.hint{color:#9ca3af;font-size:12px;margin-top:16px}
</style></head><body><div class="card">
<h1>Home __M__</h1><div class="sub">Simulated household appliances</div>
<div class="big" id="kw">-</div><div class="u">kW the home is using now</div>
<div id="heat" class="st"></div>
<button onclick="heater('on')">Switch on heater (3 kW)</button><button class="off" onclick="heater('off')">Switch off</button>
<div class="st" id="bal"></div>
<div class="hint">This page stands in for the people and appliances in the house. Watch the meter page (/meter/__M__) and the phone app react.</div>
</div><script>
const M="__M__";
async function heater(a){try{await fetch("/home_heater/"+M+"/"+a,{method:"POST"});}catch(e){}setTimeout(tick,1500);}
async function tick(){try{const r=await fetch("/meter_status/"+M,{cache:"no-store"});
 if(r.status===404){document.getElementById("bal").textContent="Meter "+M+" not found";return;}
 const d=await r.json();document.getElementById("kw").textContent=((d.load_w||0)/1000).toFixed(2);
 document.getElementById("heat").textContent=(d.load_w||0)>=3000?"Heater is ON":"Heater is off";
 document.getElementById("bal").textContent="Credit left: "+d.units_left.toFixed(2)+" kWh (KSh "+d.balance.toFixed(2)+")";}catch(e){}}
tick();setInterval(tick,2000);
</script></body></html>"""


@app.route("/home/<meter_number>", methods=["GET"])
def home_screen(meter_number):
    """Demo page: the household's appliances (a heater switch) for a simulated meter."""
    safe = "".join(c for c in meter_number.upper() if c.isalnum())[:20]
    return HOME_PAGE.replace("__M__", safe), 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


@app.route("/home_heater/<meter_number>/<action>", methods=["POST"])
def home_heater(meter_number, action):
    """Demo: the household switches a 3 kW heater on (15 minutes) or off, from the meter or solar page.
    It stands in for a real appliance at home, so usage visibly goes up and the usage alert can fire."""
    if action not in ("on", "off"):
        return jsonify({"error": "Unknown action"}), 404
    meter = "".join(c for c in meter_number.upper() if c.isalnum())[:20]
    conn = get_connection()
    try:
        if not conn.execute("SELECT 1 FROM meters WHERE meter_number = ?", (meter,)).fetchone():
            return jsonify({"error": "Meter not found"}), 404
        # optional ?watts=N lets the app switch several appliances on at once (their total power); default is one 3 kW heater
        watts = request.args.get("watts", default=3000.0, type=float)
        watts = max(0.0, min(watts if watts == watts else 0.0, 15000.0))
        on = action == "on" and watts > 0
        conn.execute("UPDATE meters SET boost_w = ?, boost_until = ? WHERE meter_number = ?",
                     (watts if on else 0.0, time.time() + 1800 if on else None, meter))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok", "heater": action, "watts": watts if on else 0})


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
    try:
        load_w = float(data.get("load_w")) if data.get("load_w") is not None else None
    except (TypeError, ValueError):
        load_w = None
    # a reporting meter takes over from the built-in one for the next two minutes (see advance_sim)
    conn.execute(
        "UPDATE meters SET balance = MAX(0, balance - ?), kwh_used = kwh_used + ?, "
        "ext_at = ?, sim_at = ?, load_w = COALESCE(?, load_w), "
        "last_updated = CURRENT_TIMESTAMP WHERE meter_number = ?",
        (kwh_delta * info["rate"], kwh_delta, time.time(), time.time(), load_w, meter_number),
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

SOLAR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Solar System __M__</title>
<style>
body{margin:0;background:#0a1a2a;color:#fff;font-family:Arial,Helvetica,sans-serif;text-align:center;padding:18px}
.card{max-width:480px;margin:0 auto}
.corner{position:fixed;top:10px;right:14px;font-size:15px;font-weight:bold;color:#ffcc00;border:1px solid #ffcc00;border-radius:4px;padding:3px 9px}
h1{font-size:20px;margin:8px 0 2px;color:#ffcc00}.sub{color:#8aa;font-size:13px}
.batt{font-size:54px;font-weight:bold;margin:14px 0 0}.lbl{color:#8aa;font-size:13px;margin-bottom:10px}
.row{display:flex;justify-content:space-around;margin:12px 0}.row div{flex:1}.row b{display:block;font-size:22px}
.bar{display:flex;height:26px;border-radius:5px;overflow:hidden;background:#223;margin:14px 0 6px}
.bar span{display:block;height:100%}.legend{font-size:13px;color:#cde;display:flex;justify-content:space-around}
.note{margin-top:16px;font-size:14px;color:#cde;min-height:40px}.status{font-size:13px;margin-top:14px}
.home{margin:18px auto 0;background:#12293f;border-radius:12px;padding:12px}.ht{font-weight:bold;font-size:14px;margin-bottom:8px}
.hb button{margin:0 4px;padding:9px 14px;border:0;border-radius:8px;font-size:14px;cursor:pointer;background:#f59e0b;color:#222}.hb button+button{background:#4b5563;color:#fff}
.small{color:#789;font-size:12px;margin-top:22px}
</style></head><body><div class="card">
<div class="corner">__M__</div>
<h1>SOLAR SYSTEM</h1><div class="sub" id="sub">Meter __M__</div>
<div class="batt" id="batt">-</div><div class="lbl">Battery</div>
<div class="row"><div><b id="gen">-</b>Sun power (made)</div><div><b id="load">-</b>Home is using</div><div><b id="today">-</b>Made today</div></div>
<div class="sub">Where the home's power comes from</div>
<div class="bar"><span id="b_sun" style="background:#ffcc00"></span><span id="b_bat" style="background:#34d399"></span><span id="b_grid" style="background:#fb7185"></span></div>
<div class="legend"><span style="color:#ffcc00">Solar <b id="p_sun">0</b>%</span><span style="color:#34d399">Battery <b id="p_bat">0</b>%</span><span style="color:#fb7185">Grid <b id="p_grid">0</b>%</span></div>
<div class="note" id="note"></div>
<div class="status" id="status" style="color:#8aa">Connecting...</div>
<div class="small">The simulated sun and battery follow the time of day. This page shows the same values as the phone app.</div>
</div>
<script>
const M="__M__";
async function tick(){
 const st=document.getElementById("status");
 try{
  const r=await fetch("/solar_status/"+M,{cache:"no-store"});
  if(r.status===404){st.textContent="Meter "+M+" has no solar system";st.style.color="#f87171";return;}
  const d=await r.json(),f=d.flow||{};
  const bp=d.battery_percent;document.getElementById("batt").textContent=bp.toFixed(0)+"%";
  document.getElementById("batt").style.color=bp<=20?"#f87171":bp<=40?"#fb923c":"#34d399";
  document.getElementById("gen").textContent=d.generation_kw.toFixed(2)+" kW";
  document.getElementById("load").textContent=d.load_kw.toFixed(2)+" kW";
  document.getElementById("today").textContent=d.solar_kwh_today.toFixed(2)+" kWh";
  document.getElementById("b_sun").style.width=(f.solar_pct||0)+"%";document.getElementById("b_bat").style.width=(f.battery_pct||0)+"%";document.getElementById("b_grid").style.width=(f.grid_pct||0)+"%";
  document.getElementById("p_sun").textContent=f.solar_pct||0;document.getElementById("p_bat").textContent=f.battery_pct||0;document.getElementById("p_grid").textContent=f.grid_pct||0;
  document.getElementById("note").textContent=f.note||"";
  st.textContent="Live: synced with the app";st.style.color="#22c55e";
 }catch(e){st.textContent="Server not reachable";st.style.color="#fb923c";}
}
tick();setInterval(tick,2000);
</script></body></html>"""


@app.route("/solar/<meter_number>", methods=["GET"])
def solar_screen(meter_number):
    """A web page that shows the sun, the battery and where the home's power comes from."""
    safe = "".join(c for c in meter_number.upper() if c.isalnum())[:20]
    return SOLAR_PAGE.replace("__M__", safe), 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


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

    conn.execute("UPDATE meters SET sol_ext_at = ? WHERE meter_number = ?", (time.time(), meter_number))   # a laptop inverter is reporting: pause the built-in one
    conn.commit()
    conn.close()
    return jsonify({"message": "Solar updated"}), 200


@app.route("/solar_status/<meter_number>", methods=["GET"])
def solar_status(meter_number):
    advance_sim(meter_number)
    conn = get_connection()
    solar = conn.execute("""
        SELECT * FROM solar_state WHERE meter_number = ?
        ORDER BY id DESC LIMIT 1
    """, (meter_number,)).fetchone()
    conn.close()
    if solar:
        import insights   # imported here because insights is loaded after this file
        data = dict(solar)
        data["flow"] = insights.solar_flow(data["generation_kw"], data["load_kw"], data["battery_percent"])
        return jsonify(data), 200
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
    advance_sim(meter_number)
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
import admin      # noqa: E402

accounts.init_account_tables()
insights.init(billing_info, daily_kwh_series, accounts.phones_for_meter, advance_sim)
app.register_blueprint(accounts.bp)
app.register_blueprint(insights.bp)
admin.init(billing_info, daily_kwh_series)
app.register_blueprint(admin.bp)


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("DEBUG") == "1")