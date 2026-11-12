# HERMES Payload (ADS-B + Battery Thermal Control)

Payload software for the HERMES CubeSat. It runs on a Raspberry Pi Zero W, receives
ADS-B messages with an RTL-SDR, keeps a table of up to 20 aircraft, sends binary
telemetry to the OBC over UART and controls two battery heater channels.

The OBC is the mission master and the only path to the ground segment. The payload
does not implement TT&C, LoRa or fragmentation.

## Repository layout

```text
hermes/                 Python package
  adsb/                 decoder wrapper, aircraft table, raw feed
  protocol/             frame, CRC, Plane_t and payload codecs
  thermal/              sensors, policy, control loop
  config.py             all mission parameters
  gpio_control.py       MISSION_ON/OFF and SDR power
  pwm.py                heater PWM backends
  timebase.py           met_ms, mission_id, TIME_SYNC
  uart.py               OBC link and TX queue
  mission.py            ON/OFF state machine
  main.py               entry point
services/               systemd unit
scripts/safe_off.py     forces safe outputs
tests/                  unittest suite
docs/                   detailed documentation (English)
plan_pld.md             implementation plan (Portuguese)
```

## Quick start (no hardware)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m hermes.main --simulate --auto-on --duration 20
```

Simulation uses mock GPIO, PWM and sensors and generates three moving aircraft.
It still needs no SDR and no OBC.

## Raspberry Pi setup

1. Enable the 1-wire bus on GPIO4 and the serial port on `/dev/serial0` at 115200.
2. Install the decoder and pigpio:

```bash
sudo apt update
sudo apt install dump1090-fa pigpio python3-serial
sudo systemctl enable --now pigpiod
```

3. Verify the SDR and the decoder:

```bash
rtl_test -t
dump1090-fa --device-type rtlsdr --gain -10 --net
curl http://127.0.0.1:8080/data/aircraft.json
```

4. Verify the sensors:

```bash
ls /sys/bus/w1/devices/
cat /sys/bus/w1/devices/28-*/w1_slave
```

5. Run manually:

```bash
python -m hermes.main
```

## systemd

```bash
sudo mkdir -p /opt/hermes
sudo cp -r hermes scripts services requirements.txt /opt/hermes/
sudo cp services/hermes-payload.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-payload
journalctl -u hermes-payload -f
```

The unit uses `Type=notify`, a 10 s watchdog and `ExecStopPost` to force the SDR
off and both PWM channels to zero.

## UART protocol

- 115200 8N1, 3.3 V, little-endian.
- Frame: `SOF 0xAA55`, version, type, seq, met_ms, len, flags, count, payload, CRC16.
- CRC16-CCITT, poly `0x1021`, init `0xFFFF`.
- `Plane_t`: 28 bytes, 20 records = 560 bytes.
- Types: HK=1, ADSB=2, THERMAL=3, EVENT=4, TIME_SYNC=5, ACK=6.

See `docs/HERMES_Payload_ICD.md` for the complete interface.

## Defaults that must be reviewed before flight

- GPIOs: ON=27, OFF=22, SDR power=17, PWM=12/13, 1-wire=4.
- Thermal: start at 5 C, target 15 C, cutoff 45 C.
- Heater model: 82 ohm at 13.8 V (~168 mA, ~2.32 W per heater).
- Mission duration: 600 s.
- Decoder: `/usr/bin/dump1090-fa` on port 8080.

## Safety

- The 13.8 V bus has no fuse on the PCB. Add an inline fuse (0.5 A per heater
  branch) and, if possible, a thermal fuse.
- The RPi Zero W and the RTL-SDR are commercial-temperature parts. Operation at
  -10 C requires thermal justification or additional heating.
- Never connect the RPi UART to RS-232 levels. Use 3.3 V logic and a common ground.

## Documentation

- `docs/HERMES_Payload_Design.md` - architecture, modes, budgets, risks
- `docs/HERMES_Payload_ICD.md` - frame, payloads, electrical interface
- `docs/HERMES_Data_Schema.md` - decoder input, raw log, application log
- `docs/HERMES_Thermal_Control.md` - heater hardware, sensors, control law
- `docs/HERMES_VV_Plan.md` - unit, bench, integration and acceptance tests
