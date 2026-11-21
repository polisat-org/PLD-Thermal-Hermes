# TCS: controle térmico das baterias do Hermes

| | |
|---|---|
| **Versão** | 0.3.0 (20/11/2026) |
| **Estado** | testado em simulação e no Pi **só em modo de observação** (`--sem-pwm`); PWM real e serviço ainda não testados |
| **Roda em** | Raspberry Pi Zero W (Raspberry Pi OS Bookworm 32 bits), Python 3 só com a biblioteca padrão + `pigpio` |
| **Histórico** | [seção 9](#9-histórico-de-versões) |

Mantém os 2 blocos de baterias do Hermes em **20 °C** com os aquecedores resistivos, durante o ensaio térmico
(CubeDesign 2026). É um programa **separado** do payload ADS-B deste repositório: não importa nada de `hermes/`.

## Sumário

1. [Estrutura](#1-estrutura)
2. [Hardware](#2-hardware)
3. [Como funciona](#3-como-funciona)
4. [Ligar e desligar no Pi](#4-ligar-e-desligar-no-pi)
5. [Log e códigos de situação](#5-log-e-códigos-de-situação)
6. [Calibração das sondas](#6-calibração-das-sondas)
7. [Teste no PC (software-in-the-loop)](#7-teste-no-pc-software-in-the-loop)
8. [Relação com `hermes/thermal`](#8-relação-com-hermesthermal)
9. [Histórico de versões](#9-histórico-de-versões)
10. [Pendências](#10-pendências)

## 1. Estrutura

```text
controle-termico-bateria/
  firmware/                  o que vai para o Pi (no Pi: ~/tcs/)
    tcs/                     pacote do controlador (python3 -m tcs)
      __main__.py            entrada e opções da linha de comando
      config.py              TODOS os ajustes: referência, corte, modo degradado, pinos, período, PWM
      controlador.py         o ciclo: lê -> decide -> PWM -> log
      lei.py                 lei de controle (LQI, PI, liga/desliga) e proteções; função pura
      sondas.py              leitura do 1-Wire (/sys/bus/w1/devices) e calibração
      pwm.py                 PWM por hardware via pigpiod (ou em memória, com --sem-pwm)
      lqi_ganhos_1s.json     ganhos do LQI, laço de 1 s (gerados no PC; não editar à mão)
      lqi_ganhos_5s.json     ganhos do LQI, laço de reserva de 5 s
    servico/tcs.service      unidade systemd (NÃO instalada): reinício, watchdog de 10 s, zera PWM ao parar
    calibracao.py            ferramenta da calibração (mostrar, ligar/desligar a correção)
    calibracao.json          correção de cada sonda (a, b) e a chave "ativa"
    sondas.json              número de cada sonda (1 a 4), ROM e posição no módulo
    w1_coletor.py            leitura simples das sondas (--listar, --csv, --varrer)
    teste_local/             o mesmo código do Pi rodando no PC contra um satélite simulado
      planta.py              modelo térmico de 8 nós + "kernel" 1-Wire falso (escreve w1_slave)
      bancada.py             liga o tcs à planta, ciclo a ciclo
      testar_lqi.py          conferência completa antes de levar ao Pi (tem de dar APROVADO)
      servidor.py, pagina.html   página local para provocar falhas e ver o controle (só no próprio PC)
  03-sistema-modelo/         só no PC (numpy/scipy): modelo e cálculo dos ganhos
    rede_termica.py          rede térmica de 8 nós do módulo de baterias
    controle.py              LQI de referência (o firmware tem de bater com ele)
    gerar_ganhos_lqi.py      gera tcs/lqi_ganhos_<T>s.json
```

A árvore repete a do projeto do TCS para os caminhos relativos entre `firmware/` e `03-sistema-modelo/`
continuarem valendo sem mudar o código.

## 2. Hardware

| Bloco | Sondas DS18B20 (1-Wire, GPIO 4) | Aquecedor (MOSFET, PWM por hardware 1 kHz) |
|---|---|---|
| A | 1 e 2 | GPIO 18 (canal PWM 0) |
| B | 3 e 4 | GPIO 19 (canal PWM 1) |

- Pinos 18/19 e um resistor de 82 Ω por bloco confirmados pela equipe em 13/11/2026. Potência máxima por bloco:
  13,8² / 82 = 2,32 W.
- Número da sonda = furo no módulo = fileira na grade do OBC (padrão da equipe, 06/11/2026); ROMs em `sondas.json`.
- Barramento 1-Wire: `dtoverlay=w1-gpio` (GPIO 4) no `config.txt`. PWM: serviço `pigpiod` ativo.
- Convive com o `hermes-simples` (GPIO 27/22/17 e UART): nenhum pino em comum.

## 3. Como funciona

A cada **1 s**:

1. converte as 4 sondas ao mesmo tempo (`therm_bulk_read`, ~0,75 s; precisa de root);
2. corrige cada leitura pela calibração;
3. calcula o duty de cada bloco (0 a 1) com o **LQI com observador de Kalman em regime**, pela sonda **mais fria**
   do bloco, para a referência de 20 °C (só multiplicação de matrizes 4 × 4; os ganhos vêm prontos do PC);
4. aplica as proteções, em `lei.decidir`:
   - **corte por temperatura alta:** a sonda **mais quente** do bloco chegou a **30 °C** → aquecedor em 0, até ela
     cair abaixo de **29 °C** (histerese);
   - **sem sonda:** nenhuma sonda válida no bloco → aquecedor em 0;
   - **modo degradado** (o bloco ficou com uma sonda só): aquecedor em 0 se ela ler mais de **5 °C** abaixo da média
     do outro bloco; **travado** em 0 até reiniciar o programa se o duty ficar ≥ 90 % por 10 min sem a sonda subir
     0,5 °C (sonda fora do canal, aquecedor solto);
5. ajusta o PWM e grava uma linha no log.

**Reserva:** se a conversão simultânea falhar 3 vezes seguidas (ou o programa rodar sem root), passa a ler uma sonda
por vez com período de **5 s** e troca para os ganhos de 5 s, sem parar o controle.

**Ao parar** por qualquer motivo (Ctrl+C, erro, `systemctl stop`), os dois aquecedores vão a 0.

Leitura válida: CRC "YES", entre −55 e 125 °C e diferente de 85 °C de partida.

## 4. Ligar e desligar no Pi

No Pi a pasta `firmware/` fica em `~/tcs/`. Não existe chave `true`/`false`: o controle roda **enquanto o comando
estiver aberto**.

```bash
cd ~/tcs
sudo python3 -m tcs --sem-pwm             # SÓ OBSERVA: lê e decide, não mexe nos pinos
sudo python3 -m tcs --sem-pwm --ref 35    # observa forçando a lei a querer aquecer (teste do corte com a mão)
sudo python3 -m tcs                       # LIGADO DE VERDADE: PWM nos GPIO 18/19
```

- **Desligar:** Ctrl+C → `tcs: interrompido (Ctrl+C); aquecedores em 0, log fechado.`
- Opções: `--ref 25` (referência em °C); `--lei pi` ou `--lei liga_desliga` (padrão `lqi`).
- `sudo` é necessário para a conversão simultânea; sem ele o programa avisa e roda a cada 5 s.
- **Cuidado:** sem `--sem-pwm` e com a fonte dos MOSFETs ligada, **aquece de verdade**. Não deixar ligado sem alguém
  olhando até os testes de segurança da [seção 10](#10-pendências) serem feitos.
- Ao ligar a fonte dos aquecedores (com ou sem programa), conferir que ficam frios. Se esquentarem sem o programa,
  algo puxa o gate do MOSFET para cima: desligar a fonte.

Atualizar o Pi a partir deste repositório (depois de o teste da [seção 7](#7-teste-no-pc-software-in-the-loop) dar
APROVADO):

```bash
scp -r firmware/tcs firmware/servico firmware/*.py firmware/*.json polisat@<ip-do-pi>:tcs/
```

**Serviço (ainda não instalado; só com acordo da equipe):** `servico/tcs.service` é `Type=notify` com `WatchdogSec=10`
e `Restart=on-failure`; ao parar, zera os PWMs (`pigs hp 18/19 0 0`) e põe os pinos em saída baixa (`pinctrl`).
Instruções de instalação nos comentários do próprio arquivo.

## 5. Log e códigos de situação

Um CSV por execução em `~/tcs/logs/tcs-AAAAMMDD-HHMMSS.csv`, gravado a cada ciclo (`fsync` a cada 30 s). Na tela,
um resumo a cada 15 s.

| Coluna | O que é |
|---|---|
| `t_s` | relógio do laço (s, monotônico) |
| `hora` | relógio de parede, ISO com milissegundos e fuso (para alinhar com outros logs) |
| `sondaN_bruta` | leitura da sonda N sem correção (°C); vazio = falhou |
| `sondaN` | leitura corrigida (°C) |
| `duty_A`, `duty_B` | fração de potência aplicada (0 a 1) |
| `situacao_A`, `situacao_B` | soma dos códigos abaixo (0 = normal) |

| Código | Situação | Efeito |
|---|---|---|
| 1 | sem sonda | aquecedor em 0 |
| 2 | corte por temperatura alta (entra em 30 °C, sai abaixo de 29 °C) | aquecedor em 0; marcado só quando a lei queria aquecer |
| 4 | uma sonda falhou | segue com a outra (modo degradado) |
| 8 | sonda sem calibração | usa a leitura bruta |
| 16 | leitura lenta (sem conversão simultânea) | período de 5 s |
| 32 | sonda restante discorda do outro bloco | aquecedor em 0 |
| 64 | aquecedor sem resposta (travado) | aquecedor em 0 até reiniciar o programa |

Exemplo: `6` = 4 + 2 (uma sonda falhou e o corte agiu).

## 6. Calibração das sondas

Correção por sonda: **T = (leitura − b) / a**, com `a` e `b` em `calibracao.json`, medidos no gelo fundente (0 °C)
em 08/11 e 17/11/2026. Hoje só offset (a = 1): +0,335 / +0,248 / +0,373 / +0,309 °C nas sondas 1 a 4. Incerteza
depois de corrigir: ±0,02 °C em 0 °C e ±0,12 °C em 20 °C.

```bash
python3 calibracao.py              # bruto -> calibrado, ao vivo (Ctrl+C para parar)
python3 calibracao.py --mostrar    # só a tabela
python3 calibracao.py --estado     # correção ligada ou desligada?
python3 calibracao.py --desligar   # usa a leitura bruta (troca só "ativa" no calibracao.json)
python3 calibracao.py --ligar      # volta a corrigir
python3 w1_coletor.py --listar     # acha as sondas no barramento
```

Sonda nova ou trocada: calibrar no gelo e atualizar `calibracao.json` e `sondas.json` (a ferramenta e os dados da
bancada ficam no projeto do TCS).

## 7. Teste no PC (software-in-the-loop)

O código do Pi roda **sem mudanças** no PC: `planta.py` simula o satélite (modelo de 8 nós) e escreve os mesmos
`w1_slave` que o kernel escreve no Pi; o PWM vira potência no modelo. Precisa de Python 3.11+, `numpy` e `scipy`.

```bash
cd controle-termico-bateria/firmware
python -I teste_local/testar_lqi.py        # ~15 min; tem de terminar em APROVADO
python -I teste_local/bancada.py sala lqi  # um ensaio inteiro, imprime o resumo
python -I teste_local/servidor.py          # página em http://127.0.0.1:8765 para provocar falhas
```

`testar_lqi.py` confere: firmware = LQI de referência (diferença < 10⁻⁹ em 400 passos, 1 s e 5 s); ensaio inteiro
contra o modelo; reserva de 5 s; sonda solta; corte com e sem histerese; queda da conversão simultânea; falha dupla
com e sem o outro bloco como referência; e ausência de alarme falso com uma sonda boa. Resultado em
`teste_local/logs/testar_lqi.json`.

**Regra:** nenhuma mudança vai ao Pi sem esse teste dar APROVADO. Mudou o modelo do bloco ou o período? Refazer os
ganhos: `python 03-sistema-modelo/gerar_ganhos_lqi.py` (1 s e 5 s).

## 8. Relação com `hermes/thermal`

O pacote `hermes/thermal` deste repositório (com `hermes/pwm.py` e `docs/HERMES_Thermal_Control.md`) é o controle
térmico da versão 0.1.0 do payload. O TCS **não reaproveita nem altera** esse código; as diferenças principais:

| | `hermes/thermal` | TCS (`controle-termico-bateria`) |
|---|---|---|
| Pinos dos aquecedores | GPIO 12/13 | GPIO 18/19 (confirmados pela equipe em 13/11/2026) |
| Corte | 45 °C | 30 °C, libera abaixo de 29 °C |
| Lei | política própria | LQI com observador (alternativas PI e liga/desliga) |
| Sondas | sem calibração; mapa automático pela ordem da ROM | calibradas; mapa fixo por número (`sondas.json`) |

**Não rodar os dois juntos.** No Pi, o serviço `hermes-payload` (que usa `hermes/thermal`) está parado e desligado
no boot; o que roda é o `hermes-simples`, sem parte térmica.

## 9. Histórico de versões

As siglas (T = teste, D = decisão, A = achado) remetem ao registro do projeto do TCS (etapa 03, sistema e modelo).

| Versão | Data | Mudanças |
|---|---|---|
| 0.3.0 | 20/11/2026 | Revisão do firmware (T03-11): coluna `hora` e `fsync` no log; passagem automática para leitura lenta de 5 s se a conversão simultânea parar (código 16); **modo degradado** com os códigos 32 e 64 (D03-05); **histerese do corte** 30/29 °C (D03-06); Ctrl+C sem erro; serviço zera os pinos também pelo `pinctrl`. 1ª execução no Pi, em `--sem-pwm`: 4 sondas a 1 Hz; corte conferido segurando as sondas na mão |
| 0.2.0 | 16/11/2026 | **LQI com observador** no lugar do PI (D03-02, T03-05); laço de 1 s com conversão simultânea e reserva de 5 s; corte em 30 °C pela sonda mais quente; serviço systemd com watchdog (D03-04, T03-06) |
| 0.1.0 | 12/11/2026 | Pacote `tcs` (liga/desliga e PI, laço de 5 s), calibração das sondas e teste local contra o modelo térmico |

Ao mudar o código: subir a versão aqui (MAJOR.MINOR.PATCH), dizer o que mudou e rodar a [seção 7](#7-teste-no-pc-software-in-the-loop).

## 10. Pendências

- **Testes de segurança no Pi sem aquecedor** (GPIO 18/19 soltos ou fonte dos MOSFETs desligada): PWM ligado, parada
  normal, `kill -9`, `kill -STOP` (watchdog) e `pigpiod` parado, conferindo os pinos com `pigs gdc 18` e
  `pinctrl get 18,19`. Atenção: parar o `pigpiod` afeta o `hermes-simples`.
- **Eletrônica a confirmar:** MOSFET de nível lógico (gate a 3,3 V); se há pull-up no gate; termostato ou fusível
  térmico por aquecedor.
- **Ganhos com o atraso medido:** refazer o LQI depois do primeiro teste com aquecedor.
- **Instalação do serviço** e do watchdog de hardware: só com acordo da equipe.
