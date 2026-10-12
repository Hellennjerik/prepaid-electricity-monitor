import tkinter as tk
import threading
import time
import requests

# ---- Configuration ----
API_URL = "http://127.0.0.1:5000"
METER_NUMBER = "M001"   # Must match the one you registered in the database
RATE_PER_KWH = 25.0     # KSh per kWh (typical KPLC rate)
POWER_DRAW = 0.5        # kW being drawn (simulated load)


class KPLCPrepaidMeter:
    """A self-contained KPLC prepaid meter simulator that syncs with the API."""
    def __init__(self, meter_number, initial_balance=100.0):
        self.meter_number = meter_number
        self.balance = initial_balance
        self.kwh_used = 0.0
        self.used_tokens = set()

    def tick(self):
        """Simulate one second of consumption."""
        if self.balance > 0:
            kwh_consumed = (POWER_DRAW / 3600.0)
            cost = kwh_consumed * RATE_PER_KWH
            self.balance = round(max(0, self.balance - cost), 2)
            self.kwh_used = round(self.kwh_used + kwh_consumed, 4)

    def sync_to_api(self):
        """Push current state to the backend."""
        try:
            requests.post(f"{API_URL}/update_meter", json={
                "meter_number": self.meter_number,
                "balance": self.balance,
                "kwh_used": self.kwh_used
            }, timeout=2)
            return True
        except requests.exceptions.RequestException:
            return False

    def submit_token_to_api(self, token):
        """Ask the API to validate and apply a token."""
        clean = token.replace(" ", "").replace("-", "").strip()
        if len(clean) != 20 or not clean.isdigit():
            return "❌ Invalid Token: Must be 20 digits."

        try:
            r = requests.post(f"{API_URL}/apply_token", json={
                "token_string": clean,
                "meter_number": self.meter_number
            }, timeout=3)
            data = r.json()
            if r.status_code == 200:
                # Reload balance from server
                self.refresh_balance_from_api()
                return f"✅ Token Accepted! Added KSh {data['amount_added']}. New Balance: KSh {self.balance}"
            else:
                return f"❌ {data.get('error', 'Unknown error')}"
        except requests.exceptions.RequestException:
            return "❌ Cannot reach server. Is the API running?"

    def refresh_balance_from_api(self):
        """Pull the latest balance from the backend."""
        try:
            r = requests.get(f"{API_URL}/meter_status/{self.meter_number}", timeout=2)
            if r.status_code == 200:
                data = r.json()
                self.balance = data.get("balance", self.balance)
                self.kwh_used = data.get("kwh_used", self.kwh_used)
        except requests.exceptions.RequestException:
            pass


class SmartMeterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("KPLC Smart Meter Simulator")
        self.root.geometry("450x450")
        self.root.configure(bg="black")

        # Try to load existing balance from API; fall back to 100
        self.meter = KPLCPrepaidMeter(meter_number=METER_NUMBER, initial_balance=0.0)
        self.meter.refresh_balance_from_api()

        tk.Label(root, text="KPLC PREPAID METER", font=("Arial", 14, "bold"),
                 fg="white", bg="black").pack(pady=10)

        tk.Label(root, text=f"Meter: {METER_NUMBER}", font=("Arial", 10),
                 fg="gray", bg="black").pack()

        self.energy_label = tk.Label(root, text="0.0000 kWh", font=("Arial", 36, "bold"),
                                      fg="cyan", bg="black")
        self.energy_label.pack(pady=10)

        self.credit_label = tk.Label(root, text="Credit: KSh 0.00", font=("Arial", 18),
                                      fg="yellow", bg="black")
        self.credit_label.pack(pady=10)

        tk.Label(root, text="Enter 20-digit Token:", font=("Arial", 10),
                 fg="white", bg="black").pack(pady=(15, 5))

        self.token_entry = tk.Entry(root, font=("Arial", 12), justify="center",
                                     bg="#222", fg="white", insertbackground="white")
        self.token_entry.pack(pady=5, ipady=5, padx=20, fill="x")

        tk.Button(root, text="Submit Token", font=("Arial", 11, "bold"),
                  bg="#00aa00", fg="white", command=self.submit_token).pack(pady=10)

        self.status_label = tk.Label(root, text="Connecting...",
                                      font=("Arial", 10), fg="gray", bg="black",
                                      wraplength=400)
        self.status_label.pack(side="bottom", pady=10)

        self.update_loop()

    def update_loop(self):
        """Every second: consume power, update GUI, sync to API."""
        self.meter.tick()

        self.energy_label.config(text=f"{self.meter.kwh_used:.4f} kWh")
        self.credit_label.config(text=f"Credit: KSh {self.meter.balance:.2f}")

        if self.meter.balance <= 0:
            self.credit_label.config(fg="red")
        elif self.meter.balance <= 20:
            self.credit_label.config(fg="orange")
        else:
            self.credit_label.config(fg="yellow")

        # Push to API in the background (non-blocking)
        threading.Thread(target=self._sync, daemon=True).start()

        self.root.after(1000, self.update_loop)

    def _sync(self):
        ok = self.meter.sync_to_api()
        if ok:
            self.status_label.config(text="✅ Synced with server", fg="green")
        else:
            self.status_label.config(text="⚠️ Server offline — running locally", fg="orange")

    def submit_token(self):
        token = self.token_entry.get()
        result = self.meter.submit_token_to_api(token)
        self.status_label.config(
            text=result,
            fg="green" if result.startswith("✅") else "red"
        )
        self.token_entry.delete(0, "end")


if __name__ == "__main__":
    root = tk.Tk()
    app = SmartMeterApp(root)
    root.mainloop()