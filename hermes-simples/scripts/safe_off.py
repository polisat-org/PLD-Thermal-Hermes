#!/usr/bin/env python3
"""ExecStopPost: garante que o SDR fique desligado mesmo se o app morreu."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_simples.config import Config
from hermes_simples.gpio import Gpio


def main() -> int:
    gpio = Gpio(Config(), simulate=False)
    gpio.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
