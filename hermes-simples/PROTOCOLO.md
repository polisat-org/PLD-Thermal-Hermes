## Carga útil

Se você não conhece o projeto, comece por [`COMECE_AQUI.md`](COMECE_AQUI.md):
ele mostra onde cada arquivo está no Raspberry Pi e como verificar o serviço.
Aqui só está o formato dos dados.

OBC recebe da terra para ligar carga útil.

OBC manda **LIGAR pela própria UART** (USART3 TX = PB10 → RX0 do Pi, pino 10):
4 bytes `A5 5A 01 FE`, repetidos a cada 2 s enquanto a missão estiver pedida.
Para desligar, manda `A5 5A 00 FF` (3 vezes). Formato: `A5 5A cmd ~cmd`, com
`cmd` = `01` LIGAR / `00` DESLIGAR; o 4º byte é o complemento do 3º e protege
contra ruído. O Pi ignora qualquer coisa fora desse formato e não responde (a
confirmação são os blocos chegando). Os GPIOs BCM27/BCM22 continuam aceitos
como alternativa, mas a integração com o OBC **não usa GPIO** (decisão de 20/11/2026).

OBC ouve na **USART3 (PB11 = RX, igual à PCB)**: 115200, 8N1, sem paridade, sem
controle de fluxo. A recepção é
por **DMA com interrupção de bloco completo** — não uma interrupção por byte —
porque o OBC roda a 4 MHz (MSI sem PLL). Detalhes na seção "Ligação UART OBC ↔
Carga Útil" e em `obc_integration/PATCHES.md`.

Payload faz buffer com os dados do dump1090 e envia o buffer inteiro de 5 em 5 s.

**Desde 21/11/2026 (integração com o TCS):** cada envio leva, no fim, um **trailer térmico
de 32 bytes** com o estado do controle térmico das baterias. O bloco passa a ter
**4080 + 32 = 4112 bytes** e sai a cada 5 s **também com a missão desligada** (os 20
aviões vão zerados), para a telemetria térmica chegar o tempo todo. Ver a seção
"Trailer térmico (TCS)" abaixo. Para voltar ao protocolo de 4080 B: `TCS_TRAILER=False`
em `config.py` (e o OBC também precisa voltar).
Não tem cabeçalho, não tem CRC, não tem `met_ms`, não tem `seq`, não tem
`TIME_SYNC`, não tem ACK. São só **4080 bytes seguidos** (20 × 204).

Uma posição para cada avião. Se recebe sinal com identificador novo, vai para a
primeira posição livre; faz uma listinha de qual avião está em cada posição
(índice 0 a 19). Se recebe sinal com identificador já recebido, só troca a
informação que estava naquela posição. Se a lista está cheia, ignora o avião novo.
Posição livre tem `icaoID = 0` e todos os outros bytes em zero.

Se um avião fica velho (`seen` > 300 s) ou falta em 3 consultas seguidas ao
`aircraft.json`, o slot dele é liberado. Assim, numa missão de 10 minutos, o
21º avião consegue entrar quando alguém sai.

OBC recebe exatamente 4080 bytes (20 × 204), guarda o array e manda os dados mais
recentes no downlink a cada 10 s.

Se a Terra mandar para parar, OBC manda DESLIGAR pela UART (`A5 5A 00 FF`).
Payload desliga o SDR, para o decoder e fica em silêncio na UART. DESLIGAR tem
prioridade sobre LIGAR (num lote de comandos, vale o último).

