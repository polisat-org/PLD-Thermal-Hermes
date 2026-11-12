"""Thermal control loop: sensors -> policy -> PWM."""

from __future__ import annotations

import logging
import threading
import time

from hermes.protocol.payloads import ThermalSample

from .policy import HeatingParams, ThermalStatus, decide_heating

log = logging.getLogger(__name__)


class ThermalController:
    def __init__(self, config, sensors, pwm, met_ms_fn) -> None:
        self.config = config
        self.sensors = sensors
        self.pwm = pwm
        self._met_ms = met_ms_fn
        self.params = HeatingParams(
            start_c=config.TEMP_START_C,
            target_c=config.TEMP_TARGET_C,
            stop_c=config.TEMP_STOP_C,
            cutoff_c=config.TEMP_CUTOFF_C,
            min_duty=config.HEAT_MIN_DUTY,
            max_duty=config.HEAT_MAX_DUTY,
            rate_limit_per_s=config.HEAT_RATE_LIMIT_PER_S,
            supply_v=config.HEATER_SUPPLY_V,
            resistance_ohm=config.HEATER_RESISTANCE_OHM,
            max_current_a=config.HEATER_MAX_CURRENT_A,
        )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._heating = {1: False, 2: False}
        self._duty = {1: 0.0, 2: 0.0}
        self._temps: tuple[float | None, ...] = (None, None, None, None)
        self._status = ThermalStatus.NO_SENSOR
        self._last_tick = 0.0
        self._enabled = True
        self.ticks = 0

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        for pack in (1, 2):
            self.pwm.set_duty(pack, 0.0)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="thermal", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        for pack in (1, 2):
            try:
                self.pwm.set_duty(pack, 0.0)
            except Exception:  # pragma: no cover
                log.exception("failed to zero PWM pack %s", pack)
        try:
            self.pwm.close()
        except Exception:  # pragma: no cover
            log.exception("failed to close PWM backend")

    def tick(self, now_mono: float | None = None) -> ThermalSample:
        now = time.monotonic() if now_mono is None else now_mono
        dt = 0.0 if self._last_tick == 0.0 else max(0.0, now - self._last_tick)
        self._last_tick = now
        with self._lock:
            previous_duty = dict(self._duty)
            previous_heating = dict(self._heating)
            enabled = self._enabled
        groups = self.sensors.read_groups()
        status = ThermalStatus.OK
        temps: list[float | None] = []
        duties: list[float] = []
        new_duty: dict[int, float] = {}
        new_heating: dict[int, bool] = {}
        for pack in (1, 2):
            values = [value for value in groups.get(pack, (None, None)) if value is not None]
            temps.extend(groups.get(pack, (None, None)))
            min_c = min(values) if values else None
            max_c = max(values) if values else None
            decision = decide_heating(
                min_c,
                max_c,
                previous_duty[pack],
                previous_heating[pack],
                dt,
                self.params,
            )
            if not enabled:
                decision = type(decision)(0.0, False, decision.status)
            new_heating[pack] = decision.heating
            new_duty[pack] = decision.duty
            status |= decision.status
            self.pwm.set_duty(pack, decision.duty)
            duties.append(decision.duty)
        with self._lock:
            self._heating = new_heating
            self._duty = new_duty
            self._temps = tuple(temps[:4])
            self._status = status
        self.ticks += 1
        return ThermalSample(tuple(temps[:4]), (duties[0], duties[1]), int(status))

    def snapshot(self) -> ThermalSample:
        with self._lock:
            return ThermalSample(self._temps, (self._duty[1], self._duty[2]), int(self._status))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - keep the loop alive
                log.exception("thermal tick failed")
                for pack in (1, 2):
                    self._duty[pack] = 0.0
                    self._heating[pack] = False
                    try:
                        self.pwm.set_duty(pack, 0.0)
                    except Exception:
                        log.exception("failed to zero PWM pack %s", pack)
            self._stop.wait(self.config.SENSOR_POLL_S)
