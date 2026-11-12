# HERMES Payload - Design Description

Document: HERMES-PLD-DD-001
Status: preliminary design, software version 0.1.0

## 1. Purpose

This document describes the payload software for the HERMES CubeSat. The payload
receives ADS-B messages on 1090 MHz, extracts aircraft state, sends a compact binary
telemetry stream to the OBC and keeps the battery pack inside its allowed temperature
range using resistive heaters.

The document is the top-level reference for the interface, thermal and verification
documents. Mission-specific values are centralized in `hermes/config.py`.

## 2. Scope and boundaries

### 2.1 In scope

- RTL-SDR power switching and supervision.
- ADS-B demodulation and decoding by an external C decoder (`readsb` or
  `dump1090-fa`).
- Aircraft table with up to 20 entries, per-ICAO update, TTL and LRU eviction.
- Binary UART telemetry to the OBC: aircraft, thermal, housekeeping, events and ACKs.
- Mission ON/OFF GPIOs, SDR power output and safe shutdown.
- Four DS18B20 sensors and two hardware PWM heater channels.
- Time base synchronized by the OBC; monotonic fallback.
- Raw ADS-B logging with mission timestamps.
- Unit tests and bench/integration procedures.

### 2.2 Out of scope

- OBC-to-ESP32 link, LoRa modulation, ground-station protocol.
- Downlink packet fragmentation and reassembly.
- Attitude control, EPS, deployment and other spacecraft subsystems.
- Any control of the 13.8 V bus beyond the two low-side heater switches.
- ADS-B transmission; the payload is receive-only.

## 3. System context

```text
                 +-----------------------------+
                 |          CubeSat            |
                 |                             |
 1090 MHz  ----> | RTL-SDR --> RPi Zero W ---> | UART 115200 ---> OBC/STM
                 |               |             |                     |
                 |               +--> 2x PWM --+--> heater MOSFETs    |
                 |               +--> GPIO17 ---+--> SDR 5 V switch   |
                 |               +--> GPIO27/22 <--- ON/OFF commands  |
                 |               +--> 1-wire <----- 4x DS18B20        |
                 +-----------------------------+
```

The OBC is the mission master. It commands ON/OFF through GPIO and is the time
master through `TIME_SYNC` frames. The payload is a sensor/actuator node.

## 4. Functional requirements

| ID | Requirement | Verification |
|---|---|---|
| PLD-F-01 | Receive and decode ADS-B on 1090 MHz | bench, integration |
| PLD-F-02 | Track at least 20 aircraft simultaneously for at least 10 min | integration |
| PLD-F-03 | Extract ICAO, latitude, longitude, altitude, velocity and track | unit, integration |
| PLD-F-04 | Timestamp every position with `met_ms` | unit, integration |
| PLD-F-05 | Send aircraft telemetry at 1 Hz over UART | integration |
| PLD-F-06 | Read 4 battery temperatures and drive 2 PWM heater channels | bench |
| PLD-F-07 | Keep battery temperature above 0 C without exceeding 45 C | thermal test |
| PLD-F-08 | Enter a safe state on command loss, decoder failure or shutdown | fault injection |
| PLD-F-09 | Boot automatically after power-on | integration |
| PLD-F-10 | Record raw ADS-B messages with mission timestamps | inspection |

## 5. Architecture

### 5.1 Partitioning rationale

ADS-B demodulation and CRC are CPU-intensive and timing-sensitive. They are done by
`readsb`/`dump1090-fa`, a mature C decoder, instead of Python. Python only reads the
decoded JSON and AVR stream, which keeps CPU load and risk low on the Pi Zero W.

Thermal control is isolated in a pure policy function (`decide_heating`) plus a thin
I/O loop. This makes the control law testable without hardware and easy to tune during
TVAC.

### 5.2 Software modules

| Module | Responsibility |
|---|---|
| `hermes/config.py` | frozen configuration, all mission constants |
| `hermes/protocol/crc.py` | CRC16-CCITT |
| `hermes/protocol/frames.py` | frame encode/decode and incremental parser |
| `hermes/protocol/planes.py` | `Plane_t` 28-byte codec |
| `hermes/protocol/payloads.py` | thermal, HK, ACK, event codecs |
| `hermes/adsb/table.py` | thread-safe aircraft table, TTL, LRU, staleness |
| `hermes/adsb/source.py` | JSON poller and bench simulator |
| `hermes/adsb/raw_feed.py` | AVR TCP logger |
| `hermes/adsb/decoder_process.py` | decoder supervision and readiness check |
| `hermes/thermal/sensors.py` | DS18B20 discovery, parsing, failure tracking |
| `hermes/thermal/policy.py` | pure heater decision |
| `hermes/thermal/controller.py` | sensor -> policy -> PWM loop |
| `hermes/gpio_control.py` | GPIO backend and debounced inputs |
| `hermes/pwm.py` | pigpio and mock PWM backends |
| `hermes/timebase.py` | mission time, mission ID, time sync |
| `hermes/uart.py` | serial link, TX queue, RX parser |
| `hermes/mission.py` | ON/OFF state machine |
| `hermes/systemd.py` | sd_notify and watchdog |
| `hermes/main.py` | composition root and main loop |

