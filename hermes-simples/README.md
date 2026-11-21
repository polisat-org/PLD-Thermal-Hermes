# HERMES Simples

**Primeira leitura:** [`COMECE_AQUI.md`](COMECE_AQUI.md) — mapa do projeto no
Raspberry Pi, com “onde está cada coisa”, comandos de verificação e glossário.
É escrito para quem nunca viu o projeto antes.

Versão minimalista da carga útil. O projeto completo continua em
`~/hermes/antigo` (antigo `PLD-Thermal-Hermes`).

Protocolo: [`PROTOCOLO.md`](PROTOCOLO.md). Resumo: a cada 5 s o Pi escreve na
UART um bloco cru de **4080 bytes** = 20 aviões × 204 bytes. Sem cabeçalho,
sem CRC, sem TIME_SYNC, sem ACK. Cada avião carrega `icaoID` e `met_s` como
dois primeiros floats; `met_s - seen` dá o instante da mensagem ADS-B.

## Estrutura

```
COMECE_AQUI.md   guia inicial: onde está cada coisa no Raspberry Pi
PROTOCOLO.md     formato dos 4080 bytes, pinos, slots e timestamps
hermes_simples/
  config.py       pinos, baud, períodos, TTL de slot
  plane.py        Plane_t (204 bytes) e buffer de 4080 bytes
  aircraft.py     lista de 20 posições + purge + leitura do aircraft.json
  uart.py         escrita bruta na serial
  gpio.py         entradas ON/OFF e saída do SDR (pigpio)
  decoder.py      casca fina start/stop do dump1090-fa
  decoder_process.py  supervisor reaproveitado de VERSAO_ANTIGA_HERMES
  mission.py      máquina de estados mínima
  mission_log.py  CSV local de evidência da missão
  main.py         ponto de entrada
tests/            testes Python (unittest)
stm32_test/       modelo C do receptor por IT de RX + testes host (gcc)
obc_integration/  receptor real para o OBC (configurável) + patch pronto
teste_uart/       console interativa para testar a serial
scripts/          safe_off.py (ExecStopPost)
services/         hermes-simples.service
```

## Compatibilidade com o OBC

O receptor do OBC está em [`obc_integration/`](obc_integration/), com
`PATCHES.md` e o patch pronto `OBC-hermes.patch`. O patch foi escrito para o
`polisat-org/OBC` (`functionalCodes/stateMachineVSCode`), **mas o protocolo não
depende dessa versão do repositório**: UART, DMA, IRQs e tempos ficam todos em
[`obc_integration/hermes_config.h`](obc_integration/hermes_config.h).
Os valores padrão usam USART3 (PC4/PC5) + DMA1_Channel3/request 2 porque aquele
OBC roda a 4 MHz (MSI sem PLL) — uma IT por byte a 115200 seria impossível.
USART1 (telecomandos), USART2 (debug/telemetria) e o `Telemetry_t` de 85 bytes
não são alterados. Se o OBC mudou, veja o checklist em
`obc_integration/PATCHES.md` antes de aplicar o patch.

## Rodar os testes

```bash
cd ~/hermes/atual
python3 -m unittest discover -s tests -v
cd stm32_test && make test
python3 teste_uart/console_uart.py --selftest
```

## Bancada (sem SDR e sem GPIO reais)

```bash
# 20 aviões, 12 s, capturando num pty e gerando log CSV
python3 -m hermes_simples.main --sim --sim-aircraft 20 --force-on \
    --port /tmp/ttyA --duration 12 --log /tmp/missao-teste.csv
```

Com `socat pty,raw,echo=0,link=/tmp/ttyA pty,raw,echo=0,link=/tmp/ttyB`, use
`teste_uart/console_uart.py --port /tmp/ttyB` para ver os 4080 bytes chegando.

## Verificação de 10 minutos

```bash
python3 -m hermes_simples.main --sim --sim-aircraft 20 --force-on \
    --no-uart --duration 600 --log ~/hermes/resultados/missao-10min.csv
cat ~/hermes/resultados/missao-10min.csv
```

Com SDR plugado, tire `--sim` e `--force-on`: o gpio ON inicia a missão. O CSV
registra `messages_total` (do `aircraft.json`), contagem de aviões e buffers
enviados a cada 5 s.

## Instalar como serviço

```bash
sudo systemctl stop hermes-payload hermes-simples 2>/dev/null || true
sudo mkdir -p /opt/hermes-simples
sudo cp -r hermes_simples scripts /opt/hermes-simples/
sudo cp services/hermes-simples.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now hermes-payload
sudo systemctl enable --now hermes-simples
journalctl -u hermes-simples -f
```

O serviço define `MISSION_LOG_PATH=/home/polisat/hermes-simples-mission.csv`
e `DECODER_LOG_PATH=/home/polisat/hermes-simples-decoder.log` para os logs não
caírem em `/root` (systemd roda como root). O `RuntimeDirectory=dump1090-fa`
cria `/run/dump1090-fa`, como no projeto antigo. Manualmente, os padroes sao
`~/hermes-simples-*.csv`, alteraveis com `--log` e com a variavel de ambiente.
Para voltar ao projeto completo:

```bash
sudo systemctl disable --now hermes-simples
sudo systemctl enable --now hermes-payload
```
