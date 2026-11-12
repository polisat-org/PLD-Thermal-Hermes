"""DS18B20 sensors on the 1-wire bus."""

from __future__ import annotations

import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)


def parse_w1_slave(text: str) -> float | None:
    """Parse a ``w1_slave`` file.  Returns None on CRC error or bad data."""
    lines = [line.strip() for line in text.strip().splitlines()]
    if len(lines) < 2:
        return None
    if not lines[0].endswith("YES"):
        return None
    for token in lines[1].split():
        if token.startswith("t="):
            try:
                raw = int(token[2:])
            except ValueError:
                return None
            value = raw / 1000.0
            if not math.isfinite(value) or value < -55.0 or value > 125.0:
                return None
            return value
    return None


def discover_sensors(root: str = "/sys/bus/w1/devices") -> list[Path]:
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(
        (path for path in base.iterdir() if path.name.startswith("28-")),
        key=lambda path: path.name,
    )


def read_sensor(path: Path) -> float | None:
    try:
        return parse_w1_slave((path / "w1_slave").read_text(encoding="ascii"))
    except OSError:
        return None


class ThermalSensors:
    """Read two groups of DS18B20 sensors, with failure tracking."""

    def __init__(
        self,
        sensor_map: dict[int, tuple[str, ...]] | None = None,
        root: str = "/sys/bus/w1/devices",
        fail_limit: int = 3,
    ) -> None:
        self.root = root
        self.fail_limit = max(1, int(fail_limit))
        self._failures = {1: 0, 2: 0}
        self._paths: dict[int, list[Path]] = {}
        self._configure(sensor_map or {})

    def _configure(self, sensor_map: dict[int, tuple[str, ...]]) -> None:
        if sensor_map.get(1) or sensor_map.get(2):
            for group in (1, 2):
                self._paths[group] = [
                    Path(self.root) / rom for rom in sensor_map.get(group, ())
                ]
            return
        found = discover_sensors(self.root)
        self._paths = {1: [], 2: []}
        if not found:
            return
        if len(found) == 1:
            self._paths[1] = found
        elif len(found) <= 2:
            self._paths[1] = found[:1]
            self._paths[2] = found[1:2]
        else:
            self._paths[1] = found[:2]
            self._paths[2] = found[2:4]

    @property
    def paths(self) -> dict[int, list[Path]]:
        return {group: list(paths) for group, paths in self._paths.items()}

    def read_groups(self) -> dict[int, tuple[float | None, float | None]]:
        result: dict[int, tuple[float | None, float | None]] = {}
        for group in (1, 2):
            values: list[float] = []
            for path in self._paths.get(group, []):
                value = read_sensor(path)
                if value is not None:
                    values.append(value)
            if values:
                self._failures[group] = 0
            else:
                self._failures[group] += 1
            if self._failures[group] >= self.fail_limit:
                values = []
            values = values[:2]
            result[group] = (
                values[0] if len(values) >= 1 else None,
                values[1] if len(values) >= 2 else None,
            )
        return result


class SimulatedSensors:
    """Deterministic sensors for bench simulation."""

    def __init__(self, temps: tuple[float, float] = (9.0, 11.0)) -> None:
        self.temps = temps

    def read_groups(self) -> dict[int, tuple[float | None, float | None]]:
        return {
            1: (self.temps[0], self.temps[0]),
            2: (self.temps[1], self.temps[1]),
        }
