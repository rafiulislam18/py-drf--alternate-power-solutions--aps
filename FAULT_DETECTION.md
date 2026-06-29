# Solar Fault Detection — Conditions Reference

Automated monitoring for the Urban Growth Home Assistant solar installation
(`https://urbangrowth.invertermon.com`).

The system runs two independent checks **every 5 minutes** (via Celery Beat) and
sends an **email + Telegram** alert when a fault is detected, and again when it
recovers. This document defines exactly what counts as a fault and what does not.

> Implemented in `apps/fault_detection/`. All thresholds are configurable through
> environment variables (see [demo.env](demo.env)); the values below are the
> defaults.

---

## Check 1 — SOC Imbalance

**Question it answers:** Are the 6 IES battery banks staying in balance with each
other?

### Data read from Home Assistant
- `sensor.ies_ies_1_soc` … `sensor.ies_ies_6_soc` — the State of Charge (%) of
  each of the 6 IES banks.

### Condition

| Situation | Result |
|---|---|
| `max(SOC) − min(SOC)` **> 10 %** | **FAULT** — imbalance detected |
| `max(SOC) − min(SOC)` **≤ 10 %** | No fault — banks balanced |
| Fewer than 2 banks reporting a usable value | Skipped (logged) — cannot compare |

- The threshold (10 %) is `HA_SOC_IMBALANCE_THRESHOLD`.
- The difference is the **spread** (highest bank minus lowest bank), not a sum.
- Banks reporting `unavailable` / `unknown` / non-numeric are skipped, not faulted.

### Example
Banks at 78, 77, 79, **55**, 80, 79 → spread = 80 − 55 = **25 %** → **FAULT**.
Banks at 73, 72, 75, 73, 75, 75 → spread = 75 − 72 = **3 %** → no fault.

---

## Check 2 — Charge / Discharge Behavior (Peak / Valley schedule)

**Question it answers:** Is the battery actually charging when it should be
(Valley) and discharging when it should be (Peak), according to **APS's own
schedule**?

### Source of truth: the APS schedule, NOT the live mode
The correct Peak/Valley schedule is **defined by APS** in configuration
(`HA_VALLEY_WINDOWS` / `HA_PEAK_WINDOWS`), as time windows in SAST. This is
deliberately **not** read from the inverter's live mode sensor
(`sensor.energitrack_ems_ems_mode`).

The reason: if the client manually forces a different mode on Home Assistant
(for example forcing Valley during what should be a Peak window), the battery
will deviate from the schedule APS set — and that is exactly the situation we
want to **alert** on. Trusting the live mode would hide it. So the check always
judges the battery against APS's intended schedule.

Default schedule, in **Cape Town time (SAST, GMT+2)** — set by
`HA_SCHEDULE_TIMEZONE` (`Africa/Johannesburg`). The check converts the current
time to this zone before comparing, so it is correct even though the project's
global `TIME_ZONE` is UTC.

| Period | Windows (SAST) | Battery should |
|---|---|---|
| **Valley** | 00:00–05:45, 11:01–16:45 | Charge |
| **Peak** | 05:46–11:00, 16:46–23:59 | Discharge |
| (outside any window) | — | No expectation — skipped |

### Data read from Home Assistant
| Entity | Used for |
|---|---|
| `sensor.battery_power` | Charge direction (see sign convention below) |
| `sensor.battery_soc` | Overall battery SoC (average of the banks) — for the limit guard |

> **Note on the IES per-bank status fields:** the
> `sensor.ies_ies_N_charge_and_discharge_status` fields are **deliberately NOT
> used** for charge direction. During testing (30/06/2026) they were observed
> stuck on "Discharging" while the battery was genuinely charging, so they are
> unreliable for this purpose. The live `ems_mode` sensor and the inverter's
> charge/discharge setting are also **not** used to decide faults, because they
> follow a manual override and would mask the fault we want to catch.

