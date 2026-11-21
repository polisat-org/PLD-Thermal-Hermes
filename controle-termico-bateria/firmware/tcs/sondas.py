"""Leitura das sondas DS18B20 pelo 1-Wire do Linux (/sys/bus/w1/devices/28-*/w1_slave), com calibração.

No Pi a raiz é /sys/bus/w1/devices. No teste local, é uma pasta com arquivos iguais, escritos pelo simulador.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

VALOR_DE_LIGAR = 85.0   # o DS18B20 devolve 85,000 °C se for lido antes da primeira conversão: descartar


def pasta_do_kernel(rom: str) -> str:
    """'280000004801DC' → '28-0000004801dc' (nome da pasta que o kernel cria)."""
    rom = rom.lower()
    return f"{rom[:2]}-{rom[2:]}"


def interpretar(texto: str, tmin: float, tmax: float) -> tuple[float | None, str]:
    """Conteúdo do w1_slave → (°C, '') ou (None, motivo)."""
    linhas = texto.strip().splitlines()
    if len(linhas) < 2:
        return None, "arquivo incompleto"
    if not linhas[0].strip().endswith("YES"):
        return None, "CRC errado"
    if "t=" not in linhas[1]:
        return None, "sem t="
    try:
        t = int(linhas[1].split("t=")[1]) / 1000.0
    except ValueError:
        return None, "t= ilegível"
    if t == VALOR_DE_LIGAR:
        return None, "85 °C (valor de ligar)"
    if not (tmin <= t <= tmax):
        return None, "fora da faixa"
    return t, ""


class Sondas:
    def __init__(self, cfg, raiz: str | None = None):
        self.cfg = cfg
        self.raiz = Path(raiz or cfg.W1_RAIZ)
        mapa = json.loads(Path(cfg.SONDAS_JSON).read_text(encoding="utf-8"))["sondas"]
        self.rom = {int(n): s["rom"] for n, s in mapa.items() if s.get("rom")}
        calib = json.loads(Path(cfg.CALIB_JSON).read_text(encoding="utf-8"))
        self.calib_ativa = bool(calib.get("ativa", True))
        self.ab = {s["numero"]: (s["a"], s["b"]) for s in calib["sondas"].values()}
        mestres = sorted(self.raiz.glob("w1_bus_master*"))
        bulk = mestres[0] / "therm_bulk_read" if mestres else None
        self.bulk = bulk if bulk is not None and bulk.exists() else None

    def converter_todas(self) -> bool:
        """Dispara a conversão de TODAS as sondas ao mesmo tempo e espera terminar (~0,75 s; o arquivo lê −1 enquanto
        converte). Depois, ler o w1_slave de cada uma só busca o resultado, sem nova conversão (driver w1_therm).
        Precisa de root (o serviço roda como root). Devolve False se não der (sem permissão, erro ou estouro de 1,5 s):
        aí cada leitura converte sozinha. Erro de escrita desliga a conversão simultânea de vez (bulk = None)."""
        if self.bulk is None:
            return False
        try:
            self.bulk.write_text("trigger\n")
        except OSError:
            self.bulk = None
            return False
        limite = time.monotonic() + 1.5
        while time.monotonic() < limite:
            try:
                if self.bulk.read_text().strip() != "-1":
                    return True
            except OSError:
                return False
            time.sleep(0.02)
        return False                    # estourou o tempo: a conversão não terminou

    def tem_calibracao(self, n: int) -> bool:
        return self.calib_ativa and n in self.ab

    def ler(self, n: int) -> dict:
        """Lê a sonda nº n. Devolve {'bruta', 'temp', 'erro'}; temp = calibrada (ou bruta, se não houver calibração)."""
        if n not in self.rom:
            return {"bruta": None, "temp": None, "erro": "sem ROM no sondas.json"}
        arq = self.raiz / pasta_do_kernel(self.rom[n]) / "w1_slave"
        try:
            texto = arq.read_text(encoding="ascii")
        except OSError:
            bruta, erro = None, "não encontrada"
        else:
            bruta, erro = interpretar(texto, self.cfg.T_MIN_VALIDA_C, self.cfg.T_MAX_VALIDA_C)
        if bruta is None:
            return {"bruta": None, "temp": None, "erro": erro}
        if self.tem_calibracao(n):
            a, b = self.ab[n]
            temp = (bruta - b) / a
        else:
            temp = bruta
        return {"bruta": bruta, "temp": temp, "erro": ""}
