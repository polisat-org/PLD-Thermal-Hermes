# COMECE AQUI — mapa do HERMES no Raspberry Pi

> Caminhos reorganizados em 20/11/2026 (mapa completo em `~/LEIAME.md`):
> `~/HERMES_SIMPLES` → `~/hermes/atual`, `~/VERSAO_ANTIGA_HERMES` → `~/hermes/antigo`,
> resultados de teste em `~/hermes/resultados/`. O serviço e `/opt/hermes-simples` não mudaram.

Este arquivo é para quem **não conhece o projeto**. Não é preciso saber nada de
ADS-B, STM32 ou systemd para achar as coisas. Leia esta página primeiro; depois
vá para [`PROTOCOLO.md`](PROTOCOLO.md) (o formato dos dados) ou
[`obc_integration/PATCHES.md`](obc_integration/PATCHES.md) (o lado OBC).

## 1. O que o projeto faz, em 1 minuto

1. O Raspberry Pi liga o receptor SDR (um dongle RTL-SDR) quando o OBC manda
   **LIGAR** pela UART (`A5 5A 01 FE`, repetido a cada 2 s; ver `PROTOCOLO.md`).
2. O programa `dump1090-fa` escuta o ar e escreve os aviões vistos num arquivo
   JSON em `/run/dump1090-fa/aircraft.json`.
3. O HERMES lê esse JSON, guarda até **20 aviões** em posições fixas e, **a cada
   5 segundos**, escreve na UART um bloco de **4080 bytes** = 20 × 204.
4. O OBC recebe esse bloco e manda os dados para a Terra. O comando de
   LIGAR/DESLIGAR vem **pela mesma serial** (sentido OBC → Pi); os GPIOs ficaram
   só como alternativa. O código do OBC está no repo `polisat-org/OBC`, branch
   `payload_integration` (`functionalCodes/stateMachineInVSCode`).
5. O Pi também salva um CSV local (`~/hermes-simples-mission.csv`) com a
   evidência da missão: horário, quantos aviões, quantas mensagens, quantos
   blocos enviados. Esse CSV **não vai na UART**.

O projeto antigo, maior, fazia HK, THERMAL, EVENT, ACK, TIME_SYNC, CRC e
vários tipos de frame. Esta versão é só o necessário para a missão ADS-B.

## 2. Onde está cada coisa no Raspberry Pi

| Lugar | O que é | Pode editar? |
| --- | --- | --- |
| `~/hermes/antigo` | **Arquivo histórico** do projeto completo. Serve de referência; não é o que está rodando. | Não apagar. Só consultar. |
| `~/hermes/atual` | **Código fonte legível**, documentação, testes e o pacote de integração com o OBC. | Sim — é aqui que se mexe. |
| `/opt/hermes-simples` | Cópia do código que o systemd realmente executa. | Não editar direto; copie de `~/hermes/atual` e reinicie o serviço. |
| `/etc/systemd/system/hermes-simples.service` | Diz ao Linux como iniciar o programa e onde gravar os logs. | Sim, com `sudo systemctl daemon-reload` depois. |
| `/run/dump1090-fa/aircraft.json` | Saída do decoder: os aviões que ele viu agora. Temporário, some no reboot. | Não. |
| `~/hermes-simples-mission.csv` | Evidência local da missão (uma linha a cada 5 s). Só aparece depois que a missão é ligada pelo GPIO. | É gerado; não editar. |
| `~/hermes-simples-decoder.log` | Saída do `dump1090-fa`. | É gerado; não editar. |
| `~/hermes/resultados/missao-10min-final.*`, `~/hermes/resultados/hermes-real-sdr-report.*` | Resultados dos testes de bancada/SDR. | Não. |

## 3. Mapa de `~/hermes/atual`

| Arquivo/pasta | Para que serve |
| --- | --- |
| `COMECE_AQUI.md` | Este guia. |
| `README.md` | Resumo técnico, instalação, rollback. |
| `PROTOCOLO.md` | Pinos, `Plane_t` (204 bytes), regras dos 20 slots, `met_s`, CSV. |
| `hermes_simples/config.py` | **Todos os números ajustáveis**: pinos, baud, período de envio, TTL do slot, caminhos dos logs. |
| `hermes_simples/main.py` | Ponto de entrada do programa. |
| `hermes_simples/mission.py` | Máquina de estados: liga, lê aviões, envia, desliga. |
| `hermes_simples/aircraft.py` | Controla os 20 slots (novo ICAO, ICAO repetido, slot velho liberado). |
| `hermes_simples/plane.py` | Monta o registro de 204 bytes. |
| `hermes_simples/uart.py` | Escreve os 4080 bytes na serial. |
| `hermes_simples/gpio.py` | Lê ON/OFF e controla a energia do SDR. |
| `hermes_simples/decoder.py`, `decoder_process.py` | Ligam e vigilam o `dump1090-fa`. |
| `hermes_simples/mission_log.py` | Escreve o CSV de evidência. |
| `tests/` | Testes automáticos do lado Pi (`python3 -m unittest ...`). |
| `stm32_test/` | Testes C do receptor (simulam byte a byte no computador). |
| `obc_integration/` | **Lado OBC**: receptor por DMA, configuração e patch para o firmware. |
| `teste_uart/` | Console para olhar os bytes chegando na serial. |
| `services/`, `scripts/` | Arquivo do systemd e desligamento seguro do SDR. |
| `*.o`, `__pycache__/`, `test_receiver` | Gerados pelos testes. Não são código-fonte. |

