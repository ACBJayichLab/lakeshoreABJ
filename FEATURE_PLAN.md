# Feature Plan — Desired Behavior

**Date:** 2026-08-26  
**Status:** Planned

---

## Features

### 1. Warning Icons (OR Condition)
**Location:** Traces list (left panel)
**Behavior:** Red warning triangle (⚠) appears next to a trace when **EITHER**:
- The associated heater is at rail (≥99% or ≤1% output) **OR**
- The associated temperature is not at setpoint (|T - SP| > threshold)
- Tooltip lists which condition(s) triggered
- Threshold: `stabilized_threshold_k` (default 0.5 K, configurable per cryostat)

---

### 2. Legend Shows Current Value
**Location:** Plot legends (both panels)
**Behavior:** Legend entries show trace name with current value: `Sample (96.234 K)`
- Precision: 3 decimal places for K, 3 for %
- Updates every refresh (1 Hz)
- Hidden traces show last value or "—"

---

### 3. Y-Axis Hard Clamp
**Location:** Both plot panels
**Behavior:** Value axis zoom cannot exceed hard limits:
- Kelvin panel: 0 K to 450 K
- Percent panel: 0% to 100%
- Zoom gesture stops at limit; panning within limits works
- X-axis unaffected

---

### 4. View Buttons — Live Windows Only
**Location:** View row (top of left panel)
**Behavior:**
- Buttons: 6h, 12h, **24h (default)**, 48h — no "All" button
- Clicking a button = sliding live window (rides with newest sample)
- Dragging/zooming = fixed window (accesses older data from disk)
- After drag/zoom: no button checked, status bar says "not following"
- Clicking any view button returns to live sliding mode

---

### 5. Hover Tooltip on Traces
**Location:** Both plot panels
**Behavior:** Hovering over a trace shows tooltip with:
- Trace name
- Interpolated value at cursor position
- Precision matches readouts (3dp K, 3dp %)
- Works on both kelvin and percent panels

---

### 6. Readouts Table Grouped by Loop
**Location:** Top-left readouts table
**Behavior:** Table reorganized from flat channel list to loop-centric rows:

| Loop | Sensor Channel | Kelvin | Setpoint | Heater Output | Heater Range |
|------|----------------|--------|----------|---------------|--------------|
| 1    | Sample         | 96.234 | 77.000   | 12.5%         | Medium       |
| 2    | Shield         | 4.210  | 4.200    | 0.0%          | Off          |

- One row per control loop (not per sensor channel)
- 336: 4 rows; loops 3-4 show analog outputs (range = N/A)
- 218: 1 row; setpoint/range = N/A, shows analog output %

---

### 7. Heater Range Tied to Selected Loop
**Location:** Command panel (Heater range group)
**Behavior:**
- No separate "Output" dropdown for heater range
- Range control automatically applies to the heater output driven by the currently selected loop
- Loop selector drives both setpoint **and** range controls
- Recorder config defines `loop_heater_map` per instrument (e.g., `{1: 1, 2: 2, 3: 3, 4: 4}`)
- Range combo pre-fills with current range for that loop's heater output

---

### 8. PID Parameter Boxes (Per Loop, Editable)
**Location:** Command panel (33x instruments only)
**Behavior:**
- Visible when instrument has loops
- Tied to selected loop (same selector as setpoint/range)
- Three fields: P, I, D (pre-filled with instrument's current values on loop change)
- "Get PID" button to refresh from instrument
- "Send PID" button with confirmation dialog
- Same interlocks as setpoint/range (accept_commands, allow_writes, allow_heater_range)
- Not polled continuously — fetched on demand

---

### 9. Sensor Input Channel Label for Loop
**Location:** Setpoint group (Command panel)
**Behavior:**
- Text label below loop selector: `Loop 1 → Sample`
- Populated from recorder status
- Updates when loop selector changes
- Hidden for 218 (no loops) or when sensor name unknown

---

## Recorder Changes Needed

| Feature | Recorder Change |
|---------|-----------------|
| 1, 6, 7 | Config: `loop_heater_map` per instrument |
| 1 | Config: `stabilized_threshold_k` (global, default 0.5 K) |
| 6, 9 | Status.json: `loop_sensors` per link (e.g., `{1: "Sample", 2: "Shield"}`) |
| 8 | Command spool: new "pid" command kind (loop, P, I, D) |
| 1, 6, 7, 8 | Aux polling: all loops/outputs covered (setpoint, heater%, range, aout) |

---

## Priority Order

### Phase 1 — Viewer Only (no recorder changes)
1. View buttons (Feature 4)
2. Y-axis clamp (Feature 3)
3. Hover tooltips (Feature 5)
4. Legend current value (Feature 2)

### Phase 2 — Recorder Config + Viewer
5. Range tied to loop (Feature 7) — needs `loop_heater_map`
6. Sensor label (Feature 9) — needs `loop_sensors` in status
7. Warning icons (Feature 1) — needs threshold + loop→heater map
8. Readouts by loop (Feature 6) — needs loop→heater map + sensor names

### Phase 3 — Recorder Command Support
9. PID boxes (Feature 8) — needs "pid" command

---

## Open Questions

1. Feature 8: Confirm "pid" command in spool is acceptable this cycle
2. Feature 1: Default 0.5 K threshold OK? Per-cryostat or per-loop?
3. Feature 6: Loops 3-4 on 336 — show as analog outputs (range=N/A) or hide?
4. Feature 7: Default `loop_heater_map` to identity `{1:1, 2:2, 3:3, 4:4}` if not configured?