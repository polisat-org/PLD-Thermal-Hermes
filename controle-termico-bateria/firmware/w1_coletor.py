#!/usr/bin/env python3
"""Coletor 1-Wire do Raspberry Pi: lê os DS18B20 pelo driver do kernel (w1-gpio + w1_therm).

Imprime uma linha por leitura no MESMO formato do sketch do Arduino da etapa 2, para que o
ao_vivo.py e o analisar.py funcionem sem mudança:
    D,<ms>,<índice>,<ROM>,<bruto em 1/128 °C>,<°C>        (falha: bruto -7040 e -127.0)
Linhas que começam com '#' são informativas. Só biblioteca padrão: roda no Raspberry Pi OS Lite.

Pré-requisito: dtoverlay=w1-gpio,gpiopin=<N> no /boot/firmware/config.txt (ou carregado com dtoverlay).

Uso no Pi:
    sudo python3 w1_coletor.py                  # contínuo, todas as sondas, 1 ciclo por segundo
    sudo python3 w1_coletor.py --csv sessao.csv # também grava no cartão
    python3 w1_coletor.py --listar              # mostra as sondas e sai
    sudo python3 w1_coletor.py --varrer         # procura em qual GPIO está o barramento
Do PC:  python ao_vivo.py --pi pi@<host>        # envia este arquivo pelo ssh e lê a saída

Com sudo, as sondas convertem juntas (therm_bulk_read): um ciclo leva ~0,75 s para todas.
Sem sudo, cada leitura dispara a sua própria conversão: ~0,75 s por sonda.
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

DESCONECTADO_BRUTO, DESCONECTADO_C = -7040, -127.0     # mesmos valores da DallasTemperature
GPIO_CANDIDATOS = [4, 17, 27, 22, 5, 6, 13, 19, 26, 23, 24, 25, 16, 20, 21, 12, 18]  # sem I2C (2, 3) e UART (14, 15)


def info(msg: str):
    print("# " + msg, flush=True)


def ler(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def achar_sondas(raiz: Path) -> list[Path]:
    return sorted(raiz.glob("28-*"))


def rom(p: Path) -> str:
    return p.name.replace("-", "").upper()


def descrever(raiz: Path, sondas: list[Path]):
    info(f"DS18B20 encontrados: {len(sondas)}")
    for i, p in enumerate(sondas):
        res = ler(p / "resolution") or "?"
        ext = ler(p / "ext_power")
        alim = {"1": "externa", "0": "parasita"}.get(ext or "", "?")
        info(f"sensor {i} ROM {rom(p)} resolucao {res} bits alimentacao {alim}")


def temperatura_mC(p: Path) -> int | None:
    v = ler(p / "temperature")
    try:
        return int(v) if v else None
    except ValueError:
        return None


class Leitor:
    def __init__(self, raiz: Path):
        self.raiz = raiz
        mestres = sorted(raiz.glob("w1_bus_master*"))
        self.bulk = mestres[0] / "therm_bulk_read" if mestres and (mestres[0] / "therm_bulk_read").exists() else None
        if self.bulk is None:
            info("sem therm_bulk_read: leitura uma a uma")

    def converter_todas(self):
        """Dispara a conversão simultânea e espera terminar (o arquivo lê -1 enquanto converte)."""
        if self.bulk is None:
            return
        try:
            self.bulk.write_text("trigger\n")
        except OSError as e:
            info(f"conversao simultanea indisponivel ({e.__class__.__name__}; rode com sudo): leitura uma a uma")
            self.bulk = None
            return
        limite = time.monotonic() + 1.5
        while time.monotonic() < limite:
            if ler(self.bulk) != "-1":
                return
            time.sleep(0.05)


def coletar(args):
    raiz = Path(args.raiz)
    sondas = achar_sondas(raiz)
    descrever(raiz, sondas)
    if not sondas:
        info("nenhuma sonda: confira o dtoverlay w1-gpio (pino), o pull-up e a solda; ou rode --varrer")
        return 1
    leitor = Leitor(raiz)
    arq = open(args.csv, "w", newline="") if args.csv else None
    wr = csv.writer(arq) if arq else None
    if wr:
        wr.writerow(["hora", "ms", "sensor", "rom", "bruto_1_128C", "temp_C"])
    t0 = time.monotonic()
    try:
        while True:
            ini = time.monotonic()
            leitor.converter_todas()
            ms = int((time.monotonic() - t0) * 1000)       # mesmo ms para todas: o ao_vivo emparelha por ele
            hora = time.strftime("%Y-%m-%d %H:%M:%S")
            for i, p in enumerate(sondas):
                mc = temperatura_mC(p)
                if mc is None:
                    bruto, c = DESCONECTADO_BRUTO, DESCONECTADO_C
                else:
                    bruto = round(mc * 128 / 1000)       # o kernel trunca: 23062 m°C = 2952/128 = 23,0625 °C
                    c = bruto / 128
                print(f"D,{ms},{i},{rom(p)},{bruto},{c:.4f}", flush=True)
                if wr:
                    wr.writerow([hora, ms, i, rom(p), bruto, f"{c:.4f}"])
            if arq:
                arq.flush()
            if args.ciclos > 0:
                args.ciclos -= 1
                if args.ciclos == 0:
                    break
            resto = args.periodo - (time.monotonic() - ini)
            if resto > 0:
                time.sleep(resto)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        if arq:
            arq.close()
    return 0


def varrer(args):
    """Carrega o w1-gpio pino a pino até aparecerem sondas. O w1-gpio só puxa a linha para baixo ou a deixa
    em alta impedância: não liga o gate de um AO3400A que esteja no pino testado."""
    raiz = Path(args.raiz)
    if list(raiz.glob("w1_bus_master*")):
        info("ja existe um barramento 1-Wire ativo; sondas nele: " + str(len(achar_sondas(raiz))))
        info("para varrer, remova o dtoverlay w1-gpio do config.txt e reinicie")
        return 1
    for g in args.pinos or GPIO_CANDIDATOS:
        r = subprocess.run(["dtoverlay", "w1-gpio", f"gpiopin={g}"], capture_output=True, text=True)
        if r.returncode:
            info(f"GPIO {g}: falha ao carregar o overlay ({r.stderr.strip()})")
            continue
        achou = []
        limite = time.monotonic() + 12                     # o mestre procura dispositivos a cada ~10 s
        while time.monotonic() < limite and not achou:
            time.sleep(0.5)
            achou = achar_sondas(raiz)
        subprocess.run(["dtoverlay", "-r", "w1-gpio"], capture_output=True)
        time.sleep(0.5)
        info(f"GPIO {g}: {len(achou)} sonda(s)" + (" <-- barramento" if achou else ""))
        if achou:
            info(f"coloque no config.txt: dtoverlay=w1-gpio,gpiopin={g}")
            return 0
    info("nenhum GPIO respondeu")
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default="/sys/bus/w1/devices", help="pasta do sysfs 1-Wire (troca só em teste)")
    ap.add_argument("--periodo", type=float, default=1.0, help="segundos entre ciclos (mínimo real ~0,75 s)")
    ap.add_argument("--ciclos", type=int, default=0, help="0 = contínuo")
    ap.add_argument("--csv", help="também grava as leituras neste arquivo")
    ap.add_argument("--listar", action="store_true", help="mostra as sondas e sai")
    ap.add_argument("--varrer", action="store_true", help="procura o GPIO do barramento (precisa de sudo)")
    ap.add_argument("--pinos", type=int, nargs="*", help="GPIOs a testar no --varrer")
    args = ap.parse_args()
    if args.varrer:
        return varrer(args)
    if args.listar:
        descrever(Path(args.raiz), achar_sondas(Path(args.raiz)))
        return 0
    return coletar(args)


if __name__ == "__main__":
    sys.exit(main())
