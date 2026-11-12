"""Wire representation of one aircraft."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

PLANE_STRUCT = struct.Struct("<IfffffI")
PLANE_SIZE = PLANE_STRUCT.size
ICAO_MASK = 0xFFFFFF


def _f(value: object) -> float:
    if value is None:
        return math.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


@dataclass(frozen=True)
class Plane:
    """One decoded aircraft in the exact format expected by the OBC."""

    icao_id: int
    latitude: float = math.nan
    longitude: float = math.nan
    altitude_ft: float = math.nan
    velocity_kt: float = math.nan
    track_deg: float = math.nan
    last_pos_met_ms: int = 0

    def to_bytes(self) -> bytes:
        icao = int(self.icao_id) & ICAO_MASK
        last_pos = max(0, int(self.last_pos_met_ms)) & 0xFFFFFFFF
        return PLANE_STRUCT.pack(
            icao,
            _f(self.latitude),
            _f(self.longitude),
            _f(self.altitude_ft),
            _f(self.velocity_kt),
            _f(self.track_deg),
            last_pos,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Plane":
        if len(data) != PLANE_SIZE:
            raise ValueError(f"Plane_t must be {PLANE_SIZE} bytes, got {len(data)}")
        icao, lat, lon, alt, vel, trk, last_pos = PLANE_STRUCT.unpack(data)
        return cls(icao, lat, lon, alt, vel, trk, last_pos)


def pack_planes(planes: list[Plane]) -> bytes:
    if len(planes) > 20:
        raise ValueError("at most 20 aircraft are supported in one frame")
    return b"".join(plane.to_bytes() for plane in planes)


def unpack_planes(data: bytes) -> list[Plane]:
    if len(data) % PLANE_SIZE:
        raise ValueError("payload length is not a multiple of Plane_t size")
    return [
        Plane.from_bytes(data[offset : offset + PLANE_SIZE])
        for offset in range(0, len(data), PLANE_SIZE)
    ]


def parse_icao(value: object) -> int:
    """Accept an int or a hexadecimal string and return a 24-bit ICAO."""
    if isinstance(value, int):
        return value & ICAO_MASK
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty ICAO")
        return int(text, 16) & ICAO_MASK
    raise ValueError(f"invalid ICAO value: {value!r}")
