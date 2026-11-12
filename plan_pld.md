# Plano do Payload HERMES (ADS-B + Controle Térmico)

## 1. Escopo

O RPi Zero W atua exclusivamente como processador do payload. O OBC/STM é o mestre do
sistema e o responsável pelo downlink via ESP32/TT&C. O limite deste projeto é a UART
RPi <-> OBC.

Dentro do escopo:

- recepção ADS-B em 1090 MHz com RTL-SDR Blog V3;
- decodificação via `readsb`/`dump1090-fa` (C) e leitura do JSON decodificado;
- tabela de até 20 aeronaves por ICAO, com LRU e TTL;
- extração de ICAO, latitude, longitude, altitude, velocidade e track;
- frame binário RPi -> OBC a 115200 8N1, com `seq`, `met_ms`, `len`, `count`, CRC16;
- log bruto ADS-B com timestamp por mensagem;
- controle térmico de 2 conjuntos de baterias com 4 DS18B20 e 2 PWM de hardware;
- política térmica modular `decide_heating()`;
- GPIO de ON/OFF da missão, potência do SDR e debounce;
- boot automático, systemd, watchdog e estado seguro;
- documentação detalhada em inglês em `docs/`.

Fora do escopo:

- OBC <-> ESP32, protocolo fixo de TT&C, LoRa, ground station;
- downlink e remontagem de fragmentos;
- controle de atitude, EPS e comunicação do CubeSat.

## 2. Arquitetura

```text
RTL-SDR V3 --USB--> [MOSFET/relé 5V] <-- GPIO SDR_POWER
       |
       v
 dump1090-fa/readsb (C) --JSON HTTP--> Python: AircraftTable
       |                                      |
       +--AVR TCP--> RawFeedLogger             +--1s--> frame UART --> OBC
                                              |
DS18B20 x4 --1-wire GPIO4--> ThermalController --PWM GPIO12/13--> MOSFET --> aquecedores
```

O OBC recebe:

- frames ADS-B a 1 Hz, até 20 aeronaves;
- frames térmicos a 1 Hz, 4 temperaturas e 2 duties;
- frames de housekeeping;
- ACK de time sync, quando aplicável.

O OBC envia `TIME_SYNC` pelo UART. O ON/OFF da missão continua por GPIO.

## 3. Interface RPi <-> OBC

### 3.1 UART

- Porta: `/dev/serial0`
- Baud: 115200 8N1, little-endian
- RX habilitado: sim
- Tensão: 3,3 V, terra comum

### 3.2 Struct `Plane_t`

```c
typedef struct {
    uint32_t icaoID;            // 24 bits uteis; 0 = slot vazio
    float    latitude;          // graus; NaN se invalido
    float    longitude;         // graus; NaN se invalido
    float    altitude_ft;       // pes; NaN se invalido
    float    velocity_kt;       // nos; NaN se invalido
    float    track_deg;         // graus; NaN se invalido
    uint32_t last_pos_met_ms;   // timestamp da ultima posicao; 0 se invalida
} Plane_t;
```

- `sizeof(Plane_t) == 28`.
- `buffer[20]` = 560 bytes.
- Frame completo maximo = 14 + 560 + 2 = 576 bytes.
- 1 Hz: ~576 bytes/s. A 115200: ~5% de ocupacao.

### 3.3 Frame

| Offset | Tamanho | Campo | Descricao |
|---:|---:|---|---|
| 0 | 2 | SOF | `0xAA 0x55` |
| 2 | 1 | version | `0x01` |
| 3 | 1 | type | HK=1, ADSB=2, THERMAL=3, EVENT=4, TIME_SYNC=5, ACK=6 |
| 4 | 2 | seq | incrementa por frame |
| 6 | 4 | met_ms | tempo de missao em ms |
| 10 | 2 | len | tamanho do payload |
| 12 | 1 | flags | bit0 time sync valido |
| 13 | 1 | count | numero de `Plane_t` no ADSB |
| 14 | len | payload | dados |
| 14+len | 2 | crc16 | CRC16-CCITT sobre version..payload |

CRC16-CCITT: polinomio `0x1021`, init `0xFFFF`, sem reflexao.

### 3.4 Tempo

- OBC e mestre de tempo quando envia `TIME_SYNC` (type 5, `len=0`, `met_ms` no cabecalho).
- RPi usa `time.monotonic_ns()` e aplica o offset do OBC.
- Sem sync, usa tempo decorrido desde o ON da missao.
- Nao usar UTC/GPS; o Zero W nao tem RTC.

## 4. ADS-B

- `readsb`/`dump1090-fa` demodula e valida CRC. O Python nao processa IQ.
- Fonte primaria: `http://127.0.0.1:8080/data/aircraft.json` a 1 Hz.
- Tabela `OrderedDict[icao]`, `N=20`, lock, TTL 300 s.
- Eviccao: em caso de novo ICAO com tabela cheia, remove o menor `last_seen`.
- Posicao: CPR global feita pelo decoder; sem posicao do receptor.
- Campos ausentes viram `NaN`; campos previamente conhecidos nao sao apagados por
  mensagens que nao os contenham.
