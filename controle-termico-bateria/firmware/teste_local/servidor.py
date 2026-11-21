"""Página local do teste: http://127.0.0.1:8765

    cd controle-termico-bateria/firmware
    python -I teste_local/servidor.py

Só escuta no próprio PC (127.0.0.1). Ctrl+C para sair.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bancada import AQUI, Bancada
from planta import CENARIOS

PORTA = 8765
VELOCIDADES = {"1": 1, "10": 10, "60": 60, "300": 300, "max": 0}


class Sessao:
    def __init__(self):
        self.trava = threading.Lock()
        self.ajustes = {"cenario": "sala", "ref": 20.0, "lei": "lqi", "hist": 0.5}
        self.velocidade = "60"
        self.rodando = False
        self.novo()
        threading.Thread(target=self._laco, daemon=True).start()

    def novo(self):
        a = self.ajustes
        if getattr(self, "bancada", None):
            self.bancada.fechar()
        self.bancada = Bancada(a["cenario"], float(a["ref"]), a["lei"], float(a["hist"]))
        self.bancada.passo()
        self.rodando = False

    def _laco(self):
        while True:
            if not self.rodando:
                time.sleep(0.05)
                continue
            t0 = time.perf_counter()
            with self.trava:
                b = self.bancada
                if b.planta.acabou:
                    self.rodando = False
                    continue
                b.passo()
                periodo = b.periodo
            v = VELOCIDADES[self.velocidade]
            espera = periodo / v - (time.perf_counter() - t0) if v else 0.0
            time.sleep(max(0.0, espera) if v else 0.0005)

    def estado(self, desde: int):
        with self.trava:
            b = self.bancada
            return {"rodando": self.rodando, "acabou": b.planta.acabou, "velocidade": self.velocidade,
                    "ajustes": self.ajustes, "titulo": b.planta.titulo, "dur_min": b.planta.dur / 60,
                    "periodo_s": b.periodo, "pmax": b.cfg.PMAX_W, "falha": b.planta.falha,
                    "total": len(b.amostras), "desde": desde, "amostras": b.amostras[desde:desde + 3000]}

    def comando(self, c: dict):
        with self.trava:
            acao = c.get("acao")
            if acao == "iniciar":
                self.rodando = True
            elif acao == "pausar":
                self.rodando = False
            elif acao == "reiniciar":
                for k in ("cenario", "lei"):
                    if k in c:
                        self.ajustes[k] = c[k]
                for k in ("ref", "hist"):
                    if k in c:
                        self.ajustes[k] = float(c[k])
                if self.ajustes["cenario"] not in CENARIOS:
                    self.ajustes["cenario"] = "sala"
                self.novo()
            elif acao == "velocidade" and c.get("valor") in VELOCIDADES:
                self.velocidade = c["valor"]
            elif acao == "falha":
                n = int(c["sonda"])
                if n in self.bancada.planta.falha and c.get("tipo") in ("", "desconectada", "crc", "85", "fria"):
                    self.bancada.planta.falha[n] = c["tipo"]


SESSAO = None


class Pagina(BaseHTTPRequestHandler):
    def _enviar(self, corpo: bytes, tipo: str, cod=200):
        self.send_response(cod)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._enviar((AQUI / "pagina.html").read_bytes(), "text/html; charset=utf-8")
        elif u.path == "/api/estado":
            desde = int(parse_qs(u.query).get("desde", ["0"])[0])
            self._enviar(json.dumps(SESSAO.estado(desde)).encode(), "application/json")
        else:
            self._enviar(b"nao encontrado", "text/plain", 404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/comando":
            return self._enviar(b"nao encontrado", "text/plain", 404)
        n = int(self.headers.get("Content-Length", 0))
        SESSAO.comando(json.loads(self.rfile.read(n) or b"{}"))
        self._enviar(b'{"ok":true}', "application/json")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    SESSAO = Sessao()
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Pagina)
    url = f"http://127.0.0.1:{PORTA}"
    print(f"Teste local do controle térmico em {url}  (Ctrl+C para sair)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SESSAO.bancada.fechar()
