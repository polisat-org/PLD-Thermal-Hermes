#!/usr/bin/env python3
"""Force SDR power off and both heater PWM channels to zero.

Used as systemd ExecStopPost and by the operator before powering the stack down.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.config import CONFIG  # noqa: E402


def main() -> int:
    try:
        from hermes.gpio_control import PigpioGpio
        from hermes.pwm import PigpioPwm

        gpio = PigpioGpio()
        pwm = PigpioPwm(dict(CONFIG.PWM_PINS), CONFIG.PWM_FREQ_HZ, CONFIG.PWM_ACTIVE_HIGH)
        try:
            gpio.set_sdr(False)
            for pack in CONFIG.PWM_PINS:
                pwm.set_duty(pack, 0.0)
        finally:
            pwm.close()
            gpio.close()
        return 0
    except Exception as exc:  # pragma: no cover - hardware depends
        print(f"safe_off: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