```c
typedef struct {
    char  flight[8];      // callsign ("ABC123"), ASCII, zero-padded
    char  squawk[8];      // squawk ("7700"), ASCII, zero-padded

    float icaoID;         // "hex" do dump1090 (24 bits)
    float met_s;          // TEMPO DE MISSAO em segundos quando o dado foi processado
    float addrtype;       // "type"        -> codigo: 0=unknown, 1=adsb_icao, ...
    float alt_baro;       // "alt_baro"    -> ft ("ground" vira 0)
    float alt_geom;       // "alt_geom"    -> ft
    float gs;             // "gs"          -> kt (velocidade)
    float ias;            // "ias"         -> kt
    float tas;            // "tas"         -> kt
    float mach;           // "mach"
    float track;          // "track"       -> graus (heading)
    float track_rate;     // "track_rate"  -> graus/s
    float roll;           // "roll"        -> graus
    float mag_heading;    // "mag_heading" -> graus
    float true_heading;   // "true_heading"-> graus
    float baro_rate;      // "baro_rate"   -> ft/min
    float geom_rate;      // "geom_rate"   -> ft/min
    float category;       // "category"    -> codigo hex
    float nav_qnh;        // "nav_qnh"
    float nav_altitude_mcp;
    float nav_altitude_fms;
    float nav_heading;
    float lat;            // "lat"         -> latitude
    float lon;            // "lon"         -> longitude
    float nic;            // "nic"
    float rc;             // "rc"
    float seen_pos;       // "seen_pos"    -> segundos
    float version;        // "version"
    float nic_baro;       // "nic_baro"
    float nac_p;          // "nac_p"
    float nac_v;          // "nac_v"
    float sil;            // "sil"
    float sil_type;       // "sil_type"    -> codigo
    float gva;            // "gva"
    float sda;            // "sda"
    float mrar_source;    // "mrar_source" -> codigo
    float wind_speed;     // "wind_speed"
    float wind_dir;       // "wind_dir"
    float temperature;    // "temperature"
    float pressure;       // "pressure"
    float turbulence;     // "turbulence"  -> codigo
    float humidity;       // "humidity"
    float emergency;      // "emergency"   -> codigo
    float messages;       // "messages"
    float seen;           // "seen"        -> segundos (idade do dado)
    float rssi;           // "rssi"        -> dBFS

    uint32_t nav_modes;   // "nav_modes" -> bits: 1=autopilot,2=vnav,4=alt_hold,
                          // 8=approach,16=lnav,32=tcas
    uint32_t flags;       // bit0=modea, bit1=modec, bit2=mlat, bit3=tisb
} Plane_t;

Plane_t buffer[20];   // 20 * 204 = 4080 bytes enviados de 5 em 5 s
```

### Timestamp e trajetórias

`met_s` é contado a partir do GPIO de LIGAR (zera no início da missão). Ele tem
resolução melhor que 0,1 s, suficiente para ordenar eventos numa missão de
10 minutos.

O dado do dump1090 tem `seen` (idade em segundos, resolução de 0,1 s). Então o
OBC reconstrói o tempo da mensagem ADS-B assim:

```c
float tempo_da_mensagem = p->met_s - p->seen;
```

Com `icaoID + lat/lon + alt_baro + gs + met_s + seen` dá para montar a trajetória
e estimar origem/destino no solo, sem nenhum campo extra na UART.

### Dados

| Dado | Tamanho |
| --- | --- |
| `flight`, `squawk` | 8 bytes cada (ASCII) |
| cada campo numérico (`float`) | 4 bytes (45 campos, incluindo `icaoID` e `met_s`) |
| `nav_modes`, `flags` | 4 bytes cada (`uint32_t`) |
| `Plane_t` | 204 bytes |
| `Plane_t buffer[20]` | 4080 bytes |

Campos que o dump1090 não mandou vêm como `NaN` (float) ou 0 (inteiro).
`_Static_assert(sizeof(Plane_t) == 204)` no código C garante que não há padding.

### Log local de verificação

Além da UART, o payload grava um CSV (`~/hermes-simples-mission.csv` por padrão,
alterável com `--log`) com: `utc`, `met_s`, `event`, `aircraft_count`,
`messages_total`, `decoder_errors`, `buffers_sent`.

Ele é a evidência para os testes de missão (HLR-ADS-01/05/07/08): mostra que
houve recepção, decodificação, extração dos parâmetros e transmissão do buffer,
sem colocar HK na serial. O serviço systemd define
`MISSION_LOG_PATH=/home/polisat/hermes-simples-mission.csv`; na linha de comando
use `--log CAMINHO`.

### Pinos e tempo

| Coisa | Valor |
| --- | --- |
| UART TX do Pi | wPi 15 / BCM14 / pino 8 |
| UART RX do Pi | wPi 16 / BCM15 / pino 10 |
| GND | pino 6 (comum obrigatório) |
| Nível elétrico | 3,3 V TTL — nunca RS-232, nunca 5 V |
| Baud | 115200 8N1 |
| Envio do payload | a cada 5 s enquanto a missão está ligada |
| Downlink OBC | a cada 10 s (decisão do OBC) |
| LIGAR/DESLIGAR | pela UART: `A5 5A 01 FE` / `A5 5A 00 FF` (OBC PB10 → pino 10) |
| GPIO ON / OFF (alternativa, não usada com o OBC) | BCM27 pino 13 / BCM22 pino 15 |
| GPIO SDR | wPi 0 / BCM17 / pino 11 |
| Duração de teste | 10 min, até 20 aviões simultâneos |

### Ligação UART OBC ↔ Carga Útil