### 5.3 Threads

| Thread | Period | Work |
|---|---|---|
| main | 50 ms | mission state, RX handling, telemetry scheduling |
| ADS-B JSON | 1000 ms | HTTP GET, table update |
| ADS-B raw | event | TCP read, JSONL write |
| thermal | 2000 ms | read sensors, decide, set PWM |
| UART TX | event | dequeue and write frames |

Shared state is limited to the aircraft table (lock), the UART counters and the
thermal snapshot (single writer). The design avoids locks in the control path.

## 6. Operating modes

| State | Value | SDR | Decoder | Thermal | Telemetry |
|---|---:|---|---|---|---|
| OFF | 0 | off | stopped | policy active if `THERMAL_ALWAYS_ON` | thermal only if always-on |
| POWERING | 1 | on | waiting | active | no |
| STARTING | 2 | on | starting | active | no |
| RUNNING | 3 | on | running | active | 1 Hz |
| STOPPING | 4 | off | stopping | active if always-on | no |
| FAULT | 5 | off | stopped | active | no |

Transitions:

- OFF + ON press -> POWERING; time base starts.
- POWERING after `SDR_BOOT_DELAY_S` -> STARTING.
- STARTING if decoder JSON is reachable -> RUNNING, else FAULT.
- RUNNING if decoder dies -> FAULT; if mission time exceeds `MISSION_DURATION_S` ->
  STOPPING.
- FAULT after `DECODER_RESTART_BACKOFF_S` -> power-cycle SDR, POWERING.
- Any active state + OFF press -> STOPPING; after `SDR_MIN_OFF_TIME_S` -> OFF.

## 7. Timing and bandwidth

| Stream | Rate | Payload | Frame |
|---|---:|---:|---:|
| ADSB | 1 Hz | up to 560 B | up to 576 B |
| THERMAL | 1 Hz | 11 B | 27 B |
| HK | 0.2 Hz | 25 B | 41 B |
| EVENT | event | <= 49 B | <= 65 B |
| TIME_SYNC/ACK | event | 0/4 B | 16/20 B |

Worst case ~650 B/s. At 115200 8N1 (11520 B/s), UART load is below 6%.

Position age: a position older than 10 s is reported as invalid (`NaN`, timestamp 0)
even if the aircraft is still tracked. Table TTL is 300 s.

## 8. Resource budget

| Resource | Expected | Limit |
|---|---|---|
| Python CPU | < 5% of one core | 25% |
| Memory | < 40 MB resident | 256 MB |
| SD writes | up to ~50 MB per 10 min with raw logging | card endurance |
| Aircraft table | 20 x ~100 B | 20 entries |

The decoder dominates CPU and memory; the Python layer is intentionally small.

## 9. Failure handling

| Failure | Detection | Response |
|---|---|---|
| Decoder crash | process poll / JSON timeout | SDR off, FAULT, retry with backoff |
| SDR missing | decoder start timeout | FAULT, retry |
| UART missing | open exception | log, continue thermal-only |
| UART CRC error | parser | drop frame, increment counter |
| Sensor read error | CRC or I/O | group invalid after 3 polls, PWM 0 |
| Overtemperature | any sensor >= 45 C | PWM 0 immediately |
| Main process killed | systemd watchdog | restart, `ExecStopPost` safe state |
| Power loss | hardware | PWM and SDR outputs pulled low |

The thermal loop never depends on the decoder or UART, so a communication fault does
not endanger the batteries.

## 10. Assumptions and constraints

- The OBC provides 5 V for the Pi and the SDR, and 13.8 V for the heaters, with a
  common ground.
- The OBC drives MISSION_ON/OFF as active-high 3.3 V levels.
- The OBC sends `TIME_SYNC` frequently enough to keep `met_ms` bounded (recommended
  at least every 60 s).
- The decoder exposes dump1090-compatible JSON and AVR on ports 8080 and 30002.
- No GPS and no RTC are available.
- The Pi Zero W is a commercial-temperature device; the thermal design must justify
  or mitigate operation below 0 C.

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Pi Zero W below 0 C | boot/SD failure | preheat, heater placement, TVAC evidence |
| RTL-SDR below 0 C | lost reception | keep SDR near heater, power duty, TVAC |
| No fuse on 13.8 V | harness fire | inline fuse and thermal fuse |
| SD corruption | boot failure | overlayfs/read-only root, watchdog |
| Decoder instability | no ADS-B | supervisor, backoff, raw log for diagnosis |
| Antenna/EMI | low decode rate | RF grounding, keep UART away from RF, ferrites |

## 12. Traceability

Functional requirements map to verification cases in
`docs/HERMES_VV_Plan.md`. Interface details are in
`docs/HERMES_Payload_ICD.md`. Thermal sizing and control law are in
`docs/HERMES_Thermal_Control.md`.