- `last_pos_met_ms` = `met_ms - seen_pos*1000`, limitado por `POSITION_MAX_AGE_S`.
- Log bruto: `/data/aircraft.json` + stream AVR TCP 30002, gravado em JSONL com
  `met_ms`, `icao` e `raw`. Serve para reprocessamento e V&V.

## 5. Controle Termico

- 4 DS18B20 em 1-wire GPIO4; mapeamento por ROM ID, com auto-descoberta.
- Conjunto 1 = sensores 1 e 2; conjunto 2 = sensores 3 e 4 (configuravel).
- 2 PWM de hardware: GPIO12 e GPIO13, 1 kHz, via pigpio.
- MOSFET N-channel A09T (equivalente AO3400A), low-side, 30 V, 5,8 A.
- Aquecedor assumido: 82 ohm, 13,8 V -> ~168 mA, ~2,32 W por aquecedor.
- Fusivel sugerido: 0,5 A por ramo; termofusivel em serie se possivel.
- Politica padrao:
  - sem sensor valido -> duty 0, falha;
  - `max(temps) >= 45 C` -> duty 0, overtemp;
  - histerese: liga em `min <= 5 C`, desliga em `min >= 15 C`;
  - duty proporcional entre 5 C e 15 C, limitado por `HEAT_MIN_DUTY`,
    `HEAT_MAX_DUTY`, corrente maxima e taxa de rampa.
- `decide_heating()` e funcao pura, separada e substituivel.
- Estado seguro: PWM 0 no boot, no shutdown e em falha; pull-down no gate.

## 6. GPIO

Defaults BCM, alteraveis no `config.py`:

| Funcao | GPIO |
|---|---:|
| MISSION_ON | 27 |
| MISSION_OFF | 22 |
| SDR_POWER | 17 |
| PWM pack 1 | 12 |
| PWM pack 2 | 13 |
| 1-wire | 4 |

- Entradas com pull-down externo e filtro digital de 15 ms.
- ON/OFF vem do OBC; OFF tem prioridade.
- Sem GPIOs novos: nao ha heartbeat, enable de aquecedor ou ACK dedicado.
- Watchdog do SoC + `ExecStopPost` desligam SDR e PWM em falha.

## 7. Modos de Operacao

- `OFF`: SDR desligado, PWM 0; termico pode continuar se `THERMAL_ALWAYS_ON`.
- `POWERING`: SDR energizado, aguardando boot/enumeracao.
- `STARTING`: decoder e fonte JSON iniciados.
- `RUNNING`: ADS-B e telemetria ativos; duracao maxima 600 s.
- `STOPPING`: parada ordenada, SDR desligado.
- `FAULT`: falha do decoder/SDR; retry com backoff.

## 8. Fail-safe

- Saidas para SDR e PWM com pull-down fisico.
- `ExecStopPost` forca SDR off e PWM 0.
- Watchdog de hardware do RPi reinicia o SoC; GPIOs voltam a entrada.
- Sensor termico invalido -> duty 0.
- UART ausente -> log e retry; nao derruba o controle termico.
- Sem fusivel nas PCBs: recomendar fusivel inline e termofusivel.

## 9. Testes e V&V

- Unitarios: CRC, frame roundtrip, parser parcial/corrompido, `Plane_t`, tabela
  LRU/TTL, politica termica, parsing 1-wire, timebase.
- Bancada: `rtl_test -t`, `dump1090-fa --interactive`, `ls /sys/bus/w1/devices`,
  PWM com LED/osciloscopio, loopback UART.
- Integracao: RPi -> STM por 10 min com 20 aeronaves simuladas.
- Criterios: decode >= 90%, perda de frames <= 5%, latencia <= 2 s, 1 posicao/s
  por aeronave, temperatura de bateria > 0 C.

## 10. Entregaveis

- `hermes/`: implementacao Python.
- `services/hermes-payload.service`: systemd.
- `scripts/safe_off.py`: estado seguro.
- `docs/`: documentacao detalhada em ingles.
- `tests/`: testes unitarios.
- `README.md`: instalacao, boot, operacao.

## 11. Riscos

- RPi Zero W e RTL-SDR tem faixa comercial; -10 C pode exigir aquecimento ou
  justificativa no TVAC.
- Ausencia de fusivel no barramento de 13,8 V.
- JST e A09T precisam ser verificados contra a corrente real do aquecedor.
- SD card sob vibracao; usar rootfs read-only/overlayfs e watchdog.
- Decoder externo pode falhar; retry e log sao obrigatorios.

## 12. Estado da implementacao

- Protocolo binario, tabela ADS-B, fonte JSON, log AVR, sensores, politica termica,
  controle, GPIO, PWM, UART, maquina de missao, systemd e script de estado seguro
  implementados em `hermes/` e `scripts/`.
- Testes unitarios: 68 casos em `tests/`, executados com
  `python -m unittest discover -s tests -v`.
- Simulacao sem hardware: `python -m hermes.main --simulate --auto-on --duration 20`.
- Documentacao detalhada em ingles em `docs/`.
