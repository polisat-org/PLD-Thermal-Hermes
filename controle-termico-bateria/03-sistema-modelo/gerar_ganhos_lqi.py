"""Gera os ganhos do LQI para o firmware (firmware/tcs/lqi_ganhos.json), no período do laço do Pi.

    python gerar_ganhos_lqi.py            # 1 s (laço normal, conversão simultânea) e 5 s (reserva, uma a uma)
    python gerar_ganhos_lqi.py --dt 5     # só um período

O Pi Zero não tem numpy: lá só se multiplicam as matrizes calculadas aqui. O JSON leva também o modelo e os pesos
usados (para o relatório) e um vetor de teste: uma sequência de leituras e os comandos que controle.lqi (a
referência da simulação) devolve para ela. firmware/teste_local/testar_lqi.py confere que o firmware dá o mesmo.

Refazer este arquivo sempre que o modelo do bloco mudar (ex.: depois do 1º teste de aquecedor) ou o período mudar.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

AQUI = Path(__file__).parent
sys.path.insert(0, str(AQUI))
from controle import REF, lqi  # noqa: E402
from rede_termica import PARAM_NOMINAL as P  # noqa: E402

PASTA_SAIDA = AQUI.parent / "firmware" / "tcs"           # lqi_ganhos_<T>s.json (Config.LQI_GANHOS)


def vetor_de_teste(dt, ref=REF, n=400, semente=7):
    """Leituras sintéticas (esfria de 22 para ~12 °C, volta a 25 °C, com ruído e degrau) e os comandos da referência.
    As duas malhas recebem leituras diferentes para testar a independência dos blocos."""
    rng = np.random.default_rng(semente)
    t = np.arange(n) * dt
    base = 22 - 10 * (1 - np.exp(-t / 600)) + 13 * (t > 0.6 * t[-1]) * (1 - np.exp(-(t - 0.6 * t[-1]) / 300))
    medA = np.round((base + rng.normal(0, 0.03, n)) * 16) / 16
    medB = np.round((base - 1.5 + rng.normal(0, 0.03, n)) * 16) / 16
    f = lqi(P, ref=ref, dt=dt)
    u = [f(k, t[k], [float(medA[k]), float(medB[k])]) for k in range(n)]
    return {"ref_C": ref, "estado_inicial": [ref] * 4, "medA": medA.tolist(), "medB": medB.tolist(),
            "uA": [x[0] for x in u], "uB": [x[1] for x in u]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dt", type=float, nargs="*", default=[1.0, 5.0])
    for dt in ap.parse_args().dt:
        gerar(dt)


def gerar(dt):
    class A:
        pass
    a = A()
    a.dt = dt
    f = lqi(P, dt=a.dt)
    dados = {
        "_leia": "Gerado por 03-sistema-modelo/gerar_ganhos_lqi.py. Não editar à mão: refazer pelo script.",
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "dt_s": a.dt,
        "estados": ["T_bloco", "T_celula", "T_sonda", "d (entorno equivalente)"],
        "Ad": f.Ad.tolist(), "Bd": f.Bd[:, 0].tolist(), "L": f.L[:, 0].tolist(), "K": f.K[0].tolist(),
        "g_alimentacao": float(f.G_ext / P.pmax),
        "modelo": {"C_bloco_J_K": P.C_bloco, "C_cel_J_K": P.C_cel, "G_bloco_cel_W_K": P.G_bloco_cel,
                   "tau_sonda_s": P.tau_sonda, "G_ext_W_K": float(f.G_ext), "pmax_W": P.pmax},
        "pesos": {"Q": np.diag(f.Q).tolist(), "R": float(f.R[0, 0]), "Qn_kalman": np.diag(f.Qn).tolist(),
                  "Rn_kalman": float(f.Rn[0, 0])},
        "teste": vetor_de_teste(a.dt),
    }
    saida = PASTA_SAIDA / f"lqi_ganhos_{a.dt:g}s.json"
    saida.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    print("gravado", saida)
    print("K =", np.round(f.K[0], 4), " L =", np.round(f.L[:, 0], 4), " g =", round(dados["g_alimentacao"], 5))


if __name__ == "__main__":
    main()
