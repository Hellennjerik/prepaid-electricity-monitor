"""
meter_gui.py - KPLC smart meter simulator (Tkinter).

Usage:  python meter_gui.py [M001|M002|M003]      (default M001)

Like a real smart meter, this only MEASURES. Each second it works out the household's
load from appliances_data.load_at() (which appliances are on at the simulated time),
turns that into kWh, and reports the kWh to the server with POST /report_usage.

The server owns the balance and the tariff: it charges the kWh at the meter's KPLC tier
rate and sends the new balance back. So a token bought on the phone is picked up on the
next tick and can never be overwritten by the meter. If the server is offline the meter
keeps counting and reports everything it measured once the connection returns.

Restart this window after running populate_demo.py so it picks up the seeded state.
appliances_data.py must sit in the same folder.
"""
import datetime
import sys
import threading
import time
import tkinter as tk

import requests

import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'Backend')))   # finds appliances_data.py if this file is in another folder

import appliances_data

API_URL = "http://127.0.0.1:5000"
# M001 and M002 are REAL households (replaying their real average use with a typical daily
# pattern); M003 is a simulated low-income home built from appliances.
METERS = {"M001": "real_A", "M002": "real_B", "M003": "low_income"}

# How fast simulated time runs. 1.0 = one simulated minute per real second
# (a day passes in 24 minutes). Predictions are in household hours, so at 1.0 an
# "hour left" on the phone is one real minute. Use 60 for real time.
SIM_MINUTES_PER_SECOND = 1.0


class MeterModel:
    """The household's load and energy counter. No network, no GUI."""

    def __init__(self, household_key, balance=0.0, kwh_used=0.0, start=None):
        self.household_key = household_key
        self.balance = balance
        self.kwh_used = kwh_used
        self.clock = start or datetime.datetime.now().replace(second=0, microsecond=0)
        self._carry = 0.0
        self.load_w = 0
        self.running = {}
        # Local estimate of the tier until the server tells us the real one.
        self.set_billing(appliances_data.tier_for(appliances_data.estimated_monthly_kwh(household_key)))

    def set_billing(self, tier, rate=None):
        self.tier = tier
        self.rate = rate if rate is not None else appliances_data.all_in_rate(tier)

    def advance(self, sim_minutes):
        """Move simulated time forward and return the kWh consumed meanwhile."""
        self._carry += sim_minutes
        consumed = 0.0
        while self._carry >= 1.0:
            self._carry -= 1.0
            consumed += self._one_minute()
        return consumed

    def _one_minute(self):
        minute_of_day = self.clock.hour * 60 + self.clock.minute
        self.running = appliances_data.load_at(self.household_key, minute_of_day, self.clock.weekday())
        self.load_w = sum(self.running.values())
        kwh = self.load_w / 1000.0 / 60.0
        self.kwh_used += kwh
        self.balance = max(0.0, self.balance - kwh * self.rate)   # local estimate; the server's value replaces it
        self.clock += datetime.timedelta(minutes=1)
        return kwh


class MeterLink:
    """Network calls to the Flask API. Only used off the GUI thread
    (apart from one pull at startup)."""

    def __init__(self, meter_number):
        self.meter_number = meter_number

    def pull(self):
        try:
            r = requests.get(f"{API_URL}/meter_status/{self.meter_number}", timeout=2)
            if r.status_code == 200:
                data = r.json()
                return {"balance": float(data.get("balance", 0)),
                        "kwh_used": float(data.get("kwh_used", 0)),
                        "tier": data.get("tier"), "rate": data.get("rate_per_kwh")}
        except (requests.exceptions.RequestException, ValueError):
            pass
        return None

    def report(self, kwh_delta):
        """Returns ("ok", data), ("missing", None) if the meter is unknown, or ("offline", None)."""
        try:
            r = requests.post(f"{API_URL}/report_usage", json={
                "meter_number": self.meter_number, "kwh_delta": kwh_delta}, timeout=2)
        except requests.exceptions.RequestException:
            return "offline", None
        if r.status_code == 200:
            try:
                return "ok", r.json()
            except ValueError:
                return "offline", None
        if r.status_code == 404:
            return "missing", None
        return "offline", None


