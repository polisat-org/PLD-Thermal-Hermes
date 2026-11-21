# Console UART (HERMES Simples)

```bash
sudo systemctl stop hermes-payload hermes-simples 2>/dev/null || true
python3 teste_uart/console_uart.py --port /dev/serial0
# digite 'help'
```

Sem hardware, confira o gerador de bytes:

```bash
python3 teste_uart/console_uart.py --selftest
```

## Comandos principais

| Comando | O que faz |
| --- | --- |
| `byte AA` | envia um byte só |
| `send AA 55 01` | envia bytes escolhidos |
| `burst 4080 AA` | envia exatamente 4080 bytes e para |
| `loop 204 5 AA 55` | repete 204 bytes a cada 5 s (teste de leitura de 1 `Plane_t`) |
| `plane 0` | envia 1 `Plane_t` de exemplo (204 bytes) |
| `buffer 3` | envia buffer completo com 3 aviões (4080 bytes) |
| `stream 5` | envia buffer completo a cada 5 s |
| `stop` | silêncio total na UART |
| `capture on` / `rx` / `planes` | captura e decodifica o que o STM32 mandar |
| `stats` / `clear` | contadores / limpar |

O comando `planes` mostra `met_s` (tempo de missão) ao lado de ICAO, posição,
altitude, velocidade e RSSI. A ordem no buffer é a mesma do STM32: o segundo
float de cada `Plane_t` é o timestamp.

## Ligação

| Pi | STM32 |
| --- | --- |
| TX wPi 15 / BCM14 / pino 8 | RX |
| RX wPi 16 / BCM15 / pino 10 | TX |
| GND pino 6 | GND |

3,3 V TTL. Nunca RS-232, nunca 5 V.
