"""Etapa 3: leis de controle dos aquecedores, uma malha por bloco, amostrada a 1 Hz.

Cada controlador é uma fábrica que devolve uma função (k, t, [medA, medB]) -> [uA, uB] (fração de 0 a 1 da potência
máxima do bloco). A medida de cada bloco é a menor leitura válida das suas 2 sondas (SDD).

- liga_desliga(h): liga abaixo de ref − h, desliga acima de ref + h; h = 0 vira um PWM lento de 1 Hz.
- pi(kp, ti): PI com integração condicional (anti-windup), saída em fração da potência máxima.
- lqi(...): LQI do DP (seção 5.3) sobre o modelo linear de 1 bloco (bloco, células, sonda), com observador.
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import expm, solve_discrete_are

from rede_termica import PARAM_NOMINAL, Param

REF = 20.0


def liga_desliga(h=0.0, ref=REF):
    estado = [0.0, 0.0]

    def f(k, t, med):
        for b in range(2):
            if med[b] < ref - h:
                estado[b] = 1.0
            elif med[b] > ref + h:
                estado[b] = 0.0
            elif h == 0.0:
                estado[b] = 1.0 if med[b] < ref else 0.0
        return list(estado)
    f.nome = f"Liga/desliga, h = ±{h:g} °C"
    return f


def pi(kp=0.5, ti=600.0, ref=REF, dt=1.0):
    """kp em fração da potência por K (0,5 → 1,16 W/K); ti em s. Integração condicional: o integrador
    só anda se a saída não estiver saturada ou se o erro tirar a saída da saturação."""
    integ = [0.0, 0.0]

    def f(k, t, med):
        u = []
        for b in range(2):
            e = ref - med[b]
            v = kp * (e + integ[b] / ti)
            sat_alto, sat_baixo = v >= 1.0, v <= 0.0
            if not ((sat_alto and e > 0) or (sat_baixo and e < 0)):
                integ[b] += e * dt
            u.append(min(1.0, max(0.0, kp * (e + integ[b] / ti))))
        return u
    f.nome = f"PI, Kp = {kp * PARAM_NOMINAL.pmax:.2f} W/K, Ti = {ti / 60:g} min"
    return f


def modelo_bloco(p: Param, G_ext=None):
    """Modelo linear de 1 bloco para o projeto do LQI: estados x = [T_bloco, T_cel, T_sonda] (°C), entrada u
    (fração de PMAX), perturbação d = T do entorno. G_ext = condutância equivalente bloco → entorno (W/K),
    que inclui a FR4 em série e a radiação linearizada a 0 °C."""
    if G_ext is None:
        h_r = 4 * 5.670374419e-8 * 273.15 ** 3          # W/m²K por unidade de ε efetivo
        e_ef = 1 / (1 / p.eps_bloco + 1 / 20 * (1 / p.eps_H - 1))
        G_ext = p.G_P_bloco + e_ef * p.A_bloco * h_r
    Cb, Cc, gbc, ts = p.C_bloco, p.C_cel, p.G_bloco_cel, p.tau_sonda
    A = np.array([[-(G_ext + gbc) / Cb, gbc / Cb, 0.0],
                  [gbc / Cc, -gbc / Cc, 0.0],
                  [1 / ts, 0.0, -1 / ts]])
    B = np.array([[p.pmax / Cb], [0.0], [0.0]])
    E = np.array([[G_ext / Cb], [0.0], [0.0]])
    C = np.array([[0.0, 0.0, 1.0]])
    return A, B, E, C, G_ext


def discretizar(A, B, dt):
    n, m = A.shape[0], B.shape[1]
    M = np.zeros((n + m, n + m))
    M[:n, :n], M[:n, n:] = A, B
    Md = expm(M * dt)
    return Md[:n, :n], Md[:n, n:]


def lqi(p: Param = PARAM_NOMINAL, q_int=1e-3, q_T=1.0, r_u=10.0, ref=REF, dt=1.0, d0=-10.0):
    """LQI discreto como no DP (seção 5.3): x_a = [x; ∫(ref − y)], u = u0 − K x_a, com integração condicional.
    O DP usa Q = diag(1000, 0,01, 100), R = 2e-6 em W; aqui os pesos estão em fração de PMAX e °C.
    Observador de Luenberger/Kalman em regime (ruído de processo 1e-4, de medida 0,02²) sobre [bloco, célula, sonda]
    com a perturbação d tratada como estado constante (estimada), para não depender de T_entorno medido."""
    A, B, E, C, G_ext = modelo_bloco(p)
    # estados do observador: [T_bloco, T_cel, T_sonda, d]
    Ao = np.zeros((4, 4))
    Ao[:3, :3], Ao[:3, 3:] = A, E
    Bo = np.vstack([B, [[0.0]]])
    Ad, Bd = discretizar(Ao, Bo, dt)
    Co = np.array([[0.0, 0.0, 1.0, 0.0]])
    Qn = np.diag([1e-4, 1e-5, 1e-6, 1e-3]) * dt          # ruído de processo por passo cresce com o passo (16/11)
    Rn = np.array([[0.02 ** 2]])
    Pk = solve_discrete_are(Ad.T, Co.T, Qn, Rn)
    L = Pk @ Co.T @ np.linalg.inv(Co @ Pk @ Co.T + Rn)        # ganho do filtro (forma de atualização)
    # controlador LQI sobre [bloco, célula, sonda, integral do erro]
    Ad3, Bd3 = discretizar(A, B, dt)
    Aa = np.zeros((4, 4))
    Aa[:3, :3] = Ad3
    Aa[3, :3] = -C[0] * dt
    Aa[3, 3] = 1.0
    Ba = np.vstack([Bd3, [[0.0]]])
    Q = np.diag([q_T, 0.0, 0.0, q_int])
    R = np.array([[r_u]])
    P = solve_discrete_are(Aa, Ba, Q, R)
    K = np.linalg.inv(R + Ba.T @ P @ Ba) @ Ba.T @ P @ Aa
    est = [np.array([ref, ref, ref, ref]) for _ in range(2)]
    integ = [0.0, 0.0]
    u_ant = [0.0, 0.0]

    def f(k, t, med):
        u = []
        for b in range(2):
            x = est[b]
            # predição com o comando anterior e atualização com a medida
            x = Ad @ x + (Bd[:, 0] * u_ant[b])
            x = x + (L[:, 0] * (med[b] - x[2]))
            est[b] = x
            e = ref - med[b]
            # comando em regime para segurar ref com a perturbação estimada: G_ext (ref − d) / PMAX
            u0 = G_ext * (ref - x[3]) / p.pmax
            xa = np.array([x[0] - ref, x[1] - ref, x[2] - ref, integ[b]])
            v = u0 - float(K[0] @ xa)
            if not ((v >= 1.0 and e > 0) or (v <= 0.0 and e < 0)):
                integ[b] += e * dt
                xa[3] = integ[b]
                v = u0 - float(K[0] @ xa)
            ub = min(1.0, max(0.0, v))
            u.append(ub)
            u_ant[b] = ub
        return u
    f.nome = "LQI com observador (DP, seção 5.3)"
    f.K, f.L, f.G_ext, f.Ad, f.Bd = K, L, G_ext, Ad, Bd
    f.dt, f.Qn, f.Rn, f.Q, f.R = dt, Qn, Rn, Q, R
    return f
