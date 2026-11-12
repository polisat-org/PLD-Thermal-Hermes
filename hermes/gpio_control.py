"""Mission inputs and SDR power output."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .pwm import open_pigpio

log = logging.getLogger(__name__)


class GpioBackend(Protocol):
    def setup_output(self, pin: int, value: int = 0) -> None: ...
    def setup_input(self, pin: int) -> None: ...
    def write(self, pin: int, value: int) -> None: ...
    def read(self, pin: int) -> int: ...
    def close(self) -> None: ...


class PigpioGpio:
    def __init__(self) -> None:
        self.pi, self._pigpio = open_pigpio()

    def setup_output(self, pin: int, value: int = 0) -> None:
        self.pi.set_mode(pin, self._pigpio.OUTPUT)
        self.pi.write(pin, int(bool(value)))

    def setup_input(self, pin: int) -> None:
        self.pi.set_mode(pin, self._pigpio.INPUT)
        self.pi.set_pull_up_down(pin, self._pigpio.PUD_DOWN)

    def write(self, pin: int, value: int) -> None:
        self.pi.write(pin, int(bool(value)))

    def read(self, pin: int) -> int:
        return int(bool(self.pi.read(pin)))

    def close(self) -> None:
        for pin in (17, 22, 27):
            try:
                self.pi.set_mode(pin, self._pigpio.INPUT)
            except Exception:  # pragma: no cover
                pass
        try:
            self.pi.stop()
        except Exception:  # pragma: no cover
            pass


class MockGpio:
    def __init__(self) -> None:
        self.outputs: dict[int, int] = {}
        self.inputs: dict[int, int] = {}
        self.modes: dict[int, str] = {}
        self.closed = False

    def setup_output(self, pin: int, value: int = 0) -> None:
        self.modes[pin] = "out"
        self.outputs[pin] = int(bool(value))

    def setup_input(self, pin: int) -> None:
        self.modes[pin] = "in"
        self.inputs.setdefault(pin, 0)

    def write(self, pin: int, value: int) -> None:
        self.outputs[pin] = int(bool(value))

    def read(self, pin: int) -> int:
        return self.inputs.get(pin, 0)

    def close(self) -> None:
        self.closed = True


class GpioController:
    def __init__(
        self,
        backend,
        mission_on_pin: int = 27,
        mission_off_pin: int = 22,
        sdr_power_pin: int = 17,
        active_high: bool = True,
        debounce_s: float = 0.015,
    ) -> None:
        self.backend = backend
        self.mission_on_pin = mission_on_pin
        self.mission_off_pin = mission_off_pin
        self.sdr_power_pin = sdr_power_pin
        self.active_high = active_high
        self.debounce_s = debounce_s
        self._state = {mission_on_pin: 0, mission_off_pin: 0}
        self._raw = {mission_on_pin: 0, mission_off_pin: 0}
        self._changed_at = {mission_on_pin: 0.0, mission_off_pin: 0.0}
        self.sdr_on = False
        backend.setup_input(mission_on_pin)
        backend.setup_input(mission_off_pin)
        backend.setup_output(sdr_power_pin, 0)

    def set_sdr(self, on: bool) -> None:
        value = 1 if on else 0
        if not self.active_high:
            value = 0 if value else 1
        self.backend.write(self.sdr_power_pin, value)
        self.sdr_on = bool(on)

    def poll_events(self) -> list[str]:
        events: list[str] = []
        now = time.monotonic()
        for name, pin in (("on", self.mission_on_pin), ("off", self.mission_off_pin)):
            raw = self.backend.read(pin)
            if not self.active_high:
                raw = 0 if raw else 1
            if raw != self._raw[pin]:
                self._raw[pin] = raw
                self._changed_at[pin] = now
            if raw != self._state[pin] and now - self._changed_at[pin] >= self.debounce_s:
                self._state[pin] = raw
                if raw:
                    events.append(name)
        return events

    def close(self) -> None:
        try:
            self.set_sdr(False)
        finally:
            self.backend.close()
