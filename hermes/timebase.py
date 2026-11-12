"""Mission time base.

The OBC is the time master when it sends TIME_SYNC.  Without a sync the payload
uses a monotonic clock relative to the mission ON command.  UTC is not available
because the RPi Zero W has no RTC and there is no GPS.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path


def _read_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return uuid.uuid4().hex


class Timebase:
    def __init__(self, boot_id: str | None = None) -> None:
        self.boot_id = boot_id or _read_boot_id()
        self._lock = threading.Lock()
        self._mission_id = ""
        self._mission_active = False
        self._start_mono_ns: int | None = None
        self._sync_mono_ns: int | None = None
        self._sync_met_ms = 0
        self._synced = False

    @property
    def mission_id(self) -> str:
        with self._lock:
            return self._mission_id

    @property
    def mission_active(self) -> bool:
        with self._lock:
            return self._mission_active

    @property
    def synced(self) -> bool:
        with self._lock:
            return self._synced

    def start_mission(self) -> str:
        with self._lock:
            self._mission_id = f"{self.boot_id[:8]}-{uuid.uuid4().hex[:6]}"
            self._mission_active = True
            self._start_mono_ns = time.monotonic_ns()
            self._sync_mono_ns = None
            self._sync_met_ms = 0
            self._synced = False
            return self._mission_id

    def stop_mission(self) -> None:
        with self._lock:
            self._mission_active = False

    def met_ms(self) -> int:
        now = time.monotonic_ns()
        with self._lock:
            if self._synced and self._sync_mono_ns is not None:
                return max(0, self._sync_met_ms + (now - self._sync_mono_ns) // 1_000_000)
            if self._start_mono_ns is None:
                return 0
            return max(0, (now - self._start_mono_ns) // 1_000_000)

    def apply_time_sync(self, obc_met_ms: int) -> None:
        with self._lock:
            self._sync_mono_ns = time.monotonic_ns()
            self._sync_met_ms = max(0, int(obc_met_ms))
            self._synced = True
