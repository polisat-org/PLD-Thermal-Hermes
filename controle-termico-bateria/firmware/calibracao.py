#!/usr/bin/env python3
"""Calibração das sondas DS18B20: leitura bruta (não calibrada) e calibrada, lado a lado.

A tabela fica em calibracao.json (mesma pasta): por ROM, T = (leitura - b) / a.
Só biblioteca padrão: o mesmo arquivo serve ao PC (ao_vivo.py, analisar.py) e ao firmware no Pi.

Como módulo:
    from calibracao import Calibracoes
    cal = Calibracoes()                 # lê calibracao.json
    cal.corrigir("2800000048D377", 18.44)   # -> 18.775

Como programa (tabela no terminal, 1 linha por segundo):
    python calibracao.py --pi polisat-pi      # do PC, lê o Pi pelo ssh
    python3 calibracao.py                     # no próprio Pi, lê o /sys direto
    python calibracao.py --pi polisat-pi --n 10 --csv saida.csv
    python calibracao.py --mostrar            # só imprime a tabela de correção

Ligar e desligar (vale para tudo que usa este módulo nesta máquina, inclusive o painel ao_vivo.py):
    python3 calibracao.py --estado            # diz se está ligada
    python3 calibracao.py --desligar          # "ativa": false no calibracao.json -> leitura bruta
    python3 calibracao.py --ligar             # "ativa": true  -> leitura corrigida
Desligada, a tabela continua no arquivo; corrigir() só devolve a leitura sem mudar.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
W1 = Path("/sys/bus/w1/devices")


class Calibracoes:
    """Tabela de correção por ROM. ROM sem entrada: devolve a leitura sem mudar (a = 1, b = 0)."""

    def __init__(self, arquivo: Path | str = AQUI / "calibracao.json"):
        self.arquivo = Path(arquivo)
        d = json.loads(self.arquivo.read_text(encoding="utf-8")) if self.arquivo.exists() else {"sondas": {}}
        self.versao = d.get("versao", "sem calibração")
        self.ativa = bool(d.get("ativa", True))          # chave liga/desliga (--ligar / --desligar)
        self.pendentes = d.get("pendentes", {})        # sondas ainda sem ROM ou sem calibração
        self.sondas = {rom.upper(): (float(v.get("a", 1.0)), float(v.get("b", 0.0)), v.get("numero"))
                       for rom, v in d.get("sondas", {}).items()}

    def ab(self, rom: str) -> tuple[float, float]:
        a, b, _ = self.sondas.get(rom.upper(), (1.0, 0.0, None))
        return a, b

    def numero(self, rom: str):
        return self.sondas.get(rom.upper(), (1.0, 0.0, None))[2]

    def corrigir(self, rom: str, leitura: float | None) -> float | None:
        """Leitura bruta (°C) -> calibrada (°C). None e -127 (falha) passam sem mudar; desligada, devolve a bruta."""
        if leitura is None or leitura == -127.0 or not self.ativa:
            return leitura
        a, b = self.ab(rom)
        return (leitura - b) / a


def rom_do_kernel(nome: str) -> str:
    """'28-00000048d377' (nome no /sys) -> '2800000048D377' (ROM no padrão da equipe)."""
    familia, serie = nome.split("-", 1)
    return (familia + serie).upper()


# ---------------------------------------------------------------------------------------- fontes
SCRIPT_REMOTO = ("while :; do for f in /sys/bus/w1/devices/28-*; do "
                 "echo \"$(basename $f) $(cat $f/temperature 2>/dev/null || echo erro)\"; done; echo fim; "
                 "sleep {periodo}; done")


def ciclos_local(periodo: float):
    while True:
        leit = {}
        for d in sorted(W1.glob("28-*")):
            try:
                leit[rom_do_kernel(d.name)] = int((d / "temperature").read_text().strip()) / 1000
            except (OSError, ValueError):
                leit[rom_do_kernel(d.name)] = None
        yield leit
        time.sleep(periodo)


def ciclos_pi(destino: str, periodo: float):
    cmd = ["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=5", destino,
           SCRIPT_REMOTO.format(periodo=periodo)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    leit = {}
    try:
        for linha in proc.stdout:
            linha = linha.strip()
            if linha == "fim":
                yield leit
                leit = {}
            elif " " in linha:
                nome, v = linha.split(" ", 1)
                leit[rom_do_kernel(nome)] = None if v == "erro" else int(v) / 1000
    finally:
        proc.terminate()
    print(f"# conexão com {destino} terminou (código {proc.wait()})", file=sys.stderr)


# ---------------------------------------------------------------------------------------- programa
def main():
    ap = argparse.ArgumentParser(description="Leitura bruta e calibrada das sondas DS18B20")
    ap.add_argument("--pi", metavar="HOST", help="lê o Pi pelo ssh (sem isso, lê o /sys local)")
    ap.add_argument("--periodo", type=float, default=1.0, help="segundos entre ciclos")
    ap.add_argument("--n", type=int, default=0, help="número de ciclos (0 = até Ctrl+C)")
    ap.add_argument("--csv", help="grava também em CSV")
    ap.add_argument("--calibracao", default=str(AQUI / "calibracao.json"))
    ap.add_argument("--mostrar", action="store_true", help="só mostra a tabela de correção")
    ap.add_argument("--estado", action="store_true", help="diz se a calibração está ligada")
    ap.add_argument("--ligar", action="store_true", help="liga a calibração (\"ativa\": true)")
    ap.add_argument("--desligar", action="store_true", help="desliga a calibração (\"ativa\": false)")
    args = ap.parse_args()

    if args.ligar or args.desligar:
        arq = Path(args.calibracao)
        d = json.loads(arq.read_text(encoding="utf-8"))
        d = {"ativa": bool(args.ligar), **{k: v for k, v in d.items() if k != "ativa"}}
        arq.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")   # LF: mesmo hash no PC e no Pi
    cal = Calibracoes(args.calibracao)
    if args.estado or args.ligar or args.desligar:
        print(f"Calibração {'LIGADA' if cal.ativa else 'DESLIGADA (leitura bruta)'} em {cal.arquivo}")
        return
    print(f"# calibração {cal.versao} ({cal.arquivo.name}); T = (leitura - b) / a"
          + ("" if cal.ativa else "  ** DESLIGADA: 'calibrado' = bruto (python3 calibracao.py --ligar) **"))
    for rom, (a, b, n) in sorted(cal.sondas.items(), key=lambda kv: kv[1][2] or 99):
        print(f"#   sonda {n}  {rom}  a = {a:.4f}  b = {b:+.3f} °C  (corrige {-b / a:+.3f} °C)")
    for n, v in sorted(cal.pendentes.items()):
        print(f"#   sonda {n}  SEM CALIBRAÇÃO ({v.get('furo', '')}): {v.get('como', '')}")
    if args.mostrar:
        return

    fonte = ciclos_pi(args.pi, args.periodo) if args.pi else ciclos_local(args.periodo)
    arq = open(args.csv, "w", newline="", encoding="utf-8") if args.csv else None
    wr = csv.writer(arq) if arq else None
    if wr:
        wr.writerow(["hora", "sonda", "rom", "bruto_C", "calibrado_C"])
    t0, k = time.time(), 0
    try:
        for leit in fonte:
            hora = time.strftime("%H:%M:%S")
            partes = []
            for rom in sorted(leit, key=lambda r: cal.numero(r) or 99):
                bruto = leit[rom]
                c = cal.corrigir(rom, bruto)
                n = cal.numero(rom) or "?"
                partes.append(f"S{n} {'falha':>7}" if bruto is None else f"S{n} {bruto:7.3f} -> {c:7.3f}")
                if wr:
                    wr.writerow([hora, n, rom, "" if bruto is None else f"{bruto:.4f}", "" if c is None else f"{c:.4f}"])
            print(f"{hora}  " + "   ".join(partes) + "   (bruto -> calibrado, °C)", flush=True)
            if arq:
                arq.flush()
            k += 1
            if args.n and k >= args.n:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if arq:
            arq.close()
    print(f"# {k} ciclos em {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
