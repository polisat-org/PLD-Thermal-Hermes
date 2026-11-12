"""Payload encoders/decoders for HK, thermal and ACK frames."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

TEMP_INVALID = -32768

# Four temperatures (0.01 C), two duty values (0-100 %), status bits.
THERMAL_STRUCT = struct.Struct("<4h3B")
THERMAL_SIZE = THERMAL_STRUCT.size

# frames_tx, frames_rx, crc_errors, adsb_updates, raw_messages,
# cpu_temp_x100, sdr_on, decoder_ok, mission_state
HK_STRUCT = struct.Struct("<IIIIIhBBB")
HK_SIZE = HK_STRUCT.size

ACK_STRUCT = struct.Struct("<HBB")
ACK_SIZE = ACK_STRUCT.size


@dataclass(frozen=True)
class ThermalSample:
    temps_c: tuple[float | None, float | None, float | None, float | None]
    duties: tuple[float, float]
    status: int = 0


@dataclass(frozen=True)
class Housekeeping:
    frames_tx: int = 0
    frames_rx: int = 0
    crc_errors: int = 0
    adsb_updates: int = 0
    raw_messages: int = 0
    cpu_temp_x100: int = TEMP_INVALID
    sdr_on: int = 0
    decoder_ok: int = 0
    mission_state: int = 0


def _temp_x100(value: float | None) -> int:
    if value is None:
        return TEMP_INVALID
    try:
        number = float(value)
    except (TypeError, ValueError):
        return TEMP_INVALID
    if not math.isfinite(number):
        return TEMP_INVALID
    return max(-32768, min(32767, int(round(number * 100.0))))


def _duty_u8(value: float) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return max(0, min(100, int(round(number * 100.0))))


def encode_thermal(sample: ThermalSample) -> bytes:
    temps = tuple(sample.temps_c) + (None,) * (4 - len(sample.temps_c))
    duties = tuple(sample.duties) + (0.0,) * (2 - len(sample.duties))
    return THERMAL_STRUCT.pack(
        *[_temp_x100(temps[i]) for i in range(4)],
        _duty_u8(duties[0]),
        _duty_u8(duties[1]),
        int(sample.status) & 0xFF,
    )


def decode_thermal(data: bytes) -> ThermalSample:
    if len(data) != THERMAL_SIZE:
        raise ValueError(f"thermal payload must be {THERMAL_SIZE} bytes")
    t0, t1, t2, t3, d0, d1, status = THERMAL_STRUCT.unpack(data)
    temps = tuple(
        None if value == TEMP_INVALID else value / 100.0 for value in (t0, t1, t2, t3)
    )
    return ThermalSample(temps, (d0 / 100.0, d1 / 100.0), status)


def encode_hk(hk: Housekeeping) -> bytes:
    return HK_STRUCT.pack(
        int(hk.frames_tx) & 0xFFFFFFFF,
        int(hk.frames_rx) & 0xFFFFFFFF,
        int(hk.crc_errors) & 0xFFFFFFFF,
        int(hk.adsb_updates) & 0xFFFFFFFF,
        int(hk.raw_messages) & 0xFFFFFFFF,
        max(-32768, min(32767, int(hk.cpu_temp_x100))),
        int(hk.sdr_on) & 0xFF,
        int(hk.decoder_ok) & 0xFF,
        int(hk.mission_state) & 0xFF,
    )


def decode_hk(data: bytes) -> Housekeeping:
    if len(data) != HK_SIZE:
        raise ValueError(f"HK payload must be {HK_SIZE} bytes")
    return Housekeeping(*HK_STRUCT.unpack(data))


def encode_ack(ack_seq: int, ack_type: int, status: int = 0) -> bytes:
    return ACK_STRUCT.pack(int(ack_seq) & 0xFFFF, int(ack_type) & 0xFF, int(status) & 0xFF)


def decode_ack(data: bytes) -> tuple[int, int, int]:
    if len(data) != ACK_SIZE:
        raise ValueError(f"ACK payload must be {ACK_SIZE} bytes")
    return ACK_STRUCT.unpack(data)


class EventCode:
    MISSION_ON = 1
    MISSION_OFF = 2
    DECODER_FAULT = 3
    DURATION_ELAPSED = 4
    TIME_SYNC = 5


def encode_event(code: int, text: str = "") -> bytes:
    return bytes([int(code) & 0xFF]) + text.encode("ascii", "ignore")[:48]
