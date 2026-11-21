"""No Pi:  cd ~/tcs && python3 -m tcs            (PWM real pelo pigpiod, sondas de /sys/bus/w1/devices)
        python3 -m tcs --sem-pwm                         (só lê e decide, não mexe nos pinos)

Payload da equipe: desde 19/11 roda o hermes-simples (só ADS-B/UART; GPIO 27, 22, 17 e /dev/serial0), sem parte
térmica, e pode ficar ligado junto. O hermes-payload antigo (que tinha um módulo térmico nos GPIO 12/13) está parado e
desligado no boot: se alguém religá-lo, pare-o antes de ligar o PWM (sudo systemctl stop hermes-payload).
"""
from __future__ import annotations

import argparse
import signal
import sys
from datetime import datetime

from .config import Config
from .controlador import Controlador
from .pwm import PwmMemoria, PwmPigpio
from .sondas import Sondas


def main():
    ap = argparse.ArgumentParser(prog="tcs", description="Controle térmico das baterias (PoliSat / Hermes)")
    ap.add_argument("--sem-pwm", action="store_true", help="não mexe nos pinos (só mostra a decisão)")
    ap.add_argument("--ref", type=float, help="temperatura desejada (°C)")
    ap.add_argument("--lei", choices=["lqi", "pi", "liga_desliga"])
    a = ap.parse_args()
    cfg = Config()
    if a.ref is not None:
        cfg.REF_C = a.ref
    if a.lei:
        cfg.LEI = a.lei
    pinos = {b: d["gpio"] for b, d in cfg.BLOCOS.items()}
    sondas = Sondas(cfg)
    if sondas.bulk is None or not sondas.converter_todas():
        sondas.bulk = None                  # coerente com o período: o controlador lê uma a uma
        cfg.PERIODO_S = cfg.PERIODO_SEM_BULK_S
        print(f"tcs: AVISO: sem conversão simultânea (therm_bulk_read; rode como root/serviço). Lendo uma a uma, "
              f"período {cfg.PERIODO_S} s: não cumpre R06 (1 Hz).")
    pwm = PwmMemoria(pinos) if a.sem_pwm else PwmPigpio(pinos, cfg.PWM_HZ)
    log = cfg.PASTA_LOG / f"tcs-{datetime.now():%Y%m%d-%H%M%S}.csv"
    c = Controlador(cfg, sondas, pwm, log)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # systemctl stop → finally → PWM em 0
    if cfg.LEI == "lqi":
        from .lei import ganhos_lqi
        g = ganhos_lqi(cfg.LQI_GANHOS)
        if abs(g["dt_s"] - cfg.PERIODO_S) > 0.05 * cfg.PERIODO_S:
            sys.exit(f"tcs: lqi_ganhos.json foi calculado para {g['dt_s']} s, mas PERIODO_S = {cfg.PERIODO_S} s. "
                     "Refaça os ganhos (03-sistema-modelo/gerar_ganhos_lqi.py --dt ...) ou acerte o período.")
        print(f"tcs: LQI com ganhos de {g['gerado_em']} (período {g['dt_s']} s)")
    print(f"tcs: lei {cfg.LEI}, referência {cfg.REF_C} °C, período {cfg.PERIODO_S} s, "
          f"PWM {'simulado' if a.sem_pwm else 'REAL'}, log {log}")
    try:
        c.rodar()                           # o finally do rodar() já zerou os PWMs e fechou o log
    except KeyboardInterrupt:
        print("tcs: interrompido (Ctrl+C); aquecedores em 0, log fechado.")


if __name__ == "__main__":
    main()
