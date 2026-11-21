"""Ciclo do controle: lê as 4 sondas → decide cada bloco → ajusta os 2 PWMs → grava uma linha de log."""
from __future__ import annotations

import csv
import os
import socket
import time
from datetime import datetime

from . import lei


def avisar_systemd(msg: str):
    """sd_notify só com a biblioteca padrão. Fora do systemd (NOTIFY_SOCKET ausente) não faz nada.
    O serviço (servico/tcs.service) usa WatchdogSec: se o laço travar e parar de mandar WATCHDOG=1, o systemd mata
    o processo e o ExecStopPost zera os dois PWMs (R08)."""
    end = os.environ.get("NOTIFY_SOCKET")
    if not end:
        return
    if end.startswith("@"):
        end = "\0" + end[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sk:
            sk.connect(end)
            sk.sendall(msg.encode())
    except OSError:
        pass


class Controlador:
    def __init__(self, cfg, sondas, pwm, log_csv=None):
        self.cfg, self.sondas, self.pwm = cfg, sondas, pwm
        self.mem = {b: lei.Memoria() for b in cfg.BLOCOS}
        self.usar_bulk = getattr(sondas, "bulk", None) is not None
        self.falhas_bulk = 0            # conversões simultâneas que falharam seguidas
        self.leitura_lenta = False      # passou para o período de reserva no meio do ensaio
        self.ultimo = None
        self.ciclos = 0
        self._log = None
        self._ultimo_fsync = None
        if log_csv:
            log_csv.parent.mkdir(parents=True, exist_ok=True)
            self._arq = open(log_csv, "w", newline="", encoding="utf-8")
            self._log = csv.writer(self._arq)
            cab = ["t_s", "hora"]       # t_s: relógio do laço (monotônico); hora: relógio de parede, para alinhar com a câmara
            for b, d in cfg.BLOCOS.items():
                cab += [f"sonda{n}_bruta" for n in d["sondas"]] + [f"sonda{n}" for n in d["sondas"]]
                cab += [f"duty_{b}", f"situacao_{b}"]
            self._log.writerow(cab)

    def ciclo(self, agora: float | None = None) -> dict:
        """Um ciclo completo. agora: relógio em s (no Pi, time.monotonic; no teste, o relógio simulado)."""
        agora = time.monotonic() if agora is None else agora
        dt = 0.0 if self.ultimo is None else agora - self.ultimo
        self.ultimo = agora
        saida = {"t": agora, "sondas": {}, "blocos": {}}
        linha = [round(agora, 2), datetime.now().astimezone().isoformat(timespec="milliseconds")]
        if self.usar_bulk:
            if self.sondas.converter_todas():
                self.falhas_bulk = 0
            else:
                self.falhas_bulk += 1
                if self.sondas.bulk is None or self.falhas_bulk >= 3:
                    self._passar_para_leitura_lenta()
        todas = {b: [self.sondas.ler(n) for n in d["sondas"]] for b, d in self.cfg.BLOCOS.items()}
        for b, d in self.cfg.BLOCOS.items():
            leituras = todas[b]
            for n, l in zip(d["sondas"], leituras):
                saida["sondas"][n] = {**l, "calibrada": self.sondas.tem_calibracao(n)}
            # vizinho: média das sondas dos OUTROS blocos que estão com todas as sondas boas (referência do modo degradado)
            outras = [l["temp"] for o, ls in todas.items() if o != b and all(x["temp"] is not None for x in ls)
                      for l in ls]
            vizinho = sum(outras) / len(outras) if outras else None
            try:
                duty, sit = lei.decidir([l["temp"] for l in leituras], self.mem[b], dt, self.cfg, vizinho)
            except Exception:
                duty, sit = 0.0, lei.SEM_SONDA          # qualquer erro: aquecedor desligado
            if not all(self.sondas.tem_calibracao(n) for n in d["sondas"]):
                sit |= lei.SEM_CALIBRACAO
            if self.leitura_lenta:
                sit |= lei.LEITURA_LENTA
            self.pwm.ajustar(b, duty)
            validas = [l["temp"] for l in leituras if l["temp"] is not None]
            saida["blocos"][b] = {"duty": duty, "situacao": sit, "texto": lei.descrever(sit),
                                  "medida": min(validas) if validas else None}
            linha += [l["bruta"] for l in leituras] + [None if l["temp"] is None else round(l["temp"], 3)
                                                      for l in leituras] + [round(duty, 3), sit]
        if self._log:
            self._log.writerow(linha)
            self._arq.flush()                   # numa queda, perde no máximo o ciclo em curso (os da falha importam)
            if self._ultimo_fsync is None or agora - self._ultimo_fsync >= 30.0:
                os.fsync(self._arq.fileno())    # e garante no cartão SD a cada 30 s (falta de energia)
                self._ultimo_fsync = agora
        self.ciclos += 1
        return saida

    def _passar_para_leitura_lenta(self):
        """A conversão simultânea parou no meio do ensaio. Lendo uma a uma, o ciclo leva ~3 s: rodar o LQI de 1 s
        nesse passo seria usar ganhos errados. Passa ao período de reserva (5 s), com os ganhos dele, e reinicia os
        observadores (os estados e o integrador são de outro conjunto de ganhos). Não volta sozinho para 1 s."""
        self.usar_bulk = False
        self.leitura_lenta = True
        self.cfg.PERIODO_S = self.cfg.PERIODO_SEM_BULK_S
        for m in self.mem.values():
            m.x = None
        print(f"tcs: AVISO: conversão simultânea falhou ({self.falhas_bulk}× seguidas). Lendo uma a uma, "
              f"período {self.cfg.PERIODO_S} s (ganhos {self.cfg.LQI_GANHOS.name}): não cumpre R06 (1 Hz).", flush=True)

    def rodar(self):
        """Laço do Pi: roda até ser interrompido; sempre termina com os aquecedores desligados."""
        proximo = time.monotonic()
        avisar_systemd("READY=1")
        try:
            while True:
                s = self.ciclo()
                avisar_systemd("WATCHDOG=1")
                if self.ciclos % max(1, int(round(15 / self.cfg.PERIODO_S))) == 1:
                    txt = "  ".join(f"{b}: {v['medida'] if v['medida'] is None else round(v['medida'], 2)} °C "
                                    f"{v['duty'] * 100:.0f} % ({v['texto']})" for b, v in s["blocos"].items())
                    print(datetime.now().strftime("%H:%M:%S"), txt, flush=True)
                # período fixo: o próximo ciclo começa PERIODO_S depois do início deste (a leitura já gastou ~3 s)
                proximo += self.cfg.PERIODO_S
                espera = proximo - time.monotonic()
                if espera < 0:                      # atrasou mais de um período: não tenta "recuperar" ciclos
                    proximo = time.monotonic()
                    espera = 0.0
                time.sleep(espera)
        finally:
            self.fechar()

    def fechar(self):
        self.pwm.fechar()
        if self._log:
            self._arq.flush()
            os.fsync(self._arq.fileno())
            self._arq.close()
            self._log = None
