"""Minimal sd_notify support (no external dependency)."""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(state: str) -> None:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(state.encode("utf-8"))
    except OSError:
        log.debug("sd_notify failed", exc_info=True)


def ready() -> None:
    notify("READY=1")


def watchdog() -> None:
    notify("WATCHDOG=1")


def stopping() -> None:
    notify("STOPPING=1")
