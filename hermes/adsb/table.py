"""Thread-safe aircraft table with LRU eviction and position staleness."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace

from hermes.protocol.planes import Plane, parse_icao

_MISSING = object()


@dataclass
class _Record:
    plane: Plane
    last_seen_met_ms: int


class AircraftTable:
    def __init__(
        self,
        capacity: int = 20,
        ttl_s: float = 300.0,
        position_max_age_s: float = 10.0,
        clock_ms=None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self.ttl_ms = int(ttl_s * 1000)
        self.position_max_age_ms = int(position_max_age_s * 1000)
        self._clock_ms = clock_ms or (lambda: int(time.monotonic() * 1000))
        self._lock = threading.Lock()
        self._records: OrderedDict[int, _Record] = OrderedDict()
        self.updates = 0
        self.evictions = 0

    def update(
        self,
        icao: object,
        now_met_ms: int | None = None,
        *,
        latitude: object = _MISSING,
        longitude: object = _MISSING,
        altitude_ft: object = _MISSING,
        velocity_kt: object = _MISSING,
        track_deg: object = _MISSING,
        position_age_s: float | None = None,
    ) -> Plane | None:
        """Insert or refresh one aircraft.

        Fields left as ``_MISSING`` keep their previous value.  A position is only
        accepted when both latitude and longitude are finite and its age is within
        ``position_max_age_s``.
        """
        key = parse_icao(icao)
        if key == 0:
            return None
        now = self._clock_ms() if now_met_ms is None else int(now_met_ms)
        with self._lock:
            previous = self._records.get(key)
            plane = previous.plane if previous else Plane(key)
            lat = plane.latitude
            lon = plane.longitude
            alt = plane.altitude_ft
            vel = plane.velocity_kt
            trk = plane.track_deg
            last_pos = plane.last_pos_met_ms

            if latitude is not _MISSING:
                lat = _finite_or_nan(latitude)
            if longitude is not _MISSING:
                lon = _finite_or_nan(longitude)
            if altitude_ft is not _MISSING:
                alt = _finite_or_nan(altitude_ft)
            if velocity_kt is not _MISSING:
                vel = _finite_or_nan(velocity_kt)
            if track_deg is not _MISSING:
                trk = _finite_or_nan(track_deg)

            if latitude is not _MISSING or longitude is not _MISSING:
                if math.isfinite(lat) and math.isfinite(lon):
                    age_s = 0.0 if position_age_s is None else max(0.0, float(position_age_s))
                    if age_s * 1000.0 <= self.position_max_age_ms:
                        last_pos = max(0, now - int(age_s * 1000.0))
                    else:
                        lat = lon = math.nan
                        last_pos = 0
                else:
                    lat = lon = math.nan
                    last_pos = 0

            plane = Plane(key, lat, lon, alt, vel, trk, last_pos)
            self._records[key] = _Record(plane, now)
            self._records.move_to_end(key)
            if len(self._records) > self.capacity:
                self._records.popitem(last=False)
                self.evictions += 1
            self.updates += 1
            return self._snapshot_record(plane, now)

    def purge(self, now_met_ms: int | None = None) -> int:
        now = self._clock_ms() if now_met_ms is None else int(now_met_ms)
        removed = 0
        with self._lock:
            for key, record in list(self._records.items()):
                age = now - record.last_seen_met_ms
                if age > self.ttl_ms or age < 0:
                    del self._records[key]
                    removed += 1
        return removed

    def snapshot(self, now_met_ms: int | None = None) -> list[Plane]:
        now = self._clock_ms() if now_met_ms is None else int(now_met_ms)
        self.purge(now)
        with self._lock:
            return [self._snapshot_record(record.plane, now) for record in self._records.values()]

    def _snapshot_record(self, plane: Plane, now_ms: int) -> Plane:
        if plane.last_pos_met_ms == 0:
            return plane
        age_ms = now_ms - plane.last_pos_met_ms
        if age_ms > self.position_max_age_ms or age_ms < 0:
            return replace(plane, latitude=math.nan, longitude=math.nan, last_pos_met_ms=0)
        return plane

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


def _finite_or_nan(value: object) -> float:
    if value is None:
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan
