# HERMES Payload - Battery Thermal Control

Document: HERMES-PLD-TC-001
Status: preliminary, software version 0.1.0

## 1. Objective

Keep the battery cells above 0 C during cold operations and below 45 C at all times.
Heating is allowed only while the battery is cold; there is no active cooling, so the
control law must stop early enough that residual heat does not exceed the upper limit.

## 2. Hardware

### 2.1 Heater assumptions

| Parameter | Value |
|---|---|
| Heater resistance | 82 ohm (assumed) |
| Supply | 13.8 V |
| Current per heater | 13.8 / 82 = 168 mA |
| Power per heater | 13.8^2 / 82 = 2.32 W |
| Heaters per pack | 2 (parallel) |
| Current per pack | 337 mA |
| Power per pack | 4.65 W |
| Fuse per branch | 0.5 A |

The 82 ohm value must be confirmed by measurement on the flight harness. Recalculate
the current and the fuse if the heater or supply changes.

### 2.2 Switch

- N-channel MOSFET A09T (AO3400A equivalent): 30 V, 5.8 A, logic-level gate.
- Low-side configuration: heater from +13.8 V to drain, source to ground.
- Gate driven by GPIO12/13 through a 100 ohm series resistor.
- 10 kOhm gate-to-source pull-down keeps the heater off while the Pi boots or resets.
- Rds(on) at Vgs = 3.3 V is a few tens of milliohms; dissipation at 337 mA is below
  10 mW, so no heatsink is needed.
- A flyback diode is not required for a resistive heater, but a TVS or RC snubber is
  recommended if the heater cable is long and inductive.

### 2.3 Fusing

The PCB has no fuse on the 13.8 V bus. Add:

- one inline fuse per heater branch (0.5 A recommended);
- a thermal fuse in series with the heater bus, rated below the battery maximum
  temperature;
- a main bus fuse sized for the total heater current.

The MOSFET is not a safety device: it can fail short. The thermal fuse is the last
line of defense against a stuck heater.

### 2.4 Sensors

- Four DS18B20 on the 1-wire bus, GPIO4.
- 4.7 kOhm pull-up from data to 3.3 V.
- Use parasite power only if the bus is short; otherwise use three-wire mode.
- Mount sensors on the cell surface or on the battery plate, not on the heater.
- Sensors 1-2 belong to pack 1, sensors 3-4 to pack 2. The map is configurable through
  `SENSOR_MAP` in `hermes/config.py`.
- If `SENSOR_MAP` is empty, the payload discovers `28-*` devices in sorted order and
  assigns the first two to pack 1 and the next two to pack 2.

### 2.5 PWM

- GPIO12 and GPIO13, 1 kHz.
- pigpio hardware PWM is used when available; otherwise the software-timed backend
  keeps the same interface.
- Duty 0 % is the safe state. The GPIOs are configured as outputs low at boot and the
  gate pull-down holds the MOSFET off before the software runs.
- `PWM_ACTIVE_HIGH` allows inversion if a different gate driver is used.

## 3. Control law

### 3.1 Inputs

For each pack, the controller takes the minimum and maximum of the valid sensors in
that pack:

```text
min_c = min(valid temperatures of pack)
max_c = max(valid temperatures of pack)
```

If no sensor in the pack is valid, the pack is failed and its heater is forced off.

### 3.2 Decision

Default parameters:

| Parameter | Value |
|---|---:|
| `TEMP_START_C` | 5 C |
| `TEMP_TARGET_C` | 15 C |
| `TEMP_STOP_C` | 15 C |
| `TEMP_CUTOFF_C` | 45 C |
| `HEAT_MIN_DUTY` | 5 % |
| `HEAT_MAX_DUTY` | 100 % |
| `HEAT_RATE_LIMIT_PER_S` | 0.5 |
| `SENSOR_POLL_S` | 2 s |
| `SENSOR_FAIL_LIMIT` | 3 polls |
| `HEATER_SUPPLY_V` | 13.8 V |
| `HEATER_RESISTANCE_OHM` | 82 ohm |
| `HEATER_MAX_CURRENT_A` | 0.5 A |

The pure function `decide_heating()` in `hermes/thermal/policy.py` implements:

