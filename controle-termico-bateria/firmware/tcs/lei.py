"""Lei de controle de UM bloco. Função pura: não lê sonda, não mexe em pino, não usa relógio.

Recebe as temperaturas calibradas das sondas do bloco e devolve a fração de potência (0 a 1).
É a mesma função no Pi, no teste local e em qualquer simulação.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# bits de situação (podem somar)
OK = 0
SEM_SONDA = 1        # nenhuma sonda válida no bloco → aquecedor desligado
CORTE = 2            # alguma sonda ≥ CORTE_C → aquecedor desligado
SONDA_FALHOU = 4     # uma das sondas do bloco falhou, a outra segue controlando
SEM_CALIBRACAO = 8   # alguma sonda do bloco está sem calibração (usa a leitura bruta)
LEITURA_LENTA = 16   # sem conversão simultânea: lendo uma a uma, período de reserva (marcado pelo controlador)
DISCORDA = 32        # modo degradado: a sonda que sobrou lê muito abaixo do outro bloco → aquecedor desligado
SEM_RESPOSTA = 64    # modo degradado: aqueceu forte e a sonda não subiu → aquecedor travado em 0 até reiniciar

NOMES = {SEM_SONDA: "sem sonda", CORTE: "corte por temperatura alta", SONDA_FALHOU: "uma sonda falhou",
         SEM_CALIBRACAO: "sonda sem calibração", LEITURA_LENTA: "leitura lenta (sem conversão simultânea)",
         DISCORDA: "sonda restante discorda do outro bloco", SEM_RESPOSTA: "aquecedor sem resposta (travado)"}


def descrever(situacao: int) -> str:
    return ", ".join(n for b, n in NOMES.items() if situacao & b) or "normal"


@dataclass
class Memoria:
    """O que a lei precisa lembrar entre um ciclo e outro."""
    ligado: bool = False
    integral: float = 0.0     # PI e LQI: soma do erro × tempo (°C·s)
    x: list | None = None     # LQI: estado estimado [T_bloco, T_célula, T_sonda, d]; None = (re)iniciar pela leitura
    u_ant: float = 0.0        # LQI: comando aplicado no ciclo anterior (entra na predição do observador)
    t_forte_s: float = 0.0    # modo degradado: há quanto tempo o duty está ≥ SEM_RESPOSTA_DUTY
    temp_inicio_forte: float = 0.0   # leitura no começo dessa janela
    travado: bool = False     # SEM_RESPOSTA disparou: aquecedor em 0 até o programa reiniciar
    cortado: bool = False     # corte de temperatura alta ativo (entra em CORTE_C, sai abaixo de CORTE_LIBERA_C)


# ------------------------------------------------------------------------------------------------ LQI
# Ganhos calculados no PC (03-sistema-modelo/gerar_ganhos_lqi.py) para o período do laço; aqui só há contas de 4 × 4.
_GANHOS = {}


def ganhos_lqi(arquivo) -> dict:
    arquivo = Path(arquivo)
    if arquivo not in _GANHOS:
        g = json.loads(arquivo.read_text(encoding="utf-8"))
        for k in ("Ad", "Bd", "L", "K", "g_alimentacao", "dt_s"):
            if k not in g:
                raise ValueError(f"{arquivo.name}: falta '{k}'")
        _GANHOS[arquivo] = g
    return _GANHOS[arquivo]


def passo_lqi(medida: float, mem: Memoria, ref: float, g: dict) -> float:
    """Um passo do LQI com observador, igual a 03-sistema-modelo/controle.lqi (conferido por teste_local/testar_lqi.py).
    1) predição com o comando anterior; 2) correção com a leitura; 3) u = u0(d estimado) − K·[x − ref; ∫erro],
    com integração condicional (anti-windup); 4) saturação em 0..1."""
    Ad, Bd, L, K, gff, dt = g["Ad"], g["Bd"], g["L"], g["K"], g["g_alimentacao"], g["dt_s"]
    if mem.x is None:                                   # partida ou volta de falha: equilíbrio sem aquecer
        mem.x, mem.u_ant, mem.integral = [medida] * 4, 0.0, 0.0
    x = [sum(Ad[i][j] * mem.x[j] for j in range(4)) + Bd[i] * mem.u_ant for i in range(4)]
    inov = medida - x[2]
    x = [x[i] + L[i] * inov for i in range(4)]
    mem.x = x
    e = ref - medida
    u0 = gff * (ref - x[3])

    def comando(integ):
        xa = (x[0] - ref, x[1] - ref, x[2] - ref, integ)
        return u0 - sum(K[i] * xa[i] for i in range(4))

    v = comando(mem.integral)
    if not ((v >= 1.0 and e > 0) or (v <= 0.0 and e < 0)):
        mem.integral += e * dt
        v = comando(mem.integral)
    u = min(1.0, max(0.0, v))
    mem.u_ant = u
    return u


def decidir(temps: list[float | None], mem: Memoria, dt: float, cfg,
            vizinho: float | None = None) -> tuple[float, int]:
    """temps: leituras calibradas das sondas do bloco (None = falhou). dt: segundos desde o ciclo anterior.
    vizinho: média das sondas do OUTRO bloco, só quando as duas dele estão boas (senão None).
    Controla pela sonda MAIS FRIA do bloco; corta pela MAIS QUENTE. Com uma sonda só (modo degradado), desconfia dela:
    corta se ela ler muito abaixo do vizinho e trava se o aquecedor ficar forte sem ela subir."""
    validas = [t for t in temps if t is not None]
    situacao = OK if len(validas) == len(temps) else SONDA_FALHOU
    if not validas:
        mem.ligado, mem.integral, mem.x = False, 0.0, None
        mem.t_forte_s = 0.0
        return 0.0, SEM_SONDA | (SEM_RESPOSTA if mem.travado else 0)
    medida = min(validas)
    integral_antes = mem.integral
    u, situacao = _lei(medida, mem, dt, cfg, situacao)

    def zerar(bit):
        # Se a lei queria aquecer, é falha: zera, marca e não deixa o integrador crescer. Se a lei já dava 0 (mesa
        # quente), não há o que marcar.
        nonlocal u, situacao
        if u > 0:
            situacao |= bit
            mem.integral = integral_antes
        u = 0.0
        mem.ligado = False
        mem.u_ant = 0.0                 # o observador do LQI precisa saber o que foi aplicado de fato

    # corte pela sonda MAIS QUENTE (ex.: a outra saiu do bloco e lê frio), com histerese: entra em CORTE_C e só sai
    # abaixo de CORTE_LIBERA_C, para não alternar 0/100 % a cada ciclo com a leitura oscilando em torno do limite
    quente = max(validas)
    if quente >= cfg.CORTE_C:
        mem.cortado = True
    elif quente < cfg.CORTE_LIBERA_C:
        mem.cortado = False
    if mem.cortado:
        zerar(CORTE)

    degradado = len(temps) > 1 and len(validas) == 1
    if mem.travado:
        zerar(SEM_RESPOSTA)
        situacao |= SEM_RESPOSTA        # marca sempre, mesmo quando a lei já dava 0
    elif degradado:
        if vizinho is not None and medida < vizinho - cfg.DEGRADADO_DIF_C:
            zerar(DISCORDA)
        if u >= cfg.SEM_RESPOSTA_DUTY:
            if mem.t_forte_s == 0.0:
                mem.temp_inicio_forte = medida
            mem.t_forte_s += dt
            if mem.t_forte_s >= cfg.SEM_RESPOSTA_S:
                if medida - mem.temp_inicio_forte < cfg.SEM_RESPOSTA_SUBIDA_C:
                    mem.travado = True
                    zerar(SEM_RESPOSTA)
                mem.t_forte_s = 0.0     # subiu: abre uma janela nova e continua conferindo
        else:
            mem.t_forte_s = 0.0
    else:
        mem.t_forte_s = 0.0
    return u, situacao


def _lei(medida: float, mem: Memoria, dt: float, cfg, situacao: int) -> tuple[float, int]:
    erro = cfg.REF_C - medida

    if cfg.LEI == "liga_desliga":
        if medida < cfg.REF_C - cfg.HIST_C:
            mem.ligado = True
        elif medida > cfg.REF_C + cfg.HIST_C:
            mem.ligado = False
        return (1.0 if mem.ligado else 0.0), situacao

    if cfg.LEI == "pi":
        u = cfg.KP * (erro + mem.integral / cfg.TI_S)
        # anti-windup: só integra se a saída não está saturada, ou se o erro a tira da saturação
        if not ((u >= 1.0 and erro > 0) or (u <= 0.0 and erro < 0)):
            mem.integral += erro * dt
        u = cfg.KP * (erro + mem.integral / cfg.TI_S)
        u = min(1.0, max(0.0, u))
        mem.ligado = u > 0
        return u, situacao

    if cfg.LEI == "lqi":
        g = ganhos_lqi(cfg.LQI_GANHOS)
        if dt > 3 * g["dt_s"]:                          # ciclo travou (ex.: barramento lento): reinicia o observador
            mem.x = None
        u = passo_lqi(medida, mem, cfg.REF_C, g)
        mem.ligado = u > 0
        return u, situacao

    raise ValueError(f"lei desconhecida: {cfg.LEI}")
