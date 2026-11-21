"""GPIO via pigpio. ON/OFF sao entradas; SDR e uma saida."""
from __future__ import annotations

import time


class Gpio:
    def __init__(self, cfg, simulate: bool = False) -> None:
        self.cfg = cfg
        self.simulate = simulate
        self.pi = None
        self._sim_on = False
        self._sim_off = False
        self._sim_sdr = False
        self._last_off = -1e9
        if not simulate:
            import pigpio

            self.pi = pigpio.pi()
            if not self.pi.connected:
                raise RuntimeError("pigpiod nao esta rodando (sudo systemctl start pigpiod)")
            for pin in (cfg.GPIO_ON, cfg.GPIO_OFF):
                self.pi.set_mode(pin, pigpio.INPUT)
                self.pi.set_pull_up_down(pin, pigpio.PUD_DOWN)
            self.pi.set_mode(cfg.GPIO_SDR, pigpio.OUTPUT)
            self._write(False)

    def on_pressed(self) -> bool:
        return self._sim_on if self.simulate else bool(self.pi.read(self.cfg.GPIO_ON))

    def off_pressed(self) -> bool:
        return self._sim_off if self.simulate else bool(self.pi.read(self.cfg.GPIO_OFF))

    def set_sim_inputs(self, on: bool = False, off: bool = False) -> None:
        self._sim_on = bool(on)
        self._sim_off = bool(off)

    def sdr_on(self) -> bool:
        if self.simulate:
            return self._sim_sdr
        value = bool(self.pi.read(self.cfg.GPIO_SDR))
        return value if self.cfg.SDR_ACTIVE_HIGH else not value

    def _write(self, on: bool) -> None:
        level = int(on) if self.cfg.SDR_ACTIVE_HIGH else int(not on)
        if self.simulate:
            self._sim_sdr = bool(on)
        else:
            self.pi.write(self.cfg.GPIO_SDR, level)

    def set_sdr(self, on: bool) -> bool:
        """Liga/desliga o SDR. Respeita o tempo minimo desligado. Devolve sucesso."""
        on = bool(on)
        if on and not self.sdr_on() and (time.monotonic() - self._last_off) < self.cfg.SDR_MIN_OFF_S:
            return False
        self._write(on)
        if not on:
            self._last_off = time.monotonic()
        return True

    def close(self) -> None:
        self._write(False)
        if self.pi is not None:
            self.pi.stop()
            self.pi = None
