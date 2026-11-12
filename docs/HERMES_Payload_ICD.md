# HERMES Payload - Interface Control Document

Document: HERMES-PLD-ICD-001
Status: preliminary, software version 0.1.0

## 1. Electrical interface

| Parameter | Value |
|---|---|
| Physical layer | 3.3 V CMOS UART, point-to-point |
| Connector | RPi 40-pin header, pins 8 (TXD) and 10 (RXD), pin 6 GND |
| Baud rate | 115200 |
| Framing | 8 data bits, no parity, 1 stop bit |
| Flow control | none |
| Byte order | little-endian |
| Isolation | none; common ground required |
| Voltage tolerance | 0 to 3.3 V only; never RS-232 levels |

The OBC transmits on the RPi RX line. The payload transmits on the RPi TX line.
If the OBC is off or its TX line is high-Z, the RPi RX may float; a 10 kOhm
pull-down at the OBC is recommended.

## 2. Frame format

```text
offset  size  field
0       2     SOF         0xAA 0x55
2       1     version     0x01
3       1     type        see table
4       2     seq         frame counter, wraps at 65535
6       4     met_ms      mission elapsed time, milliseconds
10      2     len         payload length in bytes
12      1     flags       bit0: time base synchronized
13      1     count       number of records in payload (ADSB)
14      len   payload     type-specific
14+len  2     crc16       CRC16-CCITT over bytes 2..14+len-1
```

Header size is 14 bytes. Maximum payload is 560 bytes (20 `Plane_t`), maximum
frame is 576 bytes. There is no byte stuffing and no escaping; framing relies on
SOF plus CRC plus length validation.

### 2.1 CRC

CRC16-CCITT, polynomial `0x1021`, initial value `0xFFFF`, no input/output
reflection, no final XOR, computed over `version` through the last payload byte
(the SOF is excluded).

Reference: `crc16_ccitt(b"123456789") == 0x29B1`.

## 3. Frame types

| Value | Name | Direction | Payload |
|---:|---|---|---|
| 1 | HK | payload -> OBC | housekeeping |
| 2 | ADSB | payload -> OBC | `count` x `Plane_t` |
| 3 | THERMAL | payload -> OBC | thermal sample |
| 4 | EVENT | payload -> OBC | code + text |
| 5 | TIME_SYNC | OBC -> payload | empty |
| 6 | ACK | payload -> OBC | ACK |

Unknown types must be ignored by the receiver.

## 4. ADSB payload

`count` is the number of `Plane_t` records; `len = 28 * count`.

```c
typedef struct {
    uint32_t icaoID;            /* 24-bit ICAO, 0 = empty */
    float    latitude;          /* degrees, NaN if invalid */
    float    longitude;         /* degrees, NaN if invalid */
    float    altitude_ft;       /* feet, NaN if invalid */
    float    velocity_kt;       /* knots, NaN if invalid */
    float    track_deg;         /* degrees true, NaN if invalid */
    uint32_t last_pos_met_ms;   /* time of last position, 0 if invalid */
} Plane_t;
```

- Size is exactly 28 bytes; Python format `<IfffffI`.
- The OBC must treat `icaoID == 0` as an empty slot.
- `NaN` is the IEEE-754 quiet NaN produced by the payload.
- `last_pos_met_ms` is in the same time base as the frame `met_ms`.
- A position older than `POSITION_MAX_AGE_S` (10 s) is sent as `NaN` with
  `last_pos_met_ms = 0`.
- Values are not filtered or smoothed; they are the decoder output rounded to
  `float32`.

## 5. THERMAL payload

Size 11 bytes, format `<4h3B`:

| Offset | Type | Field | Unit |
|---:|---|---|---|
| 0 | int16 | temperature 1 | 0.01 C |
| 2 | int16 | temperature 2 | 0.01 C |
| 4 | int16 | temperature 3 | 0.01 C |
| 6 | int16 | temperature 4 | 0.01 C |
| 8 | uint8 | heater 1 duty | percent, 0-100 |
| 9 | uint8 | heater 2 duty | percent, 0-100 |
| 10 | uint8 | status | bit flags |