1. If `min_c` or `max_c` is invalid: duty 0, status `NO_SENSOR`.
2. If `max_c >= 45 C`: duty 0 immediately, status `OVERTEMP`. This check has priority
   over any heating request.
3. Hysteresis:
   - if already heating, keep heating while `min_c < TEMP_STOP_C`;
   - if not heating, start when `min_c <= TEMP_START_C`.
4. Proportional duty while heating:

```text
duty = (TEMP_TARGET_C - min_c) / (TEMP_TARGET_C - TEMP_START_C)
duty = clamp(duty, 0, 1)
duty = max(duty, HEAT_MIN_DUTY)
duty = min(duty, HEAT_MAX_DUTY)
```

At `min_c <= 5 C` the duty is 100 %; at `min_c = 15 C` it is 0 % and heating stops.

5. Slew-rate limit:

```text
max_delta = HEAT_RATE_LIMIT_PER_S * dt
duty = clamp(duty, previous_duty - max_delta, previous_duty + max_delta)
```

The first tick after boot has `dt = 0` and may apply any duty immediately.

6. Current limit:

```text
current_per_duty = HEATER_SUPPLY_V / HEATER_RESISTANCE_OHM
duty_cap = HEATER_MAX_CURRENT_A / current_per_duty
if duty > duty_cap: duty = duty_cap; status |= CURRENT_LIMIT
```

With the default values, `duty_cap = 0.5 / 0.168 = 2.97`, so the limit does not bind.
It protects the harness if the heater resistance is lower than assumed or the supply
is higher.

### 3.3 State machine

```text
             min <= start
   IDLE ---------------------> HEATING
     ^                            |
     |        min >= stop         |
     +----------------------------+
```

`max >= cutoff` forces IDLE from any state and reports `OVERTEMP`. A failed sensor
forces IDLE and reports `NO_SENSOR`. Heating resumes automatically when a valid
sensor returns below the start threshold.

## 4. Timing

- Sensor poll: 2 s.
- PWM update: every sensor poll.
- Thermal frame: 1 Hz, independent of the poll.
- Sensor failure: after 3 consecutive polls without any valid sensor in a pack, the
  pack is treated as failed until a valid read returns.

## 5. Status and telemetry

The thermal frame carries four temperatures (0.01 C), two duties (percent) and a status
byte. `-32768` marks an invalid sensor. The status bits are:

| Bit | Meaning |
|---:|---|
| 0 | no valid sensor |
| 1 | overtemperature |
| 2 | current limit |

Status bits are OR-ed across both packs.

## 6. Failure modes and responses

| Failure | Response |
|---|---|
| One sensor CRC error | other sensor in the pack still valid; keep control |
| All sensors of a pack fail | heater off for that pack, `NO_SENSOR` |
| Sensor reads 45 C or more | heater off immediately, `OVERTEMP` |
| MOSFET short | thermal fuse opens; no software recovery |
| Pi reset | gate pull-down keeps heater off; safe state after boot |
| PWM backend error | exception logged, duty forced to 0 |
| 1-wire bus short | all reads fail, heaters off |

## 7. Commissioning

1. Measure each heater resistance at room temperature; record it.
2. Verify the 13.8 V bus current with both heaters at 100 % (expected ~674 mA for two
   packs with two heaters each).
3. Verify the gate voltage is below 0.5 V with the Pi off (pull-down working).
4. Verify 0 % and 100 % duty on an oscilloscope at GPIO12/13.
5. Place sensors in a thermal chamber and compare against a reference thermometer at
   0, 5, 15, 30 and 45 C.
6. Run the control loop with a dummy load and confirm the 45 C cutoff and the 5/15 C
   hysteresis.
7. Record the final `SENSOR_MAP` ROM IDs in the flight configuration.

## 8. Safety notes

- Never operate the heaters without a fuse and a thermal fuse.
- Do not connect the 13.8 V bus to the Pi or to the SDR.
- Keep the 1-wire and PWM wiring away from the RF path; use twisted pairs and a common
  ground at the battery board.
- The control law assumes the heater is resistive. If a different actuator is used,
  add the appropriate flyback protection and recalculate the current limit.
