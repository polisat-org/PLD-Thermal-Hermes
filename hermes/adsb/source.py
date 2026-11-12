"""Poll a readsb/dump1090-fa ``aircraft.json`` document and feed the table."""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request

from .table import AircraftTable


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _altitude(value: object) -> float | None:
    if isinstance(value, str):
        if value.strip().lower() == "ground":
            return 0.0
        return None
    return _number(value)


def apply_aircraft_json(document: dict, table: AircraftTable, now_met_ms: int) -> int:
    """Apply one decoded JSON document.  Returns the number of updated aircraft."""
    aircraft = document.get("aircraft") or []
    updated = 0
    for entry in aircraft:
        if not isinstance(entry, dict):
            continue
        icao = entry.get("hex", entry.get("icao"))
        if icao is None:
            continue
        kwargs = {}
        for name in ("latitude", "longitude"):
            if name in entry:
                kwargs[name] = entry[name]
        if "lat" in entry:
            kwargs["latitude"] = entry["lat"]
        if "lon" in entry:
            kwargs["longitude"] = entry["lon"]
        if "alt_baro" in entry:
            kwargs["altitude_ft"] = _altitude(entry["alt_baro"])
        if "gs" in entry:
            kwargs["velocity_kt"] = entry["gs"]
        if "track" in entry:
            kwargs["track_deg"] = entry["track"]
        if "seen_pos" in entry:
            kwargs["position_age_s"] = _number(entry["seen_pos"])
        try:
            result = table.update(icao, now_met_ms, **kwargs)
        except (TypeError, ValueError):
            continue
        if result is not None:
            updated += 1
    return updated


class JsonSource:
    """Background poller for the decoder HTTP endpoint."""

    def __init__(self, url: str, table: AircraftTable, clock_ms, period_s: float = 1.0) -> None:
        self.url = url
        self.table = table
        self._clock_ms = clock_ms
        self.period_s = max(0.1, period_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_update_met_ms = 0
        self.updates = 0
        self.errors = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="adsb-json", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def poll_once(self) -> int:
        try:
            with urllib.request.urlopen(self.url, timeout=1.5) as response:
                document = json.load(response)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            self.errors += 1
            return 0
        count = apply_aircraft_json(document, self.table, self._clock_ms())
        self.last_update_met_ms = self._clock_ms()
        self.updates += count
        return count

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.period_s)


class SimulatedSource:
    """Generate a few moving aircraft for bench simulation."""

    def __init__(self, table: AircraftTable, clock_ms, count: int = 3) -> None:
        self.table = table
        self._clock_ms = clock_ms
        self.count = count
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = time.monotonic()
        self.updates = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="adsb-sim", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def poll_once(self) -> int:
        now = self._clock_ms()
        elapsed = time.monotonic() - self._t0
        updated = 0
        for index in range(self.count):
            icao = 0xA00000 + index
            lat = -23.55 + 0.05 * math.sin(elapsed / 10.0 + index)
            lon = -46.63 + 0.05 * math.cos(elapsed / 10.0 + index)
            self.table.update(
                icao,
                now,
                latitude=lat,
                longitude=lon,
                altitude_ft=10000 + index * 500,
                velocity_kt=180 + index * 10,
                track_deg=(elapsed * 5.0 + index * 30) % 360.0,
            )
            updated += 1
        self.updates += updated
        return updated

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(1.0)
