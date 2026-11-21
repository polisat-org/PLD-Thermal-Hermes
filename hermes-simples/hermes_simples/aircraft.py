"""Lista fixa de 20 avioes + leitura do aircraft.json do dump1090.

Regra simples (igual a descricao do protocolo):
  - ICAO novo  -> ocupa o primeiro slot livre;
  - ICAO conhecido -> atualiza so o slot dele;
  - lista cheia -> ignora o aviao novo;
  - ICAO 0     -> slot vazio;
  - aviao velho (seen antigo ou ausente do JSON) -> libera o slot.

Cada atualizacao recebe ``met_s``: tempo decorrido desde o inicio da missao.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from .plane import (
    ADDRTYPE,
    EMERGENCY,
    FLAG_MLAT,
    FLAG_MODE_A,
    FLAG_MODE_C,
    FLAG_TISB,
    MRAR_SOURCE,
    SIL_TYPE,
    TURBULENCE,
    _float,
    empty_slot,
    enum_code,
    nav_modes_code,
    new_plane,
    parse_icao,
)

NUMBER_FIELDS = (
    "alt_baro", "alt_geom", "gs", "ias", "tas", "mach", "track", "track_rate",
    "roll", "mag_heading", "true_heading", "baro_rate", "geom_rate", "category",
    "nav_qnh", "nav_altitude_mcp", "nav_altitude_fms", "nav_heading", "lat",
    "lon", "nic", "rc", "seen_pos", "version", "nic_baro", "nac_p", "nac_v",
    "sil", "gva", "sda", "wind_speed", "wind_dir", "temperature", "pressure",
    "humidity", "messages", "seen", "rssi",
)


class AircraftList:
    def __init__(self, capacity: int = 20, max_age_s: float = 300.0,
                 max_missing_polls: int = 3, clock=None) -> None:
        self.capacity = capacity
        self.max_age_s = max_age_s
        self.max_missing_polls = max_missing_polls
        self.clock = clock or time.monotonic
        self.slots = [empty_slot() for _ in range(capacity)]
        self._index: dict[int, int] = {}
        self._missing: dict[int, int] = {}
        self.mission_t0: float | None = None
        self.updates = 0
        self.ignored = 0
        self.purged = 0
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- tempo
    def set_mission_start(self, t: float | None = None) -> None:
        """Marca t=0 da missao e zera as estatisticas desta missao."""
        with self._lock:
            self.mission_t0 = self.clock() if t is None else float(t)
            self._missing.clear()
            self.updates = 0
            self.ignored = 0
            self.purged = 0

    def met_s(self, t: float | None = None) -> float:
        """Tempo de missao em segundos (resolucao melhor que 0,1 s)."""
        if self.mission_t0 is None:
            return 0.0
        now = self.clock() if t is None else float(t)
        return round(max(0.0, now - self.mission_t0), 3)

    # --------------------------------------------------------------- slots
    def _remove_locked(self, icao: int) -> bool:
        index = self._index.pop(icao, None)
        if index is None:
            return False
        self.slots[index] = empty_slot()
        self._missing.pop(icao, None)
        self.purged += 1
        return True

    def remove(self, icao: int) -> bool:
        """Libera explicitamente o slot de um ICAO. Devolve True se removeu."""
        with self._lock:
            return self._remove_locked(icao)

    def purge(self, present_icaos: set[int] | None = None) -> int:
        """Libera slots velhos.

        Um slot cai quando ``seen`` passa de ``max_age_s`` ou quando o ICAO
        falta em ``max_missing_polls`` documentos seguidos. ``present_icaos``
        None desliga a regra de ausencia (utilize em testes unitarios).
        """
        removed = 0
        with self._lock:
            for icao in list(self._index):
                index = self._index[icao]
                seen = _float(self.slots[index].get("seen"))
                if seen == seen and seen > self.max_age_s:
                    if self._remove_locked(icao):
                        removed += 1
                    continue

                if present_icaos is None:
                    self._missing[icao] = 0
                    continue

                if icao in present_icaos:
                    self._missing[icao] = 0
                    continue

                faltas = self._missing.get(icao, 0) + 1
                if faltas > self.max_missing_polls:
                    if self._remove_locked(icao):
                        removed += 1
                else:
                    self._missing[icao] = faltas
        return removed

    def update(self, icao: int, values: dict, met_s: float | None = None) -> int | None:
        """Atualiza um aviao. Devolve o indice do slot ou None se ignorado."""
        with self._lock:
            seen = _float(values.get("seen"))
            if seen == seen and seen > self.max_age_s:
                # Se o aviao ja estava na lista, o dado velho nao pode continuar
                # ocupando o slot: libera e devolve None.
                if icao in self._index:
                    self._remove_locked(icao)
                else:
                    self.ignored += 1
                return None

            index = self._index.get(icao)
            if index is not None:
                plane = self.slots[index]
            else:
                index = next(
                    (i for i, slot in enumerate(self.slots) if slot["icaoID"] == 0),
                    None,
                )
                if index is None:
                    self.ignored += 1
                    return None
                plane = new_plane(icao)
                self.slots[index] = plane
                self._index[icao] = index

            for name in NUMBER_FIELDS:
                if name in values:
                    value = values[name]
                    if (name == "alt_baro" and isinstance(value, str)
                            and value.strip().lower() == "ground"):
                        value = 0.0
                    plane[name] = _float(value)

            if "flight" in values:
                plane["flight"] = str(values["flight"])
            if "squawk" in values:
                plane["squawk"] = str(values["squawk"])
            if "type" in values:
                plane["addrtype"] = enum_code(values["type"], ADDRTYPE)
            if "sil_type" in values:
                plane["sil_type"] = enum_code(values["sil_type"], SIL_TYPE)
            if "mrar_source" in values:
                plane["mrar_source"] = enum_code(values["mrar_source"], MRAR_SOURCE)
            if "turbulence" in values:
                plane["turbulence"] = enum_code(values["turbulence"], TURBULENCE)
            if "emergency" in values:
                plane["emergency"] = enum_code(values["emergency"], EMERGENCY)
            if "nav_modes" in values:
                plane["nav_modes"] = nav_modes_code(values["nav_modes"])

            flags = plane["flags"]
            if values.get("modea"):
                flags |= FLAG_MODE_A
            if values.get("modec"):
                flags |= FLAG_MODE_C
            if values.get("mlat"):
                flags |= FLAG_MLAT
            if values.get("tisb"):
                flags |= FLAG_TISB
            plane["flags"] = flags

            stamp = self.met_s() if met_s is None else _float(met_s)
            plane["met_s"] = stamp if stamp == stamp else 0.0
            self._missing[icao] = 0

            self.updates += 1
            return index

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(slot) for slot in self.slots]

    def clear(self) -> None:
        with self._lock:
            self.slots = [empty_slot() for _ in range(self.capacity)]
            self._index.clear()
            self._missing.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)


def apply_aircraft_json(document: dict, aircraft: AircraftList) -> int:
    """Aplica um documento aircraft.json. Devolve o numero de atualizacoes."""
    entries = []
    present: set[int] = set()
    for entry in document.get("aircraft") or []:
        if not isinstance(entry, dict):
            continue
        icao = parse_icao(entry.get("hex", entry.get("icao")))
        if icao is None:
            continue
        present.add(icao)
        entries.append((icao, entry))

    # Primeiro libera quem sumiu / ficou velho; so depois aloca novos ICAOs.
    aircraft.purge(present)
    stamp = aircraft.met_s()
    updated = 0
    for icao, entry in entries:
        if aircraft.update(icao, entry, met_s=stamp) is not None:
            updated += 1
    return updated


class JsonSource:
    """Le o arquivo/URL aircraft.json uma vez por chamada."""

    def __init__(self, url: str, aircraft: AircraftList) -> None:
        self.url = url
        self.aircraft = aircraft
        self.updates = 0
        self.errors = 0
        self.total_messages = 0
        self.last_updates = 0

    def poll_once(self) -> int:
        try:
            with urllib.request.urlopen(self.url, timeout=1.0) as response:
                document = json.load(response)
            count = apply_aircraft_json(document, self.aircraft)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            self.errors += 1
            return 0

        messages = _float(document.get("messages"))
        if messages == messages and messages >= 0:
            self.total_messages = int(messages)
        self.last_updates = count
        self.updates += count
        return count