## 4. Como saber se está funcionando agora

```bash
systemctl is-active hermes-simples hermes-payload
# esperado: active / inactive

journalctl -u hermes-simples -n 50 --no-pager
tail -5 ~/hermes-simples-mission.csv
tail -20 ~/hermes-simples-decoder.log
ls -l /run/dump1090-fa/aircraft.json
```

Se a última linha do CSV tiver `aircraft_count` e `messages_total` maiores que
zero, houve recepção e decodificação de verdade. Se estiver tudo zerado, pode
não haver aviões sobrevoando — isso já aconteceu aqui e não era falha da UART.

## 5. Testes que não precisam de SDR nem de OBC

```bash
cd ~/hermes/atual
python3 -m unittest discover -s tests -v
cd stm32_test && make test
python3 teste_uart/console_uart.py --selftest
```

Simulação de 20 aviões por 12 s:

```bash
python3 -m hermes_simples.main --sim --sim-aircraft 20 --force-on \
    --port /tmp/ttyA --duration 12 --log /tmp/missao-teste.csv
```

## 6. Onde mexer para cada tipo de mudança

| Quero mudar... | Arquivo |
| --- | --- |
| pino de ON/OFF/SDR, baud, período de 5 s, tempo de slot | `hermes_simples/config.py` |
| campos do avião / tamanho do registro | `hermes_simples/plane.py` + `PROTOCOLO.md` (e o `hermes_protocol.h` do OBC) |
| comportamento da missão | `hermes_simples/mission.py` |
| caminho dos logs do serviço | `services/hermes-simples.service` ( depois `sudo systemctl daemon-reload && sudo systemctl restart hermes-simples`) |
| UART, DMA, IRQ ou timeout **no OBC** | `obc_integration/hermes_config.h` — não editar a lógica em `hermes_adsb.c` |
| nome de task/fila/evento do firmware | `obc_integration/PATCHES.md`, item “Se o OBC mudou” |

## 7. E se o OBC do GitHub não for mais o mesmo?

O formato do arame não depende do repositório do OBC: continua sendo **4080
bytes crus, 20 × 204, 115200 8N1**. O que pode mudar é *por qual UART* o
firmware recebe. Por isso todas essas escolhas foram concentradas em
`obc_integration/hermes_config.h` (valores padrão = OBC examinado). Antes de
aplicar o patch a um OBC diferente, siga o checklist
“Se o OBC mudou: checklist de adaptação” em `obc_integration/PATCHES.md`.
Se o código do OBC mudou muito, os trechos de `main.c`/`stm32l4xx_it.c` do
patch são exemplos dentro de blocos `USER CODE` — copie a ideia, não substitua
arquivos inteiros.

## 8. Se algo der errado

```bash
# voltar ao projeto antigo (completo):
sudo systemctl disable --now hermes-simples
sudo systemctl enable --now hermes-payload
```

Não apague `~/hermes/antigo`: ele é o caminho de volta.

## 9. Glossário pequeno

- **OBC**: o computador de bordo (STM32), que manda LIGAR e recebe os dados.
- **dump1090-fa**: programa que decodifica sinais ADS-B e gera o `aircraft.json`.
- **ICAO**: identificador único do avião (24 bits).
- **slot**: uma das 20 posições fixas do buffer. Slot vazio tem ICAO = 0.
- **`met_s`**: segundos desde que a missão foi ligada; `met_s - seen` é o
  instante da mensagem ADS-B.
- **`Plane_t`**: o registro de 204 bytes de um avião.
- **GPIO ON/OFF**: fios físicos do Pi que ligam/desligam a missão.
- **DMA**: forma de o STM32 receber o bloco inteiro sem uma interrupção por byte.

## 10. Regras de ouro

1. Nunca edite `/opt/hermes-simples` direto — edite `~/hermes/atual` e copie.
2. A UART não tem cabeçalho, CRC nem sequência: **4080 bytes seguidos**.
3. O nível elétrico é **3,3 V TTL**, nunca RS-232 e nunca 5 V.
4. `Telemetry_t` do OBC continua com 85 bytes; não mexer nela.
5. Sem aviões no céu não há evidência de recepção real — use a simulação para
   verificar o caminho completo e guarde o CSV como prova do teste.
