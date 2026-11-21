"""O "satélite de mentira" para o teste local.

Roda o modelo de 8 nós da etapa 3 (03-sistema-modelo/rede_termica.py) e, a cada ciclo, escreve arquivos
w1_slave iguais aos do Pi numa pasta (w1/28-xxxx/w1_slave). O controle (pacote tcs) lê essa pasta como se fosse
/sys/bus/w1/devices e devolve o duty; o simulador transforma o duty em potência no bloco.

Sonda simulada: atraso de 1ª ordem (τ = 30 s), erro de fábrica igual ao medido no gelo (calibracao.json; a sonda 4
ainda não tem calibração e leva um erro estimado), ruído de 0,022 °C e resolução de 1/16 °C.
"""
from __future__ import annotations

import json
import math
import random
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FIRMWARE = AQUI.parent
sys.path.insert(0, str(FIRMWARE.parent / "03-sistema-modelo"))

import numpy as np  # noqa: E402
from rede_termica import IA, IB, ICA, ICB, IW, N_NOS, PARAM_NOMINAL, Rede, duracao, perfil_mesa, t_parede  # noqa: E402

PIOR_FRIO = {"G_mesa_E": 1.0, "G_E_P": 0.3, "G_P_bloco": 0.2, "eps_bloco": 0.8, "C_E": 250.0, "C_H": 300.0,
             "G_bloco_cel": 5.0}
CENARIOS = {
    "sala": ("Nominal: paredes da câmara a 20 °C", {}, "sala"),
    "segue": ("Paredes acompanham a mesa", {}, "segue"),
    "pior_frio": ("Pior frio: paredes acompanham a mesa e contatos no extremo frio", PIOR_FRIO, "segue"),
}
ERRO_SONDA4_ESTIMADO = -0.30   # °C: a sonda 4 ainda não foi ao gelo; valor típico das outras 3


def pasta_do_kernel(rom: str) -> str:
    rom = rom.lower()
    return f"{rom[:2]}-{rom[2:]}"


class Planta:
    def __init__(self, cenario="sala", raiz_w1: Path = AQUI / "w1", semente=1):
        titulo, mud, parede = CENARIOS[cenario]
        self.cenario, self.titulo, self.parede = cenario, titulo, parede
        self.p = PARAM_NOMINAL.com(**mud)
        self.rede = Rede(self.p)
        self.dur = duracao(self.p)
        self.t = 0.0
        self.T = np.full(N_NOS, self.p.T0)
        self.T[IW] = float(t_parede(0.0, parede, self.p))
        self.energia_J = [0.0, 0.0]
        self.rnd = random.Random(semente)
        self.raiz = Path(raiz_w1)
        mapa = json.loads((FIRMWARE / "sondas.json").read_text(encoding="utf-8"))["sondas"]
        calib = json.loads((FIRMWARE / "calibracao.json").read_text(encoding="utf-8"))["sondas"]
        b_por_num = {s["numero"]: s["b"] for s in calib.values()}
        self.sondas = {}
        for n, s in mapa.items():
            n = int(n)
            if not s.get("rom"):
                continue
            self.sondas[n] = {"rom": s["rom"], "no": IA if n in (1, 2) else IB,
                              "erro": b_por_num.get(n, ERRO_SONDA4_ESTIMADO), "ts": self.p.T0}
        self.falha = {n: "" for n in self.sondas}     # "", "desconectada", "crc", "85", "fria" (fora do canal, 10 °C)
        if self.raiz.exists():
            shutil.rmtree(self.raiz)
        self.raiz.mkdir(parents=True)
        # o "mestre" do barramento, com o arquivo da conversão simultânea (o tcs escreve "trigger" e espera ≠ −1)
        (self.raiz / "w1_bus_master1").mkdir()
        (self.raiz / "w1_bus_master1" / "therm_bulk_read").write_text("0\n", encoding="ascii")

    # ------------------------------------------------------------------ física
    def mesa(self, t=None):
        return float(perfil_mesa(self.t if t is None else t, self.p))

    def _par(self, t):
        return float(t_parede(t, self.parede, self.p))

    def avancar(self, dt: float, duty_a: float, duty_b: float, h: float = 0.5):
        """Avança dt segundos com os aquecedores em duty_a, duty_b (0 a 1). RK4 com passo h."""
        Qa, Qb = duty_a * self.p.pmax, duty_b * self.p.pmax
        n = max(1, int(round(dt / h)))
        h = dt / n
        r = self.rede
        for _ in range(n):
            t0 = self.t
            Tm0, Tw0 = self.mesa(t0), self._par(t0)
            Tm1, Tw1 = self.mesa(t0 + h / 2), self._par(t0 + h / 2)
            Tm2, Tw2 = self.mesa(t0 + h), self._par(t0 + h)
            k1 = r.derivada(self.T, Tm0, Tw0, Qa, Qb)
            k2 = r.derivada(self.T + h / 2 * k1, Tm1, Tw1, Qa, Qb)
            k3 = r.derivada(self.T + h / 2 * k2, Tm1, Tw1, Qa, Qb)
            k4 = r.derivada(self.T + h * k3, Tm2, Tw2, Qa, Qb)
            self.T = self.T + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
            self.T[IW] = Tw2
            self.t += h
            for s in self.sondas.values():
                s["ts"] += (1 - math.exp(-h / self.p.tau_sonda)) * (self.T[s["no"]] - s["ts"])
        self.energia_J[0] += Qa * dt
        self.energia_J[1] += Qb * dt

    # ------------------------------------------------------------------ "kernel" do 1-Wire
    def escrever_w1(self):
        """Escreve w1/28-xxxx/w1_slave de cada sonda, no formato do driver w1_therm do Linux."""
        for n, s in self.sondas.items():
            pasta = self.raiz / pasta_do_kernel(s["rom"])
            if self.falha[n] == "desconectada":
                if pasta.exists():
                    shutil.rmtree(pasta)
                continue
            pasta.mkdir(exist_ok=True)
            bruta = s["ts"] + s["erro"] + self.rnd.gauss(0.0, self.p.ruido_sonda)
            if self.falha[n] == "fria":                     # saiu do canal e mede o ar frio: lê baixo, CRC ok
                bruta = 10.0 + self.rnd.gauss(0.0, self.p.ruido_sonda)
            mC = int(round(bruta * 16)) * 1000 // 16          # resolução de 1/16 °C, em m°C como o kernel
            if self.falha[n] == "85":
                mC = 85000
            raw = (int(round(mC * 16 / 1000)) & 0xFFFF)
            bytes_ = f"{raw & 0xFF:02x} {raw >> 8:02x} 4b 46 7f ff 0c 10 1c"
            crc = "NO" if self.falha[n] == "crc" else "YES"
            (pasta / "w1_slave").write_text(f"{bytes_} : crc=1c {crc}\n{bytes_} t={mC}\n", encoding="ascii")

    def bruta_real(self, n):
        return self.sondas[n]["ts"] + self.sondas[n]["erro"]

    def fotografia(self):
        return {"t_min": self.t / 60, "mesa": self.mesa(), "parede": float(self.T[IW]),
                "bloco": [float(self.T[IA]), float(self.T[IB])],
                "celula": [float(self.T[ICA]), float(self.T[ICB])],
                "energia_Wh": [e / 3600 for e in self.energia_J]}

    @property
    def acabou(self):
        return self.t >= self.dur
