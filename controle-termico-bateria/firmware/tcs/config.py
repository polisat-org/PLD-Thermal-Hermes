"""Configuração do controle térmico das baterias. Tudo que se ajusta fica aqui.

Este pacote (tcs) é o programa que vai rodar no Raspberry Pi. Usa só a biblioteca padrão do Python
(no Pi Zero não há numpy), mais o pigpio para o PWM no Pi de verdade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PASTA = Path(__file__).resolve().parent.parent      # onde ficam sondas.json e calibracao.json


@dataclass
class Config:
    # --- o que queremos ---
    REF_C: float = 20.0          # temperatura desejada no bloco (°C)
    LEI: str = "lqi"             # "lqi" (escolhido, D03-02), "pi" ou "liga_desliga"
    HIST_C: float = 0.5          # liga/desliga: liga abaixo de REF − HIST, desliga acima de REF + HIST
    KP: float = 0.5              # PI: fração da potência por °C de erro (0,5 → 1,16 W/°C)
    TI_S: float = 600.0          # PI: tempo integral (s)

    # --- proteção ---
    CORTE_C: float = 30.0        # sonda MAIS QUENTE do bloco ≥ isto → aquecedor do bloco em 0 (16/11). Proteção contra
    #                              falha (sonda solta lendo frio, erro de programa): o controle normal não passa de ~24 °C.
    #                              Na fase quente da mesa o bloco passa disso sozinho, com o aquecedor já desligado
    CORTE_LIBERA_C: float = 29.0 # histerese (20/11): depois de cortar, só libera quando a sonda mais quente cair abaixo
    #                              disto. Sem ela, no limite o aquecedor alternava 0/100 % a cada ciclo (degraus de 1/16 °C)
    T_MIN_VALIDA_C: float = -55.0
    T_MAX_VALIDA_C: float = 125.0
    # Modo degradado (o bloco perdeu uma sonda e controla pela que sobrou; 20/11). O corte acima não protege se a sonda
    # que sobrou sair do canal e ler frio: a lei aqueceria a 100 % sem parar (~65 °C na sala). Duas regras, SÓ neste modo:
    DEGRADADO_DIF_C: float = 5.0         # a sonda que sobrou lê mais que isto abaixo do outro bloco (2 sondas boas) → 0
    SEM_RESPOSTA_DUTY: float = 0.9       # duty ≥ isto por SEM_RESPOSTA_S sem a sonda subir SEM_RESPOSTA_SUBIDA_C →
    SEM_RESPOSTA_S: float = 600.0        #   aquecedor do bloco travado em 0 até reiniciar o programa
    SEM_RESPOSTA_SUBIDA_C: float = 0.5   #   (a 100 % o bloco sobe vários °C em 10 min com a cuba a −10 °C)

    # --- hardware ---
    # bloco → números das sondas (padrão da equipe, sondas.json) e pino BCM do MOSFET.
    # Pinos BCM 18 e 19 (PWM por hardware, canais 0 e 1), um resistor de 82 Ω por bloco: confirmados pela equipe em 13/11 (P3).
    BLOCOS: dict = field(default_factory=lambda: {"A": {"sondas": [1, 2], "gpio": 18},
                                                  "B": {"sondas": [3, 4], "gpio": 19}})
    PWM_HZ: int = 1000
    W1_RAIZ: str = "/sys/bus/w1/devices"
    PERIODO_S: float = 1.0       # período FIXO do laço (s), R06: as 4 sondas convertem juntas (therm_bulk_read, ~0,75 s)
    PERIODO_SEM_BULK_S: float = 5.0  # se a conversão simultânea não estiver disponível (sem root): uma a uma, ~3 s
    #                              O LQI tem ganhos para cada período (tcs/lqi_ganhos_<T>s.json, gerar_ganhos_lqi.py)

    # --- arquivos ---
    SONDAS_JSON: Path = PASTA / "sondas.json"
    CALIB_JSON: Path = PASTA / "calibracao.json"

    @property
    def LQI_GANHOS(self) -> Path:
        return Path(__file__).resolve().parent / f"lqi_ganhos_{self.PERIODO_S:g}s.json"
    PASTA_LOG: Path = PASTA / "logs"

    @property
    def PMAX_W(self) -> float:   # só para relatório: 13,8 V em 82 Ω
        return 13.8 ** 2 / 82.0
