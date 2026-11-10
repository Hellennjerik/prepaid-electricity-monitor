"""
solar_gui.py - solar inverter + battery simulator with a window (Tkinter).

Same idea as your DeepSeek version, with a more realistic model:
  * sun follows a smooth curve through the day (none at night, peak at midday)
  * the battery charges only from surplus solar and drains only when the home needs more than the sun gives
  * "Energy made today" counts all solar energy produced (not just the surplus)
  * a "Sunny day demo" tick box fast-forwards a sunny day so you can demo it in the evening

It reports to the server with POST /update_solar, exactly as a real inverter gateway would.

Usage:  python solar_gui.py [M001]
Do NOT run this at the same time as solar_sim.py for the same meter.
"""
import math
import sys
import threading
import tkinter as tk
from datetime import datetime

import requests

API_URL = "http://127.0.0.1:5000"
METER_NUMBER = sys.argv[1] if len(sys.argv) > 1 else "M001"

PANEL_PEAK_KW = 2.0
BATTERY_KWH = 5.0
TICK_SECONDS = 2
DEMO_SPEEDUP = 120        # sunny-day mode: 1 real second = 2 simulated minutes


def sun_factor(hour):
    if hour < 6.3 or hour > 18.5:
        return 0.0
    return math.sin(math.pi * (hour - 6.3) / 12.2) ** 1.5


def home_load_kw(hour):
    return 0.25 + 0.7 * math.exp(-((hour - 7.0) ** 2) / 1.5) + 1.1 * math.exp(-((hour - 19.5) ** 2) / 3.0)


class SolarModel:
    """Pure simulation, no GUI and no network."""

    def __init__(self):
        self.battery_kwh = BATTERY_KWH * 0.65
        self.generation_kw = 0.0
        self.load_kw = 0.3
        self.kwh_today = 0.0
        self.sim_hour = 12.0
        self.demo = False
        self._day = datetime.now().date()

    @property
    def battery_percent(self):
        return 100.0 * self.battery_kwh / BATTERY_KWH

    def tick(self, now=None):
        now = now or datetime.now()
        if self.demo:
            self.sim_hour += TICK_SECONDS * DEMO_SPEEDUP / 3600.0
            if self.sim_hour > 17.5:
                self.sim_hour, self.kwh_today = 8.0, 0.0
            hour, step_h = self.sim_hour, TICK_SECONDS * DEMO_SPEEDUP / 3600.0
        else:
            if now.date() != self._day:
                self._day, self.kwh_today = now.date(), 0.0
            hour, step_h = now.hour + now.minute / 60.0, TICK_SECONDS / 3600.0

        self.generation_kw = round(PANEL_PEAK_KW * sun_factor(hour), 2)
        self.load_kw = round(home_load_kw(hour), 2)
        self.kwh_today += self.generation_kw * step_h
        self.battery_kwh = max(0.0, min(BATTERY_KWH, self.battery_kwh + (self.generation_kw - self.load_kw) * step_h))
        return hour

    def payload(self):
        return {
            "meter_number": METER_NUMBER,
            "battery_percent": round(self.battery_percent, 1),
            "generation_kw": round(self.generation_kw, 2),
            "load_kw": round(self.load_kw, 2),
            "solar_kwh_today": round(self.kwh_today, 2),
        }


class SolarApp:
    BG = "#0a1a2a"

    def __init__(self, root):
        self.root = root
        self.model = SolarModel()
        root.title("Solar Simulator")
        root.geometry("450x470")
        root.configure(bg=self.BG)

        tk.Label(root, text="☀ SOLAR SYSTEM", font=("Arial", 16, "bold"), fg="#ffcc00", bg=self.BG).pack(pady=10)
        tk.Label(root, text=f"Meter: {METER_NUMBER}", font=("Arial", 10), fg="gray", bg=self.BG).pack()
        self.battery_label = tk.Label(root, text="--%", font=("Arial", 42, "bold"), fg="#00ff88", bg=self.BG)
        self.battery_label.pack(pady=10)
        tk.Label(root, text="Battery Charge", font=("Arial", 10), fg="white", bg=self.BG).pack()
        self.gen_label = tk.Label(root, text="", font=("Arial", 14), fg="#ffcc00", bg=self.BG)
        self.gen_label.pack(pady=8)
        self.load_label = tk.Label(root, text="", font=("Arial", 14), fg="#ff8888", bg=self.BG)
        self.load_label.pack(pady=8)
        self.today_label = tk.Label(root, text="", font=("Arial", 12), fg="white", bg=self.BG)
        self.today_label.pack(pady=6)
        self.clock_label = tk.Label(root, text="", font=("Arial", 10), fg="gray", bg=self.BG)
        self.clock_label.pack()

        self.demo_var = tk.BooleanVar(value=False)
        tk.Checkbutton(root, text="Sunny day demo (fast-forward)", variable=self.demo_var, command=self._toggle_demo,
                       fg="white", bg=self.BG, selectcolor="#1b2740", activebackground=self.BG,
                       activeforeground="white", font=("Arial", 11)).pack(pady=10)

        self.status_label = tk.Label(root, text="Connecting...", font=("Arial", 10), fg="gray", bg=self.BG)
        self.status_label.pack(side="bottom", pady=10)
        self.update_loop()

    def _toggle_demo(self):
        self.model.demo = self.demo_var.get()
        if self.model.demo:
            self.model.sim_hour = 9.0

    def update_loop(self):
        hour = self.model.tick()
        m = self.model
        self.battery_label.config(text=f"{m.battery_percent:.0f}%",
                                  fg="#00ff88" if m.battery_percent > 60 else "#ffaa00" if m.battery_percent > 30 else "#ff4444")
        self.gen_label.config(text=f"Generating: {m.generation_kw:.2f} kW")
        self.load_label.config(text=f"Home Load: {m.load_kw:.2f} kW")
        self.today_label.config(text=f"Made today: {m.kwh_today:.2f} kWh")
        self.clock_label.config(text=f"{'Simulated' if m.demo else 'Local'} time: {int(hour):02d}:{int((hour % 1) * 60):02d}")
        threading.Thread(target=self._sync, args=(m.payload(),), daemon=True).start()
        self.root.after(TICK_SECONDS * 1000, self.update_loop)

    def _sync(self, payload):
        try:
            requests.post(f"{API_URL}/update_solar", json=payload, timeout=2)
            self.root.after(0, lambda: self.status_label.config(text="✅ Synced with server", fg="#00ff88"))
        except requests.exceptions.RequestException:
            self.root.after(0, lambda: self.status_label.config(text="⚠ Server offline", fg="orange"))


if __name__ == "__main__":
    root = tk.Tk()
    SolarApp(root)
    root.mainloop()