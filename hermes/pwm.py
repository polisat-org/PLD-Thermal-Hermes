"""PWM backends for the two battery heaters."""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger(__name__)


class PwmBackend(Protocol):
    def set_duty(self, pack: int, duty: float) -> None: ...
    def close(self) -> None: ...


def open_pigpio():
    try:
        import pigpio  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pigpio is not installed; install it or run with --simulate"
        ) from exc
    pi = pigpio.pi()
    if not pi.connected:
        raise RuntimeError("cannot connect to pigpiod (is it running?)")
    return pi, pigpio


def _clamp_duty(duty: float) -> float:
    try:
        value = float(duty)
    except (TypeError, ValueError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, value))


class PigpioPwm:
    def __init__(
        self,
        pins: dict[int, int],
        frequency_hz: int = 1000,
        active_high: bool = True,
    ) -> None:
        self.pins = dict(pins)
        self.frequency_hz = int(frequency_hz)
        self.active_high = active_high
        self.pi, self._pigpio = open_pigpio()
        self._backend = "hardware"
        for gpio in self.pins.values():
            result = self.pi.hardware_PWM(gpio, self.frequency_hz, 0)
            if result != 0:
                self._backend = "software"
                self.pi.set_PWM_frequency(gpio, self.frequency_hz)
                self.pi.set_PWM_range(gpio, 255)
        log.info("PWM backend: %s", self._backend)

    def set_duty(self, pack: int, duty: float) -> None:
        if pack not in self.pins:
            raise ValueError(f"unknown heater pack {pack}")
        value = _clamp_duty(duty)
        if not self.active_high:
            value = 1.0 - value
        gpio = self.pins[pack]
        if self._backend == "hardware":
            self.pi.hardware_PWM(gpio, self.frequency_hz, int(round(value * 1_000_000)))
        else:
            self.pi.set_PWM_dutycycle(gpio, int(round(value * 255)))

    def close(self) -> None:
        for pack in self.pins:
            try:
                self.set_duty(pack, 0.0)
            except Exception:  # pragma: no cover - best effort on shutdown
                pass
        try:
            self.pi.stop()
        except Exception:  # pragma: no cover
            pass


class MockPwm:
    def __init__(self) -> None:
        self.duties: dict[int, float] = {1: 0.0, 2: 0.0}
        self.closed = False

    def set_duty(self, pack: int, duty: float) -> None:
        if pack not in self.duties:
            raise ValueError(f"unknown heater pack {pack}")
        self.duties[pack] = _clamp_duty(duty)

    def close(self) -> None:
        self.duties = {1: 0.0, 2: 0.0}
        self.closed = True
