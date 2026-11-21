"""Liga o controle (pacote tcs, o mesmo código do Pi) ao satélite de mentira (planta.py), ciclo a ciclo.

Um ciclo, como no Pi: o "kernel" escreve as sondas → o tcs lê as 4 (0,75 s cada) → decide → ajusta o PWM →
espera até completar PERIODO_S (1 s com a conversão simultânea; 5 s sem ela).

Sem página (para conferir contra o modelo da etapa 3):
    python -I teste_local/bancada.py sala liga_desliga
"""
from __future__ import annotations

import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))      # firmware/ → import tcs
sys.path.insert(0, str(AQUI))

from tcs.config import Config  # noqa: E402
from tcs.controlador import Controlador  # noqa: E402
from tcs.pwm import PwmMemoria  # noqa: E402
from tcs.sondas import Sondas  # noqa: E402
from planta import Planta  # noqa: E402


class Bancada:
    def __init__(self, cenario="sala", ref=20.0, lei="lqi", hist=0.5, log=True, raiz_w1=None, bulk=True):
        """bulk=False imita o Pi sem conversão simultânea (sem root): período de reserva de 5 s."""
        self.cfg = Config()
        self.cfg.REF_C, self.cfg.LEI, self.cfg.HIST_C = ref, lei, hist
        self.cfg.PASTA_LOG = AQUI / "logs"
        self.planta = Planta(cenario, raiz_w1=raiz_w1 or AQUI / "w1")
        if not bulk:
            (self.planta.raiz / "w1_bus_master1" / "therm_bulk_read").unlink()
            self.cfg.PERIODO_S = self.cfg.PERIODO_SEM_BULK_S
        self.pwm = PwmMemoria({b: d["gpio"] for b, d in self.cfg.BLOCOS.items()})
        self.ctrl = Controlador(self.cfg, Sondas(self.cfg, raiz=str(self.planta.raiz)), self.pwm,
                                (AQUI / "logs" / f"teste-{cenario}-{lei}-{ref:g}C.csv") if log else None)
        self.amostras = []

    @property
    def periodo(self) -> float:
        """Período do laço, como no Pi: fixo, mas o controlador passa a 5 s se a conversão simultânea parar."""
        return self.cfg.PERIODO_S

    def passo(self) -> dict:
        self.planta.escrever_w1()
        s = self.ctrl.ciclo(agora=self.planta.t)
        foto = self.planta.fotografia()
        amostra = {**foto,
                   "sonda": {n: v["temp"] for n, v in s["sondas"].items()},
                   "bruta": {n: v["bruta"] for n, v in s["sondas"].items()},
                   "erro": {n: v["erro"] for n, v in s["sondas"].items()},
                   "calibrada": {n: v["calibrada"] for n, v in s["sondas"].items()},
                   "medida": [s["blocos"][b]["medida"] for b in ("A", "B")],
                   "duty": [s["blocos"][b]["duty"] for b in ("A", "B")],
                   "situacao": [s["blocos"][b]["texto"] for b in ("A", "B")]}
        self.amostras.append(amostra)
        self.planta.avancar(self.periodo, self.pwm.duty["A"], self.pwm.duty["B"])
        return amostra

    def fechar(self):
        self.ctrl.fechar()


def resumo(amostras, pmax):
    cel = [min(a["celula"]) for a in amostras]
    return {"energia_Wh": round(sum(amostras[-1]["energia_Wh"]), 3),
            "celula_min_C": round(min(cel), 2), "celula_max_C": round(max(max(a["celula"]) for a in amostras), 2),
            "min_abaixo_17": round(sum(1 for c in cel if c < 17) * (amostras[1]["t_min"] - amostras[0]["t_min"]), 1),
            "bloco_max_C": round(max(max(a["bloco"]) for a in amostras), 2)}


if __name__ == "__main__":
    cen = sys.argv[1] if len(sys.argv) > 1 else "sala"
    lei = sys.argv[2] if len(sys.argv) > 2 else "lqi"
    b = Bancada(cen, lei=lei, raiz_w1=AQUI / "w1-lote" / f"{cen}-{lei}")
    while not b.planta.acabou:
        b.passo()
    b.fechar()
    print(cen, lei, resumo(b.amostras, b.cfg.PMAX_W))
