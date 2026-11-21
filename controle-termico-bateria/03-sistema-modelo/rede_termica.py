"""Etapa 3: rede térmica de nós concentrados do módulo de baterias no ensaio de termo-vácuo (sem convecção).

Balanço em cada nó livre i:
    C_i dT_i/dt = Σ_j G_ij (T_j − T_i) + Σ_k K_ik σ (T_k⁴ − T_i⁴) + Q_i
G_ij = condução e contato (W/K); K_ik = fator radiativo de duas superfícies cinzentas (m²):
    K = 1 / [ (1−ε₁)/(ε₁A₁) + 1/(A₁F₁₂) + (1−ε₂)/(ε₂A₂) ]
Nó prescrito: mesa T_m(t) (perfil do edital). Paredes da câmara (pendência P1), 3 cenários:
  "segue"    paredes prescritas, iguais à mesa (limite pessimista);
  "sala"     paredes prescritas, constantes na temperatura da sala (só a mesa é controlada);
  "isolada"  paredes isolantes: nó livre W que flutua, trocando radiação com a mesa e o satélite e pouco calor com a sala.
Fontes de calor: os 2 aquecedores (nos blocos) e a dissipação das placas (Q_int, no nó H). Sorvedouro: a mesa.

Nós livres: E estrutura metálica (trilhos, molduras, placa do ADCS); H interior não metálico (painéis de HDPE,
base em PCB/PPS, stack); P placa FR4 do módulo + espaçadores; A e B os blocos de alumínio (com resina e
resistor); CA e CB as 2 células de cada bloco (um nó por bloco). A sonda é um filtro de 1ª ordem sobre T do bloco.

Todos os valores de parâmetro estão em PARAM_NOMINAL com a origem; as faixas (FAIXAS) entram na sensibilidade.
Uso como biblioteca: simular(params, controlador, cenario_parede).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np

SIGMA = 5.670374419e-8
K0 = 273.15
PMAX = 13.8 ** 2 / 82.0          # W por bloco: barramento de 13,8 V em 82 Ω (2,32 W)


def k_rad(a1, e1, a2, e2, f12=1.0):
    """Fator radiativo (m²) entre duas superfícies cinzentas, difusas: Q = σ·K·(T1⁴ − T2⁴)."""
    return 1.0 / ((1 - e1) / (e1 * a1) + 1.0 / (a1 * f12) + (1 - e2) / (e2 * a2))


# ------------------------------------------------------------------------------------------ parâmetros
@dataclass
class Param:
    # capacidades térmicas (J/K)
    C_E: float = 400.0      # trilhos 4×8,5²×227 mm (177 g) + molduras e placa do ADCS: ~0,45 kg de Al 7075 × 900 (estimativa)
    C_H: float = 600.0      # 4 painéis de HDPE de 4 mm (~260 g × 2000) + base e stack (~100 J/K) (estimativa)
    C_P: float = 21.0       # FR4 de 13,3 g × 1100 + 4 espaçadores de latão (16 g × 380)
    C_bloco: float = 110.0  # 112 g de Al × 900 + resina (~7 g) + resistor
    C_cel: float = 84.0     # 2 células LFP 18650 de ~40 g × 1050 (típico; pendência P2)
    # condução e contato (W/K)
    G_mesa_E: float = 0.15  # pés dos 4 trilhos (4 × 72 mm²) apoiados, contato no vácuo h ~ 500 W/m²K → 0,15 (P8)
    G_E_H: float = 0.05     # painéis e base presos à estrutura (contato parafusado)
    G_E_P: float = 0.10     # torre → módulo: espaçadores de latão M3 (≈0,08 W/K cada) em série com juntas
    G_P_bloco: float = 0.05  # orelhas parafusadas do bloco na FR4 (parafuso de aço atravessando 1,5 mm de FR4)
    G_bloco_cel: float = 0.5  # 2 células no furo Ø18 com pasta parcial (P4); sem pasta, no vácuo, ~0,02
    # radiação
    eps_bloco: float = 0.8  # DP (ε = 0,8); Al usinado sem tratamento ficaria em 0,05–0,1
    eps_H: float = 0.9      # HDPE e FR4 (típico)
    eps_parede: float = 0.9  # paredes da câmara (desconhecido; P1)
    eps_mesa: float = 0.9    # face da mesa (pintada ~0,9; Al nu ~0,1); radiação mesa ↔ satélite e mesa ↔ parede
    A_bloco: float = 0.0103  # m², faces do bloco que não dão para a FR4 (CAD V7)
    A_H_ext: float = 0.079   # m², 4 painéis 100×173 mm (0,069) + fundo voltado para a mesa (0,010)
    A_H_fundo: float = 0.010  # m², parte de A_H_ext que vê a mesa (o resto vê as paredes)
    # parede isolante (cenário "isolada"): revestimento interno que flutua
    C_W: float = 2000.0      # J/K, revestimento interno da cuba (estimativa; uma cuba de aço inteira seria ~10 kJ/K)
    A_W: float = 0.9         # m², área interna da cuba (cilindro de ~40 cm × 50 cm, estimativa)
    A_mesa: float = 0.09     # m², face da mesa (30 × 30 cm, estimativa)
    G_isol: float = 0.2      # W/K, perda da parede para a sala pelo isolamento (U ≈ 0,2 W/m²K × 0,9 m²)
    # fontes internas
    Q_int: float = 0.0       # W, dissipação das placas (OBC, Pi, rádio...) no interior H; orçamento do EPS pendente
    # sonda e aquecedor
    tau_sonda: float = 30.0  # s; T02-04: 15–21 s na água (melhor contato); com pasta no canal, estimado
    ruido_sonda: float = 0.022  # °C, desvio-padrão (T02-03, T02-04); 0 desliga (comparação Python × JavaScript)
    pmax: float = PMAX
    T0: float = 20.0         # °C, tudo em equilíbrio com a sala no início
    T_parede: float | None = None  # °C, paredes constantes (cenário "sala"); None = T0
    ordem: str = "frio"      # o ensaio começa indo para o frio ou para o quente (TBC no edital)
    ciclos: int = 2          # número de ciclos (TBC no edital)

    def com(self, **kw):
        return replace(self, **kw)


PARAM_NOMINAL = Param()

# faixas (baixo, alto) para a sensibilidade: o que ainda não foi medido ou confirmado pela equipe
FAIXAS = {
    "G_mesa_E": (0.03, 1.0, "contato com a mesa (P8)"),
    "G_E_P": (0.03, 0.3, "torre → módulo"),
    "G_P_bloco": (0.01, 0.2, "orelhas do bloco na FR4"),
    "G_bloco_cel": (0.02, 5.0, "célula ↔ bloco (P4)"),
    "eps_bloco": (0.07, 0.8, "emissividade do bloco"),
    "C_E": (250.0, 800.0, "massa da estrutura"),
    "C_H": (300.0, 1000.0, "massa do interior"),
    "C_cel": (70.0, 100.0, "célula LFP (P2)"),
    "tau_sonda": (15.0, 300.0, "τ da sonda no canal"),
}


# ------------------------------------------------------------------------------------------ perfil
def pontos_perfil(p=None, rampa=1.5, patamar=30.0, lo=-10.0, hi=50.0):
    """Pontos (min, °C) da mesa pelo edital (8.1): −10 a +50 °C, ~1,5 °C/min, 30 min de patamar; ordem e ciclos TBC.
    ordem "frio", 2 ciclos: T0 → −10 → +50 → −10 → +50 → −10 → T0 (os ciclos dividem o patamar frio do meio).
    ordem "quente": o mesmo começando pelo quente. Volta a T0 no fim."""
    p = p or PARAM_NOMINAL
    a, b = (lo, hi) if p.ordem == "frio" else (hi, lo)
    alvos = [a] + [b, a] * p.ciclos
    pts, t, T = [(0.0, p.T0)], 0.0, p.T0
    for alvo in alvos:
        t += abs(alvo - T) / rampa
        pts.append((t, alvo))
        t += patamar
        pts.append((t, alvo))
        T = alvo
    t += abs(p.T0 - T) / rampa
    pts.append((t, p.T0))
    return pts


def perfil_mesa(t_s, p=None):
    m, v = zip(*pontos_perfil(p))
    return np.interp(np.asarray(t_s) / 60.0, m, v)


def duracao(p=None):
    return pontos_perfil(p)[-1][0] * 60.0


DURACAO = duracao()

CENARIOS_PAREDE = {
    "segue": "paredes seguem a mesa (pior caso frio e quente)",
    "sala": "paredes constantes na temperatura da sala (só a mesa é controlada; nominal desde 07/11)",
}


def t_parede(t_s, cenario, p=None):
    p = p or PARAM_NOMINAL
    if cenario == "segue":
        return perfil_mesa(t_s, p)
    tw = p.T0 if p.T_parede is None else p.T_parede
    return np.full_like(np.asarray(t_s, float), tw)


# ------------------------------------------------------------------------------------------ rede
NOS = ["E", "H", "P", "A", "B", "CA", "CB", "W"]
IE, IH, IP, IA, IB, ICA, ICB, IW = range(8)
N_NOS = 8


@dataclass
class Rede:
    p: Param
    C: np.ndarray = field(init=False)
    G: np.ndarray = field(init=False)        # matriz de condutâncias entre nós livres (simétrica)
    g_mesa: np.ndarray = field(init=False)   # condutância de cada nó livre para a mesa
    rad: list = field(init=False)            # (i, j, K) entre nós livres
    rad_mesa: list = field(init=False)       # (i, K) com a mesa
    rad_parede: list = field(init=False)     # (i, K) com as paredes

    def __post_init__(self):
        p = self.p
        self.C = np.array([p.C_E, p.C_H, p.C_P, p.C_bloco, p.C_bloco, p.C_cel, p.C_cel, p.C_W], float)
        G = np.zeros((N_NOS, N_NOS))

        def lig(i, j, g):
            G[i, j] += g
            G[j, i] += g
        lig(IE, IH, p.G_E_H)
        lig(IE, IP, p.G_E_P)
        lig(IP, IA, p.G_P_bloco)
        lig(IP, IB, p.G_P_bloco)
        lig(IA, ICA, p.G_bloco_cel)
        lig(IB, ICB, p.G_bloco_cel)
        self.G = G
        self.g_mesa = np.zeros(N_NOS)
        self.g_mesa[IE] = p.G_mesa_E
        # radiação: blocos ↔ interior (o módulo fica dentro da moldura e dos painéis: F = 1, interior bem maior)
        kb = k_rad(p.A_bloco, p.eps_bloco, 20 * p.A_bloco, p.eps_H)
        self.rad = [(IA, IH, kb), (IB, IH, kb)]
        # interior ↔ mesa (fundo) e interior ↔ paredes (painéis); superfícies grandes da câmara: F = 1
        self.rad_mesa = [(IH, k_rad(p.A_H_fundo, p.eps_H, 50 * p.A_H_fundo, p.eps_mesa))]
        a_lat = p.A_H_ext - p.A_H_fundo
        self.k_HW = k_rad(a_lat, p.eps_H, 50 * a_lat, p.eps_parede)       # interior ↔ parede
        self.k_WM = k_rad(p.A_mesa, p.eps_mesa, p.A_W, p.eps_parede)       # mesa ↔ parede (só no cenário isolada)

    def derivada(self, T, Tm, Tw, Qa, Qb, livre=False, T_sala=20.0):
        """dT/dt (K/s) dos nós, com mesa Tm (°C) e aquecedores Qa, Qb (W). Parede: prescrita em Tw (livre=False)
        ou nó livre W (livre=True), que troca radiação com a mesa e o interior e perde G_isol·(T_W − T_sala)."""
        if not livre:
            T = T.copy()
            T[IW] = Tw
        q = self.G @ T - self.G.sum(1) * T + self.g_mesa * (Tm - T)
        Tk = T + K0
        for i, j, k in self.rad:
            f = SIGMA * k * (Tk[j] ** 4 - Tk[i] ** 4)
            q[i] += f
            q[j] -= f
        for i, k in self.rad_mesa:
            q[i] += SIGMA * k * ((Tm + K0) ** 4 - Tk[i] ** 4)
        f = SIGMA * self.k_HW * (Tk[IW] ** 4 - Tk[IH] ** 4)
        q[IH] += f
        q[IW] -= f
        q[IA] += Qa
        q[IB] += Qb
        q[IH] += self.p.Q_int
        if livre:
            q[IW] += SIGMA * self.k_WM * ((Tm + K0) ** 4 - Tk[IW] ** 4) + self.p.G_isol * (T_sala - T[IW])
        else:
            q[IW] = 0.0
        return q / self.C

    def fluxos(self, T, Tm, Tw):
        """Calor que sai de cada bloco (W) por caminho, para o balanço: (para a FR4, por radiação, para as células)."""
        Tk = T + K0
        out = {}
        for nome, ib, ic in (("A", IA, ICA), ("B", IB, ICB)):
            cond = self.p.G_P_bloco * (T[ib] - T[IP])
            kb = self.rad[0][2]
            rad = SIGMA * kb * (Tk[ib] ** 4 - Tk[IH] ** 4)
            cel = self.p.G_bloco_cel * (T[ib] - T[ic])
            out[nome] = (cond, rad, cel)
        return out


# ------------------------------------------------------------------------------------------ sonda
class Sonda:
    """DS18B20 no canal do bloco: atraso de 1ª ordem, offset, ruído, quantização de 12 bits, conversão de 0,75 s."""

    def __init__(self, tau, offset=0.0, ruido=0.022, semente=0, T0=20.0):
        self.tau, self.offset, self.ruido = tau, offset, ruido
        self.rng = np.random.default_rng(semente)
        self.ts = T0
        self.pronta = T0

    def passo(self, T_bloco, dt):
        self.ts += (1 - math.exp(-dt / self.tau)) * (T_bloco - self.ts)

    def ler(self):
        """Leitura disponível agora (a da conversão anterior: atraso de ~0,75 s ≈ 1 amostra) e dispara a próxima."""
        v = self.pronta
        bruto = self.ts + self.offset + (self.rng.normal(0, self.ruido) if self.ruido > 0 else 0.0)
        self.pronta = round(bruto * 16) / 16
        return v


# ------------------------------------------------------------------------------------------ simulação
@dataclass
class Resultado:
    t: np.ndarray
    T: np.ndarray            # (n, 8): E, H, P, A, B, CA, CB, W (W = parede, prescrita ou livre)
    mesa: np.ndarray
    parede: np.ndarray
    Q: np.ndarray            # (n, 2) W
    med: np.ndarray          # (n, 2) leitura usada pelo controle de cada bloco
    params: Param
    nome: str = ""

    def energia_Wh(self, ini=0.0, fim=None):
        dt = self.t[1] - self.t[0]
        m = (self.t >= ini) & (self.t < (fim if fim is not None else self.t[-1] + 1))
        return self.Q[m].sum(0) * dt / 3600.0

    def metricas(self):
        cel = self.T[:, [ICA, ICB]]
        dt = self.t[1] - self.t[0]
        # potência média em 5 min: separa saturação real de pulsos do liga/desliga
        n5 = int(300 / dt)
        media5 = np.array([np.convolve(self.Q[:, b], np.ones(n5) / n5, mode="same") for b in range(2)]).T
        return {
            "energia_Wh": float(self.energia_Wh().sum()),
            "energia_bloco_Wh": [round(float(x), 3) for x in self.energia_Wh()],
            "pot_media5_max_W": float(media5.sum(1).max()),
            "cel_min_C": float(cel.min()),
            "cel_max_C": float(cel.max()),
            "bloco_max_C": float(self.T[:, [IA, IB]].max()),
            "min_cel_abaixo_17": float((cel.min(1) < 17.0).sum() * dt / 60),
            "min_cel_abaixo_0": float((cel.min(1) < 0.0).sum() * dt / 60),
            "min_media5_saturada": float((media5.max(1) >= 0.98 * self.params.pmax).sum() * dt / 60),
            "comutacoes": [int(x) for x in (np.abs(np.diff(self.Q, axis=0)) > 1e-9).sum(0)],
        }


def simular(p: Param = PARAM_NOMINAL, controlador=None, parede="segue", dt=1.0, dur=None,
            offsets=((0.0, 0.0), (0.0, 0.0)), taus=None, semente=1, nome=""):
    """Simula o ensaio. controlador: função (k, t, [medA, medB]) -> [uA, uB] em 0..1 (None = aquecedores desligados).

    Cada bloco tem 2 sondas (A: nº 1 e 2; B: nº 3 e 4); o controle recebe a MENOR leitura do bloco (SDD).
    RK4 com passo de dt/2 entre amostras; o comando fica constante entre amostras (ZOH de 1 s)."""
    rede = Rede(p)
    n = int((dur or duracao(p)) / dt) + 1
    t = np.arange(n) * dt
    mesa = perfil_mesa(t, p)
    par = t_parede(t, parede, p)
    livre = parede == "isolada"
    T_sala = p.T0 if p.T_parede is None else p.T_parede
    T = np.full(N_NOS, p.T0)
    T[IW] = par[0]
    taus = taus or ((p.tau_sonda, p.tau_sonda), (p.tau_sonda, p.tau_sonda))
    sondas = [[Sonda(taus[b][s], offsets[b][s], ruido=p.ruido_sonda, semente=semente * 10 + 2 * b + s, T0=p.T0)
               for s in range(2)] for b in range(2)]
    TT = np.empty((n, N_NOS))
    QQ = np.zeros((n, 2))
    MM = np.empty((n, 2))
    u = [0.0, 0.0]
    h = dt / 2
    for k in range(n):
        TT[k] = T
        med = [min(s.ler() for s in sondas[b]) for b in range(2)]
        MM[k] = med
        if controlador is not None:
            if getattr(controlador, "quer_estado", False):     # só o MPC oráculo (teto teórico, controle_mpc.py)
                saida = controlador(k, t[k], med, T)
            else:
                saida = controlador(k, t[k], med)
            u = [min(1.0, max(0.0, x)) for x in saida]
        Qa, Qb = u[0] * p.pmax, u[1] * p.pmax
        QQ[k] = (Qa, Qb)
        if k == n - 1:
            break
        for sub in range(2):
            tm0 = t[k] + sub * h
            Tm0, Tw0 = np.interp(tm0, t, mesa), np.interp(tm0, t, par)
            Tm1, Tw1 = np.interp(tm0 + h / 2, t, mesa), np.interp(tm0 + h / 2, t, par)
            Tm2, Tw2 = np.interp(tm0 + h, t, mesa), np.interp(tm0 + h, t, par)
            k1 = rede.derivada(T, Tm0, Tw0, Qa, Qb, livre, T_sala)
            k2 = rede.derivada(T + h / 2 * k1, Tm1, Tw1, Qa, Qb, livre, T_sala)
            k3 = rede.derivada(T + h / 2 * k2, Tm1, Tw1, Qa, Qb, livre, T_sala)
            k4 = rede.derivada(T + h * k3, Tm2, Tw2, Qa, Qb, livre, T_sala)
            T = T + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
            if not livre:
                T[IW] = Tw2
            for b, ib in ((0, IA), (1, IB)):
                for s in sondas[b]:
                    s.passo(T[ib], h)
    return Resultado(t, TT, mesa, TT[:, IW].copy(), QQ, MM, p, nome)


if __name__ == "__main__":
    import json
    r = simular()
    print("passivo, paredes seguem a mesa:", json.dumps(r.metricas(), ensure_ascii=False))
    r = simular(parede="sala")
    print("passivo, paredes a 20 °C:", json.dumps(r.metricas(), ensure_ascii=False))
