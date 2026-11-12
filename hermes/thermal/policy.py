"""Pure heater policy, independent from GPIO, sensors and threads."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntFlag


class ThermalStatus(IntFlag):
    OK = 0
    NO_SENSOR = 1
    OVERTEMP = 2
    CURRENT_LIMIT = 4


@dataclass(frozen=True)
class HeatingParams:
    start_c: float = 5.0
    target_c: float = 15.0
    stop_c: float = 15.0
    cutoff_c: float = 45.0
    min_duty: float = 0.05
    max_duty: float = 1.0
    rate_limit_per_s: float = 0.5
    supply_v: float = 13.8
    resistance_ohm: float = 82.0
    max_current_a: float = 0.5


@dataclass(frozen=True)
class HeaterDecision:
    duty: float
    heating: bool
    status: ThermalStatus = ThermalStatus.OK

    @property
    def duty_percent(self) -> float:
        return self.duty * 100.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def decide_heating(
    min_c: float | None,
    max_c: float | None,
    current_duty: float,
    heating: bool,
    dt_s: float,
    params: HeatingParams = HeatingParams(),
) -> HeaterDecision:
    """Decide the PWM duty for one battery pack.

    - no valid sensor -> off (fail-safe);
    - any sensor above ``cutoff_c`` -> off;
    - hysteresis: turn on at ``start_c``, turn off at ``stop_c``;
    - proportional duty between ``start_c`` and ``target_c``;
    - hardware current and slew-rate limits are applied last.
    """
    if min_c is None or max_c is None:
        return HeaterDecision(0.0, False, ThermalStatus.NO_SENSOR)
    if not (math.isfinite(min_c) and math.isfinite(max_c)):
        return HeaterDecision(0.0, False, ThermalStatus.NO_SENSOR)
    if max_c >= params.cutoff_c:
        return HeaterDecision(0.0, False, ThermalStatus.OVERTEMP)

    status = ThermalStatus.OK
    if heating:
        active = min_c < params.stop_c
    else:
        active = min_c <= params.start_c

    if not active:
        target = 0.0
    else:
        span = max(1e-6, params.target_c - params.start_c)
        target = _clamp((params.target_c - min_c) / span, 0.0, 1.0)
        if target > 0.0:
            target = max(target, params.min_duty)
        target = min(target, params.max_duty)

    current = max(0.0, min(1.0, float(current_duty)))
    if dt_s > 0.0 and params.rate_limit_per_s > 0.0:
        max_delta = params.rate_limit_per_s * dt_s
        target = _clamp(target, current - max_delta, current + max_delta)

    current_per_duty = params.supply_v / params.resistance_ohm
    if current_per_duty > 0.0:
        current_cap = params.max_current_a / current_per_duty
        if target > current_cap:
            target = max(0.0, current_cap)
            status |= ThermalStatus.CURRENT_LIMIT

    target = _clamp(target, 0.0, 1.0)
    return HeaterDecision(target, target > 0.0, status)
