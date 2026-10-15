import tkinter as tk
import threading
import requests
import random
from datetime import datetime

API_URL = "http://127.0.0.1:5000"
METER_NUMBER = "M001"


class SolarSimulator:
    """Simulates a solar panel + battery system."""
    def __init__(self):
        self.battery_percent = 85.0
        self.generation_kw = 2.5
        self.load_kw = 0.8
        self.solar_kwh_today = 12.5

    def tick(self):
        hour = datetime.now().hour

        # Generation depends on time of day
        if 6 <= hour < 12:
            self.generation_kw = 3.0 - random.uniform(0, 0.5)
        elif 12 <= hour < 17:
            self.generation_kw = 2.5 - random.uniform(0, 0.5)
        elif 17 <= hour < 19:
            self.generation_kw = 1.0 - random.uniform(0, 0.3)
        else:
            self.generation_kw = 0.0  # Night

        self.load_kw = 0.8 + random.uniform(-0.2, 0.2)

        net = self.generation_kw - self.load_kw
        if net > 0:
            self.battery_percent = min(100, self.battery_percent + net * 0.5)
            self.solar_kwh_today += net * (2 / 3600)
        else:
            self.battery_percent = max(0, self.battery_percent + net * 0.5)

    def sync(self):
        try:
            requests.post(f"{API_URL}/update_solar", json={
                "meter_number": METER_NUMBER,
                "battery_percent": round(self.battery_percent, 1),
                "generation_kw": round(self.generation_kw, 2),
                "load_kw": round(self.load_kw, 2),
                "solar_kwh_today": round(self.solar_kwh_today, 2),
            }, timeout=2)
            return True
        except requests.exceptions.RequestException:
            return False


class SolarApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Solar Simulator")
        self.root.geometry("450x400")
        self.root.configure(bg="#0a1a2a")

        self.solar = SolarSimulator()

        tk.Label(root, text="☀ SOLAR SYSTEM", font=("Arial", 16, "bold"),
                 fg="#ffcc00", bg="#0a1a2a").pack(pady=10)

        tk.Label(root, text=f"Meter: {METER_NUMBER}", font=("Arial", 10),
                 fg="gray", bg="#0a1a2a").pack()

        self.battery_label = tk.Label(root, text="85%", font=("Arial", 42, "bold"),
                                       fg="#00ff88", bg="#0a1a2a")
        self.battery_label.pack(pady=10)

        tk.Label(root, text="Battery Charge", font=("Arial", 10),
                 fg="white", bg="#0a1a2a").pack()

        self.gen_label = tk.Label(root, text="Generating: 0.00 kW",
                                   font=("Arial", 14), fg="#ffcc00", bg="#0a1a2a")
        self.gen_label.pack(pady=8)

        self.load_label = tk.Label(root, text="Home Load: 0.00 kW",
                                    font=("Arial", 14), fg="#ff8888", bg="#0a1a2a")
        self.load_label.pack(pady=8)

        self.today_label = tk.Label(root, text="Today: 0.0 kWh",
                                     font=("Arial", 12), fg="white", bg="#0a1a2a")
        self.today_label.pack(pady=8)

        self.status_label = tk.Label(root, text="Connecting...",
                                      font=("Arial", 10), fg="gray", bg="#0a1a2a")
        self.status_label.pack(side="bottom", pady=10)

        self.update_loop()

    def update_loop(self):
        self.solar.tick()

        self.battery_label.config(text=f"{self.solar.battery_percent:.0f}%")
        self.gen_label.config(text=f"Generating: {self.solar.generation_kw:.2f} kW")
        self.load_label.config(text=f"Home Load: {self.solar.load_kw:.2f} kW")
        self.today_label.config(text=f"Today: {self.solar.solar_kwh_today:.2f} kWh")

        if self.solar.battery_percent > 60:
            self.battery_label.config(fg="#00ff88")
        elif self.solar.battery_percent > 30:
            self.battery_label.config(fg="#ffaa00")
        else:
            self.battery_label.config(fg="#ff4444")

        threading.Thread(target=self._sync, daemon=True).start()
        self.root.after(2000, self.update_loop)

    def _sync(self):
        if self.solar.sync():
            self.status_label.config(text="✅ Synced with server", fg="#00ff88")
        else:
            self.status_label.config(text="⚠ Server offline", fg="orange")


if __name__ == "__main__":
    root = tk.Tk()
    app = SolarApp(root)
    root.mainloop()