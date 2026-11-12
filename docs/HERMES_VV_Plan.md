# HERMES Payload - Verification and Validation Plan

Document: HERMES-PLD-VV-001
Status: preliminary, software version 0.1.0

## 1. Approach

Verification is layered:

1. Unit tests on a workstation, no hardware.
2. Bench tests on the payload with the real SDR, sensors and PWM.
3. Integration with the OBC over the real UART.
4. Mission-like run of at least 10 minutes with 20 simulated or real aircraft.
5. Environmental test as required by the mission (TVAC, vibration).

Every test records the configuration, software commit, date, operator, measured
values and pass/fail.

## 2. Unit tests

Run:

```bash
python -m unittest discover -s tests -v
```

Current suite: 68 tests.

| File | Coverage |
|---|---|
| `test_crc.py` | CRC vector, empty input, sensitivity, custom init |
| `test_frames.py` | roundtrip, max payload, bad CRC/version, split frames, recovery |
| `test_planes.py` | 28-byte layout, float32 fields, NaN, ICAO mask, 20-record packing |
| `test_payloads.py` | thermal, HK and ACK roundtrip, invalid values, event code |
| `test_table.py` | LRU, TTL, position staleness, field preservation, invalid input |
| `test_source.py` | JSON mapping, ground altitude, invalid entries |
| `test_policy.py` | no sensor, overtemp, hysteresis, rate limit, min duty, current limit |
| `test_policy_and_controller.py` | sensor -> policy -> PWM integration, disabled state |
| `test_sensors.py` | `w1_slave` parsing, CRC error, discovery, group mapping |
| `test_gpio.py` | debounce, SDR output, active-low logic |
| `test_mission.py` | start/stop sequence, duration, fault and retry |
| `test_timebase.py` | mission start, elapsed time, TIME_SYNC offset |

Acceptance: 100 % pass, no skipped test, no warning that masks a failure.

## 3. Bench tests

### 3.1 SDR and decoder

| Step | Expected |
|---|---|
| `rtl_test -t` | tuner found, no lost samples at 2.4 MS/s |
| start decoder | JSON available within 15 s |
| `curl /data/aircraft.json` | valid JSON with `aircraft` array |
| raw TCP 30002 | AVR lines arriving |
| remove SDR | decoder fails, payload enters FAULT, retries with backoff |

### 3.2 1-wire

| Step | Expected |
|---|---|
| `ls /sys/bus/w1/devices` | four `28-*` entries |
| `cat w1_slave` | `YES` and plausible temperature |
| disconnect one sensor | pack still controlled by the other sensor |
| disconnect all sensors of a pack | heater off after 3 polls, `NO_SENSOR` |
| heat sensor above 45 C | heater off, `OVERTEMP` |

### 3.3 PWM and heaters

| Step | Expected |
|---|---|
| Pi off | gate < 0.5 V, heater off |
| 0 % duty | 0 V at gate, no current |
| 50 % duty | 1 kHz square wave, ~50 % |
| 100 % duty | gate high, heater current per calculation |
| software stop | both duties 0 within one poll |
| `safe_off.py` | SDR off, both duties 0 |

### 3.4 GPIO inputs

| Step | Expected |
|---|---|
| pulse ON | state POWERING, SDR on after boot delay |
| pulse OFF while RUNNING | state STOPPING, SDR off |
| simultaneous ON and OFF | OFF wins, no start |
| contact bounce | one event after 15 ms debounce |

### 3.5 UART

| Step | Expected |
|---|---|
| loopback TX-RX | frames parsed, CRC OK |
| wrong baud | CRC/framing errors counted, no crash |
| inject bad CRC | frame dropped, next frame accepted |
| inject partial frame | parser waits, then recovers |
| fill TX queue | oldest frame dropped, `tx_dropped` increments |
| disconnect UART | log error, thermal loop continues |

## 4. Integration with the OBC

Setup: payload, OBC, common ground, 13.8 V dummy load or battery simulators.

Procedure:

1. Power on both units.
2. OBC sends `TIME_SYNC`; payload ACKs.
3. OBC pulses ON.
4. Payload sends ADSB, THERMAL and HK frames for 10 min.
5. Inject 20 aircraft into the decoder (replay or simulator).
6. Pulse OFF; payload stops within one poll.
7. Cut power; verify safe state.

Acceptance:

| Metric | Criterion |
|---|---|
| Mission duration | >= 600 s continuous |
| Aircraft tracked | >= 20 simultaneous |
| ADSB frame rate | 1.0 Hz +/- 0.1 Hz |
| Thermal frame rate | 1.0 Hz +/- 0.1 Hz |
| HK frame rate | 0.2 Hz +/- 0.05 Hz |
| Frame loss | <= 5 % at the OBC |
| CRC errors | 0 with good harness |
| Position latency | <= 2 s from decoder to OBC |
| Time sync error | <= 50 ms after ACK |
| Thermal cutoff | heater off at 45 C |
| Cold start | heating starts at 5 C |

## 5. Mission-like test

- Duration: 10 min.
- Aircraft: 20 simulated tracks with changing position, altitude and velocity.
- UART: real OBC or a recorder that validates CRC and logs every frame.
- Thermal: chamber or dummy sensors sweeping 0-50 C.
- Fault injection: kill the decoder once, disconnect one sensor once, corrupt one UART
  frame.

Pass criteria are those of section 4 plus recovery from each injected fault.

## 6. Environmental considerations

- The Pi Zero W and RTL-SDR are commercial-temperature devices (0-50 C typical).
  Operation at -10 C requires evidence or additional heating. Record the internal
  temperature of the Pi and SDR during TVAC.
- Verify heater authority at the lowest expected battery temperature: the pack must
  reach 0 C within the allowed time.
- Verify no thermal runaway with a stuck heater and the thermal fuse installed.
- Verify the SD card survives vibration and power cycling; consider overlayfs.

## 7. Traceability

| Requirement | Tests |
|---|---|
| PLD-F-01 | 3.1, 4 |
| PLD-F-02 | 4, 5 |
| PLD-F-03 | unit (`test_planes`, `test_source`), 4 |
| PLD-F-04 | unit (`test_table`, `test_timebase`), 4 |
| PLD-F-05 | 3.5, 4 |
| PLD-F-06 | 3.2, 3.3 |
| PLD-F-07 | 3.2, 3.3, 5, 6 |
| PLD-F-08 | 3.1, 3.3, 3.4, 3.5, 5 |
| PLD-F-09 | 4, 5 |
| PLD-F-10 | 3.1, 5 |

## 8. Test record template

```text
Test ID:
Date / operator:
Software version:
Hardware serial numbers:
Configuration (GPIO, sensor map, thermal parameters):
Procedure:
Measured values:
Pass / fail:
Observations:
```
