"""Leitura do controle térmico das baterias (TCS) para mandar ao OBC.

O TCS (~/tcs, serviço tcs) é outro programa: ele grava uma linha por ciclo (1 s) num CSV em ~/tcs/logs/, com flush
a cada linha. Este módulo só LÊ a última linha do CSV mais novo; não importa nem altera nada do TCS.

O resultado vai no fim de cada bloco da UART, num trailer de 32 bytes (little-endian):

  off tam campo
   0   4  magic "TCS1"
   4   1  versao (1)
   5   1  flags: bit0 dado valido (TCS rodando, linha com ate MAX_IDADE_S)
   6   2  idade da linha do TCS em decimos de s (0xFFFF = sem dado)
   8   8  4 x int16: sondas 1..4 calibradas, 0,01 C (-32768 = invalida)
  16   1  duty do bloco A (0..100 %)
  17   1  duty do bloco B (0..100 %)
  18   1  situacao do bloco A (codigos do TCS: 1 sem sonda, 2 corte, 4 uma sonda, 8 sem calibracao,
  19   1  situacao do bloco B   16 leitura lenta, 32 discorda do outro bloco, 64 aquecedor sem resposta)
  20   4  t_s do laco do TCS em decimos de s (relogio monotonico do TCS)
  24   1  missao ADS-B ativa (1/0)
  25   1  avioes no bloco (0..20)
  26   4  reservado (0)
  30   2  CRC16-CCITT (poly 0x1021, init 0xFFFF) dos bytes 0..29
"""
from __future__ import annotations

import csv
import io
import math
import os
import struct
import time
from datetime import datetime
from pathlib import Path

TRAILER_SIZE = 32
MAGIC = b"TCS1"
VERSAO = 1
TEMP_INVALIDA = -32768
MAX_IDADE_S = 10.0
_FMT = struct.Struct("<4sBBH4hBBBBIBB4x")   # 30 bytes + CRC


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _ultima_linha(caminho: Path) -> str | None:
    """Última linha completa do arquivo (lê só o fim)."""
    try:
        with open(caminho, "rb") as f:
            f.seek(0, os.SEEK_END)
            tam = f.tell()
            f.seek(max(0, tam - 1024))
            fim = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    linhas = [l for l in fim.splitlines() if l.strip()]
    if fim.endswith("\n"):
        return linhas[-1] if linhas else None
    # o TCS está no meio da escrita: a última linha pode estar pela metade
    return linhas[-2] if len(linhas) >= 2 else None


def _cabecalho(caminho: Path) -> list[str] | None:
    try:
        with open(caminho, encoding="utf-8") as f:
            return next(csv.reader([f.readline()]))
    except (OSError, StopIteration):
        return None


def _temp_x100(v: str | None) -> int:
    try:
        t = float(v)
    except (TypeError, ValueError):
        return TEMP_INVALIDA
    if not math.isfinite(t):
        return TEMP_INVALIDA
    return max(-32767, min(32767, int(round(t * 100))))


def _int(v, lo=0, hi=255) -> int:
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return lo


class TcsLeitor:
    def __init__(self, pasta_logs: str | Path = Path.home() / "tcs" / "logs", agora=None) -> None:
        self.pasta = Path(pasta_logs)
        self.agora = agora or (lambda: datetime.now().astimezone())
        self._arquivo: Path | None = None
        self._cab: list[str] | None = None

    def _arquivo_mais_novo(self) -> Path | None:
        try:
            arqs = sorted(self.pasta.glob("tcs-*.csv"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return None
        return arqs[-1] if arqs else None

    def ler(self) -> dict | None:
        """Último estado do TCS como dicionário, ou None se não houver log."""
        arq = self._arquivo_mais_novo()
        if arq is None:
            return None
        if arq != self._arquivo:
            self._arquivo, self._cab = arq, _cabecalho(arq)
        linha = _ultima_linha(arq)
        if not self._cab or not linha or linha.startswith("t_s"):
            return None
        valores = next(csv.reader(io.StringIO(linha)))
        d = dict(zip(self._cab, valores))
        try:
            hora = datetime.fromisoformat(d.get("hora", ""))
            idade = (self.agora() - hora).total_seconds()
        except (ValueError, TypeError):
            idade = math.inf
        d["_idade_s"] = idade
        return d

    def trailer(self, missao_ativa: bool, avioes: int) -> bytes:
        d = self.ler()
        if d is None:
            flags, idade_ds, temps, duty, sit, t_ds = 0, 0xFFFF, [TEMP_INVALIDA] * 4, (0, 0), (0, 0), 0
        else:
            idade = d["_idade_s"]
            valido = 0 <= idade <= MAX_IDADE_S
            flags = 1 if valido else 0
            idade_ds = 0xFFFF if not math.isfinite(idade) or idade < 0 else min(0xFFFE, int(idade * 10))
            temps = [_temp_x100(d.get(f"sonda{n}")) for n in (1, 2, 3, 4)]
            duty = tuple(_int(round(float(d.get(f"duty_{b}") or 0) * 100), 0, 100) if d.get(f"duty_{b}") else 0
                         for b in ("A", "B"))
            sit = (_int(d.get("situacao_A")), _int(d.get("situacao_B")))
            try:
                t_ds = max(0, min(0xFFFFFFFF, int(float(d.get("t_s", 0)) * 10)))
            except ValueError:
                t_ds = 0
        corpo = _FMT.pack(MAGIC, VERSAO, flags, idade_ds, *temps, duty[0], duty[1], sit[0], sit[1], t_ds,
                          1 if missao_ativa else 0, max(0, min(20, avioes)))
        return corpo + struct.pack("<H", crc16_ccitt(corpo))


def decodificar(trailer: bytes) -> dict:
    """Usado nos testes e na bancada: o inverso de TcsLeitor.trailer()."""
    if len(trailer) != TRAILER_SIZE:
        raise ValueError("trailer precisa ter 32 bytes")
    corpo, (crc,) = trailer[:30], struct.unpack("<H", trailer[30:])
    if crc16_ccitt(corpo) != crc:
        raise ValueError("CRC do trailer não confere")
    m, v, flags, idade, t1, t2, t3, t4, da, db, sa, sb, t_ds, missao, avioes = _FMT.unpack(corpo)
    if m != MAGIC:
        raise ValueError("magic inválido")
    temp = lambda x: None if x == TEMP_INVALIDA else x / 100
    return {"versao": v, "valido": bool(flags & 1), "idade_s": None if idade == 0xFFFF else idade / 10,
            "sondas": [temp(t) for t in (t1, t2, t3, t4)], "duty": (da, db), "situacao": (sa, sb),
            "t_s": t_ds / 10, "missao": bool(missao), "avioes": avioes}


if __name__ == "__main__":   # python3 -m hermes_simples.tcs_link : mostra o que iria para o OBC
    lt = TcsLeitor()
    while True:
        print(decodificar(lt.trailer(False, 0)), flush=True)
        time.sleep(2)