`-32768` (`0x8000`) means invalid sensor. Temperatures 1-2 belong to battery pack 1
and 3-4 to pack 2 (configurable through the sensor map).

Status bits:

| Bit | Meaning |
|---:|---|
| 0 | no valid sensor in at least one pack |
| 1 | overtemperature, heater forced off |
| 2 | current limit applied |

## 6. HK payload

Size 25 bytes, format `<IIIIIhBBB`:

| Offset | Type | Field |
|---:|---|---|
| 0 | uint32 | frames transmitted |
| 4 | uint32 | frames received |
| 8 | uint32 | CRC errors |
| 12 | uint32 | aircraft table updates |
| 16 | uint32 | raw AVR messages logged |
| 20 | int16 | CPU temperature, 0.01 C, `-32768` invalid |
| 22 | uint8 | SDR power on |
| 23 | uint8 | decoder alive |
| 24 | uint8 | mission state |

Mission state values: 0 OFF, 1 POWERING, 2 STARTING, 3 RUNNING, 4 STOPPING, 5 FAULT.

## 7. TIME_SYNC and ACK

The OBC sends `TIME_SYNC` with `len = 0` and its mission time in the header
`met_ms`. The payload applies the offset and sets `flags` bit 0 in subsequent
frames.

The payload answers with `ACK`, 4 bytes, format `<HBB`:

| Offset | Type | Field |
|---:|---|---|
| 0 | uint16 | sequence of the acknowledged frame |
| 2 | uint8 | type of the acknowledged frame |
| 3 | uint8 | status, 0 = OK |

The OBC should send `TIME_SYNC` at least every 60 s. Without sync the payload uses
monotonic time since mission ON; the values are consistent but not tied to the OBC
clock.

## 8. EVENT payload

Byte 0 is the event code, followed by up to 48 ASCII bytes of text. Total payload
is 1 to 49 bytes.

| Code | Event |
|---:|---|
| 1 | mission ON / powering |
| 2 | mission OFF / stopping |
| 3 | decoder fault |
| 4 | duration elapsed |
| 5 | time sync applied |

Unknown codes must be ignored.

## 9. Reliability and parser behavior

- The receiver must validate SOF, version, `len <= 560`, total length and CRC.
- On any error, discard one byte and rescan for SOF. This allows recovery after a
  truncated or corrupted frame without waiting for a timeout.
- `seq` is incremented per transmitted frame and wraps at 65535. The OBC may use it
  to detect loss; the payload does not retransmit.
- The TX queue holds 4 frames. If the UART blocks, the oldest queued frame is
  dropped and the drop is reflected in the HK counters.
- The payload sends ADSB every 1 s, THERMAL every 1 s and HK every 5 s while the
  mission is active. Empty ADSB frames (`count = 0`) are valid and indicate that the
  payload is alive with no aircraft in view.
- The payload does not send ADSB or HK telemetry while the mission is OFF.  ADSB can
  be enabled with `SEND_ADSB_WHEN_OFF`; THERMAL frames continue when
  `THERMAL_ALWAYS_ON` is enabled.

## 10. GPIO interface

| Signal | BCM | Direction | Default | Function |
|---|---:|---|---|---|
| MISSION_ON | 27 | in | pull-down | pulse/high requests mission start |
| MISSION_OFF | 22 | in | pull-down | pulse/high requests mission stop |
| SDR_POWER | 17 | out | low | high enables the SDR 5 V switch |
| PWM_PACK1 | 12 | out | 0 % | heater 1 |
| PWM_PACK2 | 13 | out | 0 % | heater 2 |
| W1 | 4 | bidir | - | DS18B20 bus |

Inputs are active-high by default and are debounced for 15 ms. OFF has priority over
ON. The SDR output must drive a logic-level N-channel MOSFET or a load switch; it must
not source the SDR current directly.

## 11. Example frame

ADSB with two aircraft, `met_ms = 123456`, `seq = 42`:

```text
AA 55 01 02 2A 00 40 E2 01 00 38 00 00 02
<56 bytes of Plane_t>
<CRC16 little-endian>
```

`len = 0x0038 = 56`, `count = 2`, `met_ms = 0x0001E240 = 123456`.
