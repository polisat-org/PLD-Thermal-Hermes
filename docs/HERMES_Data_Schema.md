# HERMES Payload - Data Schema

Document: HERMES-PLD-DS-001
Status: preliminary, software version 0.1.0

## 1. Data sources

The payload consumes two data products from the external ADS-B decoder:

1. `aircraft.json`, polled at 1 Hz from `http://127.0.0.1:8080/data/aircraft.json`.
2. AVR raw messages, read from TCP port 30002 and logged to disk.

The decoder performs demodulation, bit correction and CRC validation. The payload
does not process IQ samples.

## 2. Decoder JSON mapping

The decoder returns a document with an `aircraft` array. The payload maps these
fields:

| JSON field | Internal field | Notes |
|---|---|---|
| `hex` or `icao` | `icao_id` | hexadecimal string or integer, 24 bits |
| `lat` or `latitude` | `latitude` | degrees |
| `lon` or `longitude` | `longitude` | degrees |
| `alt_baro` | `altitude_ft` | number; `"ground"` becomes 0 ft |
| `gs` | `velocity_kt` | ground speed, knots |
| `track` | `track_deg` | degrees true |
| `seen_pos` | `last_pos_met_ms` | seconds since position; `met_ms - seen_pos*1000` |

Rules:

- Missing fields keep the previous value; they are never zeroed by a partial message.
- Non-numeric or non-finite values become `NaN`.
- A position older than 10 s is reported as invalid.
- `icaoID = 0` is reserved for empty slots.
- The table holds at most 20 aircraft. A new ICAO when full evicts the least recently
  seen entry.
- Entries older than 300 s are purged.

Example input:

```json
{
  "now": 1712345678.4,
  "messages": 123456,
  "aircraft": [
    {
      "hex": "abc123",
      "flight": "HERMES1 ",
      "lat": -23.5505,
      "lon": -46.6333,
      "alt_baro": 35000,
      "gs": 451.2,
      "track": 87.5,
      "seen_pos": 1.4,
      "seen": 0.2,
      "rssi": -12.3
    }
  ]
}
```

Internal record (wire format):

```text
icao_id=0xABC123 latitude=-23.5505 longitude=-46.6333
altitude_ft=35000.0 velocity_kt=451.2 track_deg=87.5
last_pos_met_ms=<met_ms - 1400>
```

The `flight`, `rssi` and `seen` fields are intentionally not downlinked: they are not
required by the mission and would consume bandwidth.

## 3. Raw AVR log

File: `logs/raw/avr-YYYYMMDD-HHMMSS.jsonl`, one JSON object per line:

```json
{"met_ms":123456,"icao":"ABC123","raw":"*8DABC12358A1..."}
```

| Field | Type | Description |
|---|---|---|
| `met_ms` | integer | mission time when the line was received |
| `icao` | string | six uppercase hex digits extracted from bytes 1-3 |
| `raw` | string | original AVR line, including `*` and `;` |

The log is append-only and flushed per line. It supports replay, decode-rate
analysis and post-flight anomaly investigation. It is not downlinked.

## 4. Application log

File: `logs/hermes.log`, plus stdout captured by journald.

Format:

```text
YYYY-MM-DDTHH:MM:SS.mmm LEVEL logger: message
```

The log includes mission state transitions, decoder readiness and faults, UART open
failures, thermal exceptions and shutdown. It must not contain secrets; none are
used by the payload.

## 5. Decoder log

File: `logs/decoder.log`. Standard output and standard error of `dump1090-fa` or
`readsb`. Useful for diagnosing SDR gain, dropped samples and USB errors.

## 6. File naming and retention

| File | Rotation | Retention |
|---|---|---|
| `logs/hermes.log` | none (systemd journald also keeps a copy) | mission-dependent |
| `logs/decoder.log` | none | mission-dependent |
| `logs/raw/avr-*.jsonl` | one file per process start | keep all for V&V |

For flight, mount `/var/log` or the raw directory on a separate partition and monitor
free space. A 10 min mission at high message rate is typically below 20 MB.

## 7. Replay procedure

1. Copy the `avr-*.jsonl` file to a ground station.
2. Replay each `raw` line into a local `readsb --net` instance or a decoder test
   harness.
3. Compare the decoded ICAO, position and timestamps against the telemetry captured
   by the OBC.
4. Use `met_ms` to align payload telemetry and raw messages.

## 8. Integrity

- UART frames are protected by CRC16-CCITT.
- ADS-B messages were already CRC-checked by the decoder.
- Raw logs are written as JSONL; a truncated last line is possible after power loss
  and must be ignored by parsers.
- No compression or encryption is applied.