class SmartMeterApp:
    def __init__(self, root, meter_number, profile):
        self.root = root
        self.meter_number = meter_number
        self.link = MeterLink(meter_number)
        self.model = MeterModel(profile)
        self.lock = threading.Lock()          # guards model, pending_kwh and status
        self.pending_kwh = 0.0                # measured but not yet reported to the server
        self.status = ("Connecting...", "gray")
        self._alive = True

        start = self.link.pull()              # continue from the seeded state
        if start:
            self.model.balance = start["balance"]
            self.model.kwh_used = start["kwh_used"]
            if start["tier"]:
                self.model.set_billing(start["tier"], start["rate"])

        root.title("KPLC Smart Meter Simulator")
        root.geometry("470x600")
        root.configure(bg="black")
        root.protocol("WM_DELETE_WINDOW", self._close)

        tk.Label(root, text="KPLC PREPAID METER", font=("Arial", 14, "bold"),
                 fg="white", bg="black").pack(pady=(10, 0))
        self.tier_label = tk.Label(root, text="", font=("Arial", 10), fg="gray", bg="black")
        self.tier_label.pack()
        self.clock_label = tk.Label(root, text="", font=("Arial", 10), fg="gray", bg="black")
        self.clock_label.pack()

        self.energy_label = tk.Label(root, text="0.0000 kWh", font=("Arial", 34, "bold"), fg="cyan", bg="black")
        self.energy_label.pack(pady=8)
        self.credit_label = tk.Label(root, text="Credit: KSh 0.00", font=("Arial", 18), fg="yellow", bg="black")
        self.credit_label.pack(pady=4)
        self.load_label = tk.Label(root, text="Load: 0 W", font=("Arial", 14), fg="white", bg="black")
        self.load_label.pack(pady=4)
        self.running_label = tk.Label(root, text="", font=("Arial", 10), fg="#aaa", bg="black",
                                      wraplength=430, justify="center")
        self.running_label.pack(pady=4)

        tk.Label(root, text="Enter 20-digit Token (optional):", font=("Arial", 10),
                 fg="white", bg="black").pack(pady=(14, 4))
        self.token_entry = tk.Entry(root, font=("Arial", 12), justify="center",
                                    bg="#222", fg="white", insertbackground="white")
        self.token_entry.pack(pady=4, ipady=5, padx=20, fill="x")
        tk.Button(root, text="Submit Token", font=("Arial", 11, "bold"), bg="#00aa00", fg="white",
                  command=self.submit_token).pack(pady=8)

        self.status_label = tk.Label(root, text="Connecting...", font=("Arial", 10),
                                     fg="gray", bg="black", wraplength=430)
        self.status_label.pack(side="bottom", pady=10)

        threading.Thread(target=self._worker, daemon=True).start()
        self._refresh_gui()

    # ---- background thread: all simulation and network work ----
    def _worker(self):
        last = time.monotonic()
        while self._alive:
            time.sleep(1.0)
            now = time.monotonic()
            elapsed, last = now - last, now          # real elapsed time, so no drift
            self._tick_once(elapsed)

    def _tick_once(self, elapsed_seconds):
        with self.lock:
            self.pending_kwh += self.model.advance(elapsed_seconds * SIM_MINUTES_PER_SECOND)
            to_send = self.pending_kwh

        state, data = self.link.report(to_send)      # network call, outside the lock

        with self.lock:
            if state == "ok":
                self.pending_kwh -= to_send
                self.model.balance = data["balance"]  # the server owns the balance
                self.model.kwh_used = data["kwh_used"]
                self.model.set_billing(data["tier"], data["rate"])
                self.status = ("Synced with server", "green")
            elif state == "missing":
                self.status = (f"Meter {self.meter_number} not found - run populate_demo.py or register it", "red")
            else:
                self.status = ("Server offline - measuring locally", "orange")

    # ---- GUI thread only: reads a snapshot, never touches the network ----
    def _refresh_gui(self):
        with self.lock:
            m = self.model
            balance, kwh, load_w = m.balance, m.kwh_used, m.load_w
            top = sorted(m.running, key=lambda k: -m.running[k])[:4]
            names = [appliances_data.APPLIANCES.get(k, {}).get("label", "Whole household") for k in top]
            clock, status, tier, rate = m.clock, self.status, m.tier, m.rate

        self.tier_label.config(text=f"Meter {self.meter_number}  |  {tier}  |  KSh {rate}/kWh")
        self.energy_label.config(text=f"{kwh:.4f} kWh")
        self.credit_label.config(
            text=f"Credit: KSh {balance:.2f}",
            fg="red" if balance <= 0 else "orange" if balance <= 20 else "yellow")
        self.load_label.config(text=f"Load: {load_w / 1000:.2f} kW" if load_w >= 1000 else f"Load: {load_w} W")
        self.running_label.config(text="Biggest loads now: " + (", ".join(names) if names else "none"))
        self.clock_label.config(text=f"Simulated time: {clock:%a %H:%M}  (x{SIM_MINUTES_PER_SECOND:g} min/sec)")
        self.status_label.config(text=status[0], fg=status[1])
        self.root.after(500, self._refresh_gui)

    def _set_status(self, text, colour):
        with self.lock:
            self.status = (text, colour)

    def submit_token(self):
        token = self.token_entry.get().strip()
        self.token_entry.delete(0, "end")
        if token:
            threading.Thread(target=self._apply_token, args=(token,), daemon=True).start()

    def _apply_token(self, token):
        self._set_status("Processing token...", "yellow")
        try:
            r = requests.post(f"{API_URL}/apply_token", json={
                "token_string": token, "meter_number": self.meter_number}, timeout=3)
        except requests.exceptions.RequestException:
            self._set_status("Server offline", "red")
            return
        # The new balance arrives with the next /report_usage reply, within a second.
        if r.status_code == 200:
            self._set_status("Token applied", "green")
        else:
            self._set_status("Invalid token", "red")

    def _close(self):
        self._alive = False
        self.root.destroy()


PROFILES = ("real_A", "real_B", "low_income")

if __name__ == "__main__":
    # python meter_gui.py M001                 -> demo meters use their own household pattern
    # python meter_gui.py 54321098765          -> any other meter number (e.g. one you registered in the app)
    # python meter_gui.py 54321098765 real_B   -> choose the household pattern for that meter
    meter = sys.argv[1] if len(sys.argv) > 1 else "M001"
    profile = sys.argv[2] if len(sys.argv) > 2 else METERS.get(meter, "low_income")
    if profile not in PROFILES:
        raise SystemExit(f"Unknown profile {profile}. Choose one of: {', '.join(PROFILES)}")
    root = tk.Tk()
    SmartMeterApp(root, meter, profile)
    root.mainloop()