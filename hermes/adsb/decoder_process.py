"""Supervise the external readsb/dump1090-fa decoder process."""

from __future__ import annotations

import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class DecoderProcess:
    def __init__(
        self,
        binary: str,
        args: tuple[str, ...],
        json_url: str,
        start_timeout_s: float = 15.0,
        restart_backoff_s: float = 5.0,
        log_path: str = "logs/decoder.log",
        manage: bool = True,
    ) -> None:
        self.binary = binary
        self.args = tuple(args)
        self.json_url = json_url
        self.start_timeout_s = start_timeout_s
        self.restart_backoff_s = restart_backoff_s
        self.log_path = Path(log_path)
        self.manage = manage
        self._process: subprocess.Popen | None = None
        self._last_start_mono = 0.0
        self.failures = 0

    def start(self) -> bool:
        if not self.manage:
            return self.is_json_alive()
        if self._process and self._process.poll() is None:
            return self.is_json_alive()
        now = time.monotonic()
        if now - self._last_start_mono < self.restart_backoff_s:
            return False
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_path.open("ab") as log_handle:
                self._process = subprocess.Popen(
                    [self.binary, *self.args],
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            log.error("cannot start decoder: %s", exc)
            self.failures += 1
            return False
        self._last_start_mono = now
        deadline = now + self.start_timeout_s
        while time.monotonic() < deadline:
            if self.is_json_alive():
                log.info("decoder is serving JSON")
                return True
            if self._process.poll() is not None:
                break
            time.sleep(0.25)
        log.warning("decoder did not become ready in %.1f s", self.start_timeout_s)
        self.failures += 1
        return False

    def stop(self) -> None:
        if not self._process:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2.0)
        self._process = None

    def is_alive(self) -> bool:
        if not self.manage:
            return self.is_json_alive()
        return self._process is not None and self._process.poll() is None

    def is_json_alive(self) -> bool:
        try:
            with urllib.request.urlopen(self.json_url, timeout=1.0) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process and self._process.poll() is None else None
