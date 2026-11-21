"""UART: escreve o buffer de 4080 bytes e le os comandos LIGAR/DESLIGAR do OBC.

Comando OBC -> Pi (4 bytes): A5 5A cmd ~cmd
  cmd = 0x01 LIGAR, 0x00 DESLIGAR; o 4o byte e o complemento do 3o (protege contra ruido).
O OBC reenvia LIGAR a cada 2 s enquanto a missao estiver pedida.
"""
from __future__ import annotations

CMD_SYNC = b"\xA5\x5A"
CMD_ON = 0x01
CMD_OFF = 0x00
CMD_SIZE = 4
_RX_MAX = 256


def encode_command(on: bool) -> bytes:
    cmd = CMD_ON if on else CMD_OFF
    return CMD_SYNC + bytes([cmd, cmd ^ 0xFF])


def parse_commands(buf: bytearray) -> list[bool]:
    """Extrai os comandos validos de buf (consome o que ja foi analisado).

    Devolve True para LIGAR e False para DESLIGAR, na ordem em que chegaram.
    Bytes que nao formam um comando valido sao descartados.
    """
    out: list[bool] = []
    while True:
        start = buf.find(CMD_SYNC)
        if start < 0:
            # guarda um 0xA5 no fim: pode ser o inicio de um comando cortado
            keep = 1 if buf[-1:] == CMD_SYNC[:1] else 0
            del buf[: len(buf) - keep]
            return out
        del buf[:start]
        if len(buf) < CMD_SIZE:
            return out
        cmd, check = buf[2], buf[3]
        if cmd in (CMD_ON, CMD_OFF) and check == (cmd ^ 0xFF):
            out.append(cmd == CMD_ON)
            del buf[:CMD_SIZE]
        else:
            del buf[:1]  # nao era comando: procura o proximo A5 5A


class UartSender:
    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self.serial = None
        self.frames = 0
        self.bytes_sent = 0
        self._rx = bytearray()
        self.commands_rx = 0

    def open(self) -> None:
        if self.serial is not None:
            return
        import serial

        self.serial = serial.Serial(
            self.port, self.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=1.0,
        )

    def send(self, data: bytes) -> int:
        if self.serial is None:
            return 0
        written = self.serial.write(data)
        self.frames += 1
        self.bytes_sent += written
        return written

    def read_commands(self) -> list[bool]:
        """Le o que chegou do OBC e devolve os comandos (True = LIGAR). Nao bloqueia."""
        if self.serial is None:
            return []
        waiting = getattr(self.serial, "in_waiting", 0) or 0
        if waiting:
            self._rx.extend(self.serial.read(waiting))
            if len(self._rx) > _RX_MAX:
                del self._rx[: len(self._rx) - _RX_MAX]
        cmds = parse_commands(self._rx)
        self.commands_rx += len(cmds)
        return cmds

    def close(self) -> None:
        if self.serial is not None:
            self.serial.close()
            self.serial = None
