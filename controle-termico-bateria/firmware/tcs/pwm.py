"""Saída para os MOSFETs dos aquecedores. Duty 0 = desligado = estado seguro."""
from __future__ import annotations


def _limitar(d) -> float:
    try:
        d = float(d)
    except (TypeError, ValueError):
        return 0.0
    if d != d:          # NaN
        return 0.0
    return max(0.0, min(1.0, d))


class PwmPigpio:
    """Pi de verdade: PWM por hardware (GPIO 18/19) pelo daemon pigpiod."""

    def __init__(self, pinos: dict[str, int], hz: int = 1000):
        import pigpio  # só existe no Pi
        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise RuntimeError("pigpiod não está rodando (sudo systemctl start pigpiod)")
        self.pinos, self.hz = dict(pinos), int(hz)
        self.duty = {b: 0.0 for b in pinos}
        for b in pinos:
            self.ajustar(b, 0.0)

    def ajustar(self, bloco: str, duty: float):
        d = _limitar(duty)
        self.pi.hardware_PWM(self.pinos[bloco], self.hz, int(round(d * 1_000_000)))
        self.duty[bloco] = d

    def fechar(self):
        for b in self.pinos:
            try:
                self.ajustar(b, 0.0)
            except Exception:
                pass
        self.pi.stop()


class PwmMemoria:
    """Teste local: guarda o duty; o simulador lê daqui a potência de cada aquecedor."""

    def __init__(self, pinos: dict[str, int], hz: int = 1000):
        self.pinos = dict(pinos)
        self.duty = {b: 0.0 for b in pinos}

    def ajustar(self, bloco: str, duty: float):
        self.duty[bloco] = _limitar(duty)

    def fechar(self):
        self.duty = {b: 0.0 for b in self.pinos}