O OBC (STM32L476RG) escuta apenas na **USART3**: o pino 8 do Pi (TX) vai para
**PC5 (USART3_RX)** e o GND comum vai no pino 6 do Pi. Nível **3,3 V TTL**,
115200 8N1, sem paridade e sem controle de fluxo. O OBC não manda comando nenhum
pela serial; o pino 10 do Pi / PC4 (USART3_TX) fica livre (opcional).

O payload envia **4080 bytes seguidos** (20 × 204) de 5 em 5 s, sem cabeçalho,
CRC, sequência, `met_ms` nem delimitador de início/fim. Como o OBC roda a 4 MHz
(MSI, PLL desligado), receber byte a byte por interrupção não é viável. A
recepção usa **DMA1_Channel3 com `DMA_REQUEST_2`** (request 2 da tabela 38 da
RM0351; não existe `DMA_REQUEST_USART3_RX` neste HAL) e **dois buffers de 4080
bytes**. A interrupção acontece só quando o bloco fecha; o callback sinaliza a
task com `osThreadFlagsSet()`, que troca o buffer e rearma o DMA. Não há polling.

| Item | Valor |
| --- | --- |
| Porta OBC | USART3 (PC5 = RX; PC4 = TX opcional) |
| DMA RX | DMA1_Channel3, `DMA_REQUEST_2` |
| Recepção | 2 buffers de 4080 bytes, IRQ de bloco completo |
| Prioridade IRQ USART3/DMA | 5 (igual a `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`) |
| Resincronização | timeout de 7 s descarta bloco parcial e rearma o DMA |
| USART1 / USART2 | intactas (telecomandos e debug/telemetria) |
| `Telemetry_t` | 85 bytes, formato inalterado |
| Evento novo | `EVT_HERMES_DONE` + `HermesSummary_t` (`Event_t` passa a 24 bytes) |
| Task | `hermesTask`, stack 128×4 palavras; conferir heap livre (8192 bytes) |

O código a aplicar no `functionalCodes/stateMachineVSCode`, a ordem dos passos e
a verificação estão em `obc_integration/PATCHES.md` (patch:
`obc_integration/OBC-hermes.patch`).

### O que foi removido da versão antiga

`TIME_SYNC`, ACK, HK, EVENT, THERMAL na UART, `seq`, `met_ms`, CRC, flags de
versão, fila de TX, raw TCP feed, logging CSV na serial e controle térmico na
payload. O CSV de missão continua local, não é frame de telemetria.

O supervisor do dump1090-fa (`decoder_process.py`) e o `RuntimeDirectory` do
systemd foram reaproveitados do projeto antigo. O log do decoder fica em
`~/hermes-simples-decoder.log` (via `DECODER_LOG_PATH`). O projeto completo
ficou em `~/hermes/antigo`.


### Trailer térmico (TCS)

O TCS (`~/tcs`, serviço `tcs`, em modo observação: `--sem-pwm`) grava uma linha por segundo
em `~/tcs/logs/tcs-*.csv`. O HERMES lê só a última linha (`hermes_simples/tcs_link.py`, sem
importar nem alterar o TCS) e manda os 32 bytes abaixo no fim de cada bloco (little-endian):

| Offset | Tamanho | Campo |
| --- | --- | --- |
| 0 | 4 | `"TCS1"` |
| 4 | 1 | versão (1) |
| 5 | 1 | flags: bit0 = dado válido (TCS rodando e linha com até 10 s) |
| 6 | 2 | idade da linha do TCS, em décimos de s (`0xFFFF` = sem dado) |
| 8 | 8 | 4 × `int16`: sondas 1 a 4 calibradas, em 0,01 °C (`-32768` = inválida) |
| 16 | 2 | duty dos blocos A e B (0 a 100 %) |
| 18 | 2 | situação dos blocos A e B (códigos do TCS) |
| 20 | 4 | `t_s` do laço do TCS, em décimos de s |
| 24 | 1 | missão ADS-B ativa (1/0) |
| 25 | 1 | aviões no bloco |
| 26 | 4 | reservado (0) |
| 30 | 2 | CRC16-CCITT (poly `0x1021`, init `0xFFFF`) dos bytes 0 a 29 |

O OBC confere o CRC, usa a sonda mais fria como `temperatura_bateria` na telemetria e
repassa os 32 bytes ao TT&C num quadro `'H'` (`'H'` + 32 B) a cada 5 s.

Convivência no Pi Zero (1 núcleo): o `hermes-simples` roda com `Nice=5` e o `tcs` com
`Nice=-5` (arquivos em `integracao/`). Medido em 21/11/2026 com o dump1090 ligado:
laço do TCS com mediana de 1,000 s e máximo de 1,09 s; dump1090 a 2,4 MS/s sem perder amostras.
