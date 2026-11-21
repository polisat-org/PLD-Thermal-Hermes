"""Controla o dump1090-fa usando o supervisor do projeto antigo.

So uma casca fina para manter a API simples (start/stop) usada pela missao.
"""
from __future__ import annotations

from .decoder_process import DecoderProcess


class Decoder:
    def __init__(self, cfg, enabled: bool = True) -> None:
        self.cfg = cfg
        self.enabled = enabled
        self.process = DecoderProcess(
            binary=cfg.DECODER_BIN,
            args=cfg.DECODER_ARGS,
            json_url=cfg.JSON_URL,
            start_timeout_s=cfg.DECODER_START_TIMEOUT_S,
            restart_backoff_s=cfg.DECODER_RESTART_BACKOFF_S,
            log_path=cfg.DECODER_LOG_PATH,
            manage=enabled,
        )

    def start(self) -> bool:
        if not self.enabled:
            return True
        return self.process.start()

    def stop(self) -> None:
        self.process.stop()

    def is_alive(self) -> bool:
        return self.process.is_alive()

    @property
    def failures(self) -> int:
        return self.process.failures
