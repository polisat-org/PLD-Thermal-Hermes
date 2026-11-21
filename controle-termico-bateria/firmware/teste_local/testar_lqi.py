"""Confere o controle do firmware (tcs, só biblioteca padrão) antes de levar ao Pi.

    cd controle-termico-bateria/firmware
    python -I teste_local/testar_lqi.py          # ~10 min

1. Vetores de teste (1 s e 5 s): as mesmas leituras entram no firmware e na referência da simulação (controle.lqi,
   gravada em tcs/lqi_ganhos_<T>s.json pelo gerar_ganhos_lqi.py); os comandos têm de bater (diferença < 1e-9).
2. Ensaio inteiro na bancada local (código do Pi + modelo de 8 nós), laço de 1 s com conversão simultânea, paredes na
   sala e seguindo a mesa, contra a simulação da etapa 3 (mesmo período). Aqui a sonda 4 entra SEM erro de fábrica, para
   comparar código com código (o efeito do erro dela é o achado A03-01).
3. Reserva de 5 s (Pi sem conversão simultânea): o mesmo ensaio, paredes na sala.
4. Falhas de leitura (paredes seguem a mesa, sonda 4 com o erro estimado): sonda 1 solta de 25 a 35 min; as 2 sondas
   do bloco B soltas de 40 a 45 min. Célula > 0 °C (R01) e duty sempre em 0..1.
5. Corte (R08, 30 °C): de 25 a 60 min a sonda 2 sai do canal e lê 10 °C. O bloco A só tem a sonda 1 de verdade; a lei
   aquece sem parar e o corte, pela sonda MAIS QUENTE, tem de segurar o bloco perto de 30 °C. Com histerese (entra em
   30 °C, libera abaixo de 29 °C, 20/11) o corte tem de trocar de estado menos vezes que sem ela (5b).
6. Conversão simultânea pára aos 30 min (20/11): o controlador passa a 5 s com os ganhos de 5 s e segue controlando.
   E, à parte, conversão que estoura o tempo devolve False.
7. Falha dupla com vizinho (20/11): sonda 1 solta aos 25 min, sonda 2 sai do canal (lê 10 °C) aos 30 min. O bloco A fica
   só com uma sonda que mente; tem de cortar por "discorda do outro bloco" e o bloco não pode esquentar.
8. Falha dupla sem vizinho: igual, mas o bloco B já perdeu a sonda 3 (sem referência). Tem de travar por "sem resposta"
   (10 min a ≥ 90 % sem a sonda subir) com o bloco abaixo de 45 °C.
9. Alarme falso: sonda 1 solta dos 5 min ao fim, com as paredes seguindo a mesa (−10 a +50 °C). O bloco A roda o ensaio
   inteiro com uma sonda boa: nenhum corte do modo degradado, célula > 0 °C.
Resultado em teste_local/logs/testar_lqi.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FIRMWARE = AQUI.parent
sys.path.insert(0, str(FIRMWARE))
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(FIRMWARE.parent / "03-sistema-modelo"))

import planta  # noqa: E402
from bancada import Bancada, resumo  # noqa: E402
from tcs import lei  # noqa: E402
from tcs.config import Config  # noqa: E402
from tcs.sondas import Sondas  # noqa: E402


def teste_vetor(periodo):
    cfg = Config()
    cfg.PERIODO_S = periodo
    g = lei.ganhos_lqi(cfg.LQI_GANHOS)
    t = g["teste"]
    mems = {b: lei.Memoria(x=list(t["estado_inicial"])) for b in "AB"}
    pior = 0.0
    for k in range(len(t["medA"])):
        for b, med, ref_u in (("A", t["medA"][k], t["uA"][k]), ("B", t["medB"][k], t["uB"][k])):
            u = lei.passo_lqi(med, mems[b], t["ref_C"], g)
            pior = max(pior, abs(u - ref_u))
    return {"periodo_s": periodo, "dt_no_arquivo_s": g["dt_s"], "passos": len(t["medA"]), "maior_diferenca": pior,
            "ok": pior < 1e-9 and g["dt_s"] == periodo}


def ensaio(cenario, falhas=(), bulk=True, erro_sonda4=None, nome=None, corte_libera=None):
    if erro_sonda4 is not None:
        planta.ERRO_SONDA4_ESTIMADO = erro_sonda4
    try:
        b = Bancada(cenario, ref=20.0, lei="lqi", log=True, bulk=bulk,
                    raiz_w1=AQUI / "w1-lote" / (nome or f"{cenario}-lqi"))
    finally:
        planta.ERRO_SONDA4_ESTIMADO = -0.30
    if corte_libera is not None:                    # ex.: 30.0 = sem histerese (como antes de 20/11), para comparar
        b.cfg.CORTE_LIBERA_C = corte_libera
    duties, cortes = [], 0
    marcas = {"discorda": 0, "sem resposta": 0, "leitura lenta": 0}
    primeiro = {}
    trocas_corte, corte_ant = 0, [False, False]
    while not b.planta.acabou:
        t_min = b.planta.t / 60
        for t_falha, n, tipo in falhas:
            if t_min >= t_falha > t_min - b.periodo / 60:
                if tipo == "bulk_morre":            # o arquivo do kernel some: escrever nele dá erro
                    arq = b.planta.raiz / "w1_bus_master1" / "therm_bulk_read"
                    arq.unlink()
                    arq.mkdir()
                else:
                    b.planta.falha[n] = tipo
        a = b.passo()
        duties += a["duty"]
        cortes += sum("corte" in s for s in a["situacao"])
        agora = ["corte" in s for s in a["situacao"]]
        trocas_corte += sum(1 for x, y in zip(agora, corte_ant) if x != y)
        corte_ant = agora
        for m in marcas:
            k = sum(m in s for s in a["situacao"])
            marcas[m] += k
            if k and m not in primeiro:
                primeiro[m] = round(a["t_min"], 1)
    b.fechar()
    r = resumo(b.amostras, b.cfg.PMAX_W)
    r.update(periodo_s=b.periodo, duty_fora_0_1=sum(1 for d in duties if not (0.0 <= d <= 1.0)),
             ciclos_com_corte=cortes, trocas_corte=trocas_corte, usou_bulk=b.ctrl.usar_bulk,
             ciclos_discorda=marcas["discorda"], ciclos_sem_resposta=marcas["sem resposta"],
             ciclos_leitura_lenta=marcas["leitura lenta"], primeira_marca_min=primeiro,
             bloco_A_max_C=round(max(a["bloco"][0] for a in b.amostras), 2))
    return r


def teste_estouro_conversao():
    """converter_todas tem de devolver False quando a conversão não termina em 1,5 s (antes devolvia True)."""
    class Preso:                         # imita o therm_bulk_read convertendo para sempre
        def write_text(self, _):
            pass

        def read_text(self):
            return "-1" + chr(10)
    s = Sondas.__new__(Sondas)
    s.bulk = Preso()
    return {"devolveu": s.converter_todas(), "ok": s.converter_todas() is False and s.bulk is not None}


def referencia_etapa3(parede, dt):
    from controle import lqi
    from rede_termica import PARAM_NOMINAL, simular
    m = simular(PARAM_NOMINAL, lqi(PARAM_NOMINAL, dt=dt), parede=parede, dt=dt).metricas()
    return {"energia_Wh": round(m["energia_Wh"], 3), "celula_min_C": round(m["cel_min_C"], 2),
            "celula_max_C": round(m["cel_max_C"], 2)}


def main():
    res = {"vetor_1s": teste_vetor(1.0), "vetor_5s": teste_vetor(5.0)}
    print("1) vetores:", res["vetor_1s"], res["vetor_5s"], flush=True)
    for cen in ("sala", "segue"):
        res[f"bancada_1s_{cen}"] = ensaio(cen, erro_sonda4=0.0, nome=f"{cen}-1s")
        res[f"etapa3_1s_{cen}"] = referencia_etapa3(cen, 1.0)
        print(f"2) {cen}: bancada {res[f'bancada_1s_{cen}']} | etapa 3 {res[f'etapa3_1s_{cen}']}", flush=True)
    res["bancada_5s_sala"] = ensaio("sala", bulk=False, erro_sonda4=0.0, nome="sala-5s")
    print("3) reserva 5 s:", res["bancada_5s_sala"], flush=True)
    falhas = [(25.0, 1, "desconectada"), (35.0, 1, ""), (40.0, 3, "desconectada"), (40.0, 4, "desconectada"),
              (45.0, 3, ""), (45.0, 4, "")]
    res["falhas_leitura"] = ensaio("segue", falhas, nome="segue-falhas")
    print("4) falhas de leitura:", res["falhas_leitura"], flush=True)
    res["corte_sonda_fria"] = ensaio("sala", [(25.0, 2, "fria"), (60.0, 2, "")], nome="sala-corte")
    print("5) corte (sonda 2 fora do canal):", res["corte_sonda_fria"], flush=True)
    res["corte_sem_histerese"] = ensaio("sala", [(25.0, 2, "fria"), (60.0, 2, "")], nome="sala-corte-sem-hist",
                                        corte_libera=30.0)
    print("5b) o mesmo, sem histerese (como antes de 20/11):", res["corte_sem_histerese"], flush=True)
    res["estouro_conversao"] = teste_estouro_conversao()
    print("6a) estouro da conversão:", res["estouro_conversao"], flush=True)
    res["bulk_para_30min"] = ensaio("sala", [(30.0, None, "bulk_morre")], erro_sonda4=0.0, nome="sala-bulk-para")
    print("6b) conversão simultânea pára aos 30 min:", res["bulk_para_30min"], flush=True)
    dupla = [(25.0, 1, "desconectada"), (30.0, 2, "fria")]
    res["dupla_com_vizinho"] = ensaio("sala", dupla, nome="sala-dupla")
    print("7) falha dupla, com vizinho:", res["dupla_com_vizinho"], flush=True)
    res["dupla_sem_vizinho"] = ensaio("sala", [(20.0, 3, "desconectada")] + dupla, nome="sala-dupla-sem-vizinho")
    print("8) falha dupla, sem vizinho:", res["dupla_sem_vizinho"], flush=True)
    res["degradado_ensaio_todo"] = ensaio("segue", [(5.0, 1, "desconectada")], nome="segue-degradado")
    print("9) uma sonda só no ensaio todo:", res["degradado_ensaio_todo"], flush=True)
    c = res["corte_sonda_fria"]
    bp, d1, d2, d3 = (res["bulk_para_30min"], res["dupla_com_vizinho"], res["dupla_sem_vizinho"],
                      res["degradado_ensaio_todo"])
    ok_novos = {
        "5b_histerese": c["trocas_corte"] < res["corte_sem_histerese"]["trocas_corte"] and c["bloco_max_C"] < 31.5,
        "6a_estouro": res["estouro_conversao"]["ok"],
        "6b_bulk_para": (not bp["usou_bulk"] and bp["periodo_s"] == 5.0 and bp["ciclos_leitura_lenta"] > 0
                         and bp["celula_min_C"] > 17 and bp["duty_fora_0_1"] == 0),
        "7_com_vizinho": d1["ciclos_discorda"] > 0 and d1["bloco_A_max_C"] < 25.0,
        "8_sem_vizinho": d2["ciclos_sem_resposta"] > 0 and d2["bloco_A_max_C"] < 45.0,
        "9_sem_alarme_falso": (d3["ciclos_discorda"] == 0 and d3["ciclos_sem_resposta"] == 0
                               and d3["celula_min_C"] > 0 and d3["duty_fora_0_1"] == 0),
    }
    res["conferencias_20261120"] = ok_novos
    print("conferências novas:", ok_novos, flush=True)
    ok = (all(ok_novos.values()) and res["vetor_1s"]["ok"] and res["vetor_5s"]["ok"]
          and res["bancada_1s_sala"]["usou_bulk"] and not res["bancada_5s_sala"]["usou_bulk"]
          and abs(res["bancada_1s_sala"]["energia_Wh"] / res["etapa3_1s_sala"]["energia_Wh"] - 1) < 0.02
          and res["falhas_leitura"]["celula_min_C"] > 0 and res["falhas_leitura"]["duty_fora_0_1"] == 0
          and c["ciclos_com_corte"] > 0 and c["bloco_max_C"] < 31.5)
    res["aprovado"] = bool(ok)
    (AQUI / "logs").mkdir(exist_ok=True)
    (AQUI / "logs" / "testar_lqi.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("APROVADO" if ok else "REPROVADO")


if __name__ == "__main__":
    main()
