"""
solar_model.py - the solar panel and battery model used by the simulated meter (no imports from the app,
so it can be tested on its own).

A hybrid inverter serves the home in this order: solar first, then the battery (down to a reserve),
then the Kenya Power grid. Spare solar charges the battery. Only the grid part passes through the prepaid meter.
"""
import math

PANEL_PEAK_KW = 2.0       # the panels' output at midday in full sun
BATTERY_KWH = 5.0         # usable battery size
RESERVE_PCT = 20.0        # the inverter keeps this much charge back and uses the grid below it


def sun_factor(hour):
    """0 at night, rising to 1 at midday (hour is 0 to 24)."""
    if hour < 6.3 or hour > 18.5:
        return 0.0
    return math.sin(math.pi * (hour - 6.3) / 12.2) ** 1.5


def generation_kw(hour):
    return round(PANEL_PEAK_KW * sun_factor(hour), 3)


def solar_tick(generation, home_kw, battery_kwh, hours):
    """Advance the home by `hours`. Returns the new battery energy and the power taken from each source."""
    generation, home_kw = max(0.0, generation), max(0.0, home_kw)
    solar_kw = min(generation, home_kw)
    shortfall = home_kw - solar_kw
    reserve_kwh = BATTERY_KWH * RESERVE_PCT / 100.0
    usable_kw = max(0.0, battery_kwh - reserve_kwh) / hours if hours > 0 else 0.0
    battery_kw = min(shortfall, usable_kw)
    grid_kw = shortfall - battery_kw
    spare = max(0.0, generation - home_kw)
    charge = min(spare * hours, BATTERY_KWH - battery_kwh)
    new_battery = min(BATTERY_KWH, max(0.0, battery_kwh - battery_kw * hours + charge))
    return {"battery_kwh": new_battery, "solar_kw": solar_kw, "battery_kw": battery_kw, "grid_kw": grid_kw}