### Battery power sign convention (verified 30/06/2026)
- **Negative** `battery_power` (e.g. −148 kW) = **charging**
- **Positive** `battery_power` (e.g. +176 kW) = **discharging**
- Within ±5 kW of zero (`HA_BATTERY_POWER_DEADBAND_KW`) = **idle**

### Fault condition
A fault is raised when **all** of the following are true:

1. The current time falls in an APS **Valley** or **Peak** window.
2. The battery's actual direction (from `battery_power`) does **not** match what
   the window expects — i.e. discharging/idle during a Valley window, or
   charging/idle during a Peak window.
3. The wrong behavior **persists for 2 consecutive checks**
   (`HA_BEHAVIOUR_PERSIST_COUNT`) — roughly 10 minutes.

If any one of these is not met, it is **not** treated as a fault.

### Things that are explicitly NOT a fault (guards against false alarms)

| Situation | Why it is not a fault |
|---|---|
| **Battery full in Valley** (SoC ≥ 99 %, `HA_CHARGE_FULL_SOC`) | Nothing left to charge — expected |
| **Battery empty in Peak** (SoC ≤ 20 %, `HA_DISCHARGE_FLOOR_SOC`) | At the discharge floor — expected |
| **Wrong for only 1 check**, then corrects | Debounce — likely a brief transient |
| **Outside any scheduled window** | No charge/discharge expectation defined |
| **Power unavailable** | Cannot evaluate — skipped and logged |

### Examples
- **Peak window** (e.g. 09:00), but the client forced Valley so the battery is
  **charging** (−150 kW) for 2 checks → **FAULT** (not following APS schedule).
- **Valley window** (e.g. 12:00), battery **discharging** (+150 kW) for 2 checks
  → **FAULT**.
- **Valley window**, SoC **100 %**, power ~0 → no fault (battery full).
- **Valley window**, power **+150 kW** for 1 check then **−150 kW** → no fault
  (transient, debounced).

---

## Alerting behavior (both checks)

- Alerts are **edge-triggered**: one alert when a fault **starts**, then silence
  while it persists, then one **"recovered"** alert when it clears. No repeat
  spam every 5 minutes.
- Alerts go to **email** (to `EMAIL_RECIPIENT`) and **Telegram**, each
  independently enabled via `ALERT_EMAIL_ENABLED` / `ALERT_TELEGRAM_ENABLED`.
- Fault state is tracked in the `AlertState` database table (one row per check),
  visible/editable in the Django admin.

---

## Running the checks manually

```bash
# Run a single SOC imbalance check now and print the result
python manage.py check_soc

# Run a single charge/discharge behavior check now
python manage.py check_behaviour

# Discover Home Assistant SOC entity IDs
python manage.py list_ha_soc --regex "sensor\.ies_ies_\d+_soc$"

# Find your Telegram chat ID (after messaging the bot)
python manage.py telegram_chat_id
```

Both checks run automatically every 5 minutes once a Celery worker and Celery
beat are running (registered automatically by the app's data migrations).

---

## Configuration summary

| Variable | Default | Meaning |
|---|---|---|
| `HA_SOC_IMBALANCE_THRESHOLD` | `10` | SOC spread (%) that triggers an imbalance fault |
| `HA_VALLEY_WINDOWS` | `00:00-05:45,11:01-16:45` | APS Valley (charge) windows, SAST |
| `HA_PEAK_WINDOWS` | `05:46-11:00,16:46-23:59` | APS Peak (discharge) windows, SAST |
| `HA_BATTERY_POWER_DEADBAND_KW` | `5` | \|power\| below this counts as idle |
| `HA_CHARGE_FULL_SOC` | `99` | Valley: at/above this SoC, not charging is expected |
| `HA_DISCHARGE_FLOOR_SOC` | `20` | Peak: at/below this SoC, not discharging is expected |
| `HA_BEHAVIOUR_PERSIST_COUNT` | `2` | Consecutive wrong checks before a behavior alert |
| `ALERT_EMAIL_ENABLED` | `False` | Send email alerts |
| `ALERT_TELEGRAM_ENABLED` | `False` | Send Telegram alerts |

_Last updated: 30/06/2026._
