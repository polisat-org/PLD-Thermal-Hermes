"""Plane_t: 204 bytes, exatamente o que o OBC recebe na UART.

Formato (little-endian, IEEE-754 para floats):
    char  flight[8]
    char  squawk[8]
    float 45 campos numericos (primeiro: icaoID, segundo: met_s)
    uint32 nav_modes
    uint32 flags

met_s = tempo de missao (segundos) em que o dado foi processado.
O OBC pode calcular o instante da mensagem ADS-B como met_s - seen.

NAO ha cabecalho, CRC, met_ms, seq nem qualquer outro byte extra alem do
met_s que faz parte do Plane_t.
"""
from __future__ import annotations

import math
import struct

NUM_AIRCRAFT = 20

STR_FIELDS = ("flight", "squawk")

FLOAT_FIELDS = (
    "icaoID",
    "met_s",
    "addrtype",
    "alt_baro",
    "alt_geom",
    "gs",
    "ias",
    "tas",
    "mach",
    "track",
    "track_rate",
    "roll",
    "mag_heading",
    "true_heading",
    "baro_rate",
    "geom_rate",
    "category",
    "nav_qnh",
    "nav_altitude_mcp",
    "nav_altitude_fms",
    "nav_heading",
    "lat",
    "lon",
    "nic",
    "rc",
    "seen_pos",
    "version",
    "nic_baro",
    "nac_p",
    "nac_v",
    "sil",
    "sil_type",
    "gva",
    "sda",
    "mrar_source",
    "wind_speed",
    "wind_dir",
    "temperature",
    "pressure",
    "turbulence",
    "humidity",
    "emergency",
    "messages",
    "seen",
    "rssi",
)

INT_FIELDS = ("nav_modes", "flags")

PLANE_FORMAT = "<8s8s45fII"
PLANE_STRUCT = struct.Struct(PLANE_FORMAT)
PLANE_SIZE = PLANE_STRUCT.size
BUFFER_SIZE = PLANE_SIZE * NUM_AIRCRAFT

FLAG_MODE_A = 1 << 0
FLAG_MODE_C = 1 << 1
FLAG_MLAT = 1 << 2
FLAG_TISB = 1 << 3

NAV_AUTOPILOT = 1 << 0
NAV_VNAV = 1 << 1
NAV_ALT_HOLD = 1 << 2
NAV_APPROACH = 1 << 3
NAV_LNAV = 1 << 4
NAV_TCAS = 1 << 5

ADDRTYPE = (
    "unknown",
    "adsb_icao",
    "adsb_icao_nt",
    "adsb_other",
    "adsb_tisb",
    "sls_icao",
    "sls_other",
    "adsb_non_icao",
)
SIL_TYPE = ("unknown", "SILperhour", "SILper15NM", "SILper1NM", "SILper3NM")
MRAR_SOURCE = ("unknown", "adsb", "mlat", "tisb")
TURBULENCE = ("unknown", "none", "light", "moderate", "severe")
EMERGENCY = ("none", "general", "lifeguard", "fuel", "medical", "broadcast")
NAV_MODE_NAMES = (
    ("autopilot", NAV_AUTOPILOT),
    ("vnav", NAV_VNAV),
    ("alt_hold", NAV_ALT_HOLD),
    ("approach", NAV_APPROACH),
    ("lnav", NAV_LNAV),
    ("tcas", NAV_TCAS),
)


def enum_code(value: object, table: tuple[str, ...]) -> float:
    """Converte uma string do dump1090 em codigo numerico (0 = desconhecido)."""
    if isinstance(value, str):
        text = value.strip().lower()
        for code, name in enumerate(table):
            if name.lower() == text:
                return float(code)
    return 0.0


def nav_modes_code(value: object) -> int:
    bits = 0
    if isinstance(value, (list, tuple, set)):
        for name, bit in NAV_MODE_NAMES:
            if name in value:
                bits |= bit
    return bits


def new_plane(icao: int) -> dict:
    """Um aviao novo: campos ausentes sao NaN (float) ou 0 (inteiro)."""
    plane: dict = {
        "flight": b"",
        "squawk": b"",
        "nav_modes": 0,
        "flags": 0,
    }
    for name in FLOAT_FIELDS:
        plane[name] = math.nan
    plane["icaoID"] = float(icao)
    plane["met_s"] = math.nan
    return plane


def empty_slot() -> dict:
    """Slot livre: todos os bytes zero. icaoID == 0 significa 'vazio'."""
    plane: dict = {"flight": b"", "squawk": b"", "nav_modes": 0, "flags": 0}
    for name in FLOAT_FIELDS:
        plane[name] = 0.0
    return plane


def parse_icao(value: object) -> int | None:
    try:
        if isinstance(value, int):
            icao = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            icao = int(text, 16)
        else:
            return None
    except (TypeError, ValueError):
        return None
    icao &= 0xFFFFFF
    return icao or None


def _float(value: object) -> float:
    if isinstance(value, bool) or value is None:
        return math.nan
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _ascii8(value: object) -> bytes:
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).split(b"\0", 1)[0][:7]
    return str(value).encode("ascii", "ignore")[:7]


def pack_planes(planes: list[dict]) -> bytes:
    """Gera o buffer de 4080 bytes (20 Plane_t). Completa com slots vazios."""
    if len(planes) > NUM_AIRCRAFT:
        raise ValueError(f"no maximo {NUM_AIRCRAFT} avioes")
    slots = list(planes) + [empty_slot() for _ in range(NUM_AIRCRAFT - len(planes))]
    return b"".join(
        PLANE_STRUCT.pack(
            _ascii8(plane.get("flight")),
            _ascii8(plane.get("squawk")),
            *(_float(plane.get(name)) for name in FLOAT_FIELDS),
            int(plane.get("nav_modes", 0)) & 0xFFFFFFFF,
            int(plane.get("flags", 0)) & 0xFFFFFFFF,
        )
        for plane in slots
    )


def unpack_planes(data: bytes) -> list[dict]:
    """Le o buffer de 4080 bytes e devolve os 20 Plane_t."""
    if len(data) != BUFFER_SIZE:
        raise ValueError(f"buffer deve ter {BUFFER_SIZE} bytes, tem {len(data)}")
    result = []
    for offset in range(0, BUFFER_SIZE, PLANE_SIZE):
        values = PLANE_STRUCT.unpack_from(data, offset)
        plane = {
            "flight": values[0].split(b"\0", 1)[0].decode("ascii", "replace"),
            "squawk": values[1].split(b"\0", 1)[0].decode("ascii", "replace"),
        }
        float_end = 2 + len(FLOAT_FIELDS)
        for name, value in zip(FLOAT_FIELDS, values[2:float_end]):
            plane[name] = value
        plane["nav_modes"] = values[float_end]
        plane["flags"] = values[float_end + 1]
        result.append(plane)
    return result
