> **Substituído (21/11/2026).** A integração com o OBC que está valendo fica no repositório `polisat-org/OBC`,
> branch `payload_integration` (`functionalCodes/stateMachineInVSCode`): USART3 em PB10/PB11, comando
> LIGAR/DESLIGAR pela UART (sem GPIO), bloco de 4112 B (4080 + trailer térmico de 32 B) e downlink `'P'`/`'H'`.
> Os arquivos abaixo ficam como referência do primeiro patch (PC4/PC5, 4080 B, GPIO).

# Integração HERMES ADS-B no OBC (`stateMachineVSCode`)

Todos os trechos abaixo vão dentro de blocos `USER CODE`, para sobreviver a uma
regeneração pelo CubeMX. O alvo é exatamente o repo
`polisat-org/OBC`, pasta `functionalCodes/stateMachineVSCode`.

**Aplicação automática (opcional):** `OBC-hermes.patch` contém exatamente as
mesmas alterações. Na raiz de `functionalCodes/stateMachineVSCode`:

```bash
git apply --ignore-whitespace /caminho/para/OBC-hermes.patch
```

O `--ignore-whitespace` é necessário porque o projeto do OBC mistura CRLF e LF.
O patch foi validado: aplica limpo e todos os `.c` modificados passam em
`gcc -fsyntax-only`. Se preferir, aplique manualmente com os trechos abaixo.

Antes de editar, copie para o projeto do OBC:

```
Core/Inc/hermes_config.h      <- obc_integration/hermes_config.h
Core/Inc/hermes_adsb.h        <- obc_integration/hermes_adsb.h
Core/Inc/hermes_protocol.h    <- obc_integration/hermes_protocol.h
Core/Src/hermes_adsb.c        <- obc_integration/hermes_adsb.c
Core/Src/hermes_protocol.c    <- obc_integration/hermes_protocol.c
```

**Não presuma que o OBC é sempre o mesmo repositório/versão.** O protocolo
(4080 bytes crus, 20 aviões, 115200 8N1) não muda; *onde* o OBC recebe esses
bytes muda. Toda escolha de UART, DMA, IRQ e tempo está em `hermes_config.h`,
com valores padrão que reproduzem o `stateMachineVSCode` examinado. Se o OBC
estiver diferente, ajuste **só esse arquivo** (ou use `-D...` no compilador).

## Por que DMA (e não IT por byte)

No OBC examinado: USART1 (PA9/PA10) = telecomandos, USART2 (PA2/PA3) = debug e
telemetria, USART3 (PC4/PC5) = inicializada, sem uso → vira o RX do payload.
O `SystemClock_Config()` usa MSI a **4 MHz**; a 115200 8N1, uma interrupção por
byte seriam 115200 IRQs/s — inviável. O DMA faz o transporte e só uma IRQ por
bloco de 4080 bytes avisa a task. Não há polling. Se o OBC atual tiver outro
clock ou outra UART livre, mantenha o princípio e mude os macros.

O `Plane_t` continua com 204 bytes (sem `__attribute__((packed))`; o layout é
o mesmo do `struct.pack('<8s8s45fII')` do lado Pi) e o buffer continua com
20 × 204 = 4080 bytes. `Telemetry_t` **não é alterado**: o
`_Static_assert(sizeof(Telemetry_t) == 85U)` e o `TELEMETRY_FORMAT` do
`uart.py` continuam válidos.

## Se o OBC mudou: checklist de adaptação

1. **Clock:** se não for mais MSI 4 MHz, confirme que a UART consegue 115200 e
   que `HERMES_ADSB_IRQ_PRIORITY` é ≥ `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`.
2. **UART/pinos:** escolha uma UART livre; em `hermes_config.h` troque
   `HERMES_ADSB_UART_INSTANCE`, `HERMES_ADSB_UART_HANDLE`, `HERMES_ADSB_UART_IRQn`.
   Configure RX/TX no CubeMX e troque o `MX_USARTx_UART_Init()` e o nome do
   handler nos trechos de `main.c` / `stm32l4xx_it.c`.
3. **DMA:** troque `HERMES_ADSB_DMA_CLK_ENABLE`, `HERMES_ADSB_DMA_INSTANCE`,
   `HERMES_ADSB_DMA_REQUEST` e `HERMES_ADSB_DMA_IRQn` em conjunto, conforme a
   tabela de request do reference manual do seu modelo. O nome da função
   `..._IRQHandler` em `stm32l4xx_it.c` deve bater com o vetor do startup.
4. **RTOS:** se os nomes de tasks/fila/flags mudaram, ajuste
   `hermesTaskHandle`, `eventQueueHandle`, `EVT_HERMES_DONE`,
   `HERMES_ADSB_FLAG` e `StartMainTask` nos trechos abaixo.
5. **Heap/stack:** com 9 tasks e `configUSE_NEWLIB_REENTRANT`, meça
   `xPortGetFreeHeapSize()`; se sobrar pouco, aumente `configTOTAL_HEAP_SIZE`.
6. **Nada de USART1/USART2:** se o novo OBC usa alguma delas para telemetria ou
   telecomando, escolha outra UART — a integração não deve sequer registrar
   callback nelas.

---

## 1. `cmake/files.cmake`

As fontes são listadas nesse arquivo (não no `CMakeLists.txt`). Acrescente
dentro do `target_sources(...)`:

```cmake
    "${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/hermes_adsb.c"
    "${CMAKE_CURRENT_SOURCE_DIR}/Core/Src/hermes_protocol.c"
```

> `Core/Inc` já está em `cmake/flags.cmake`, não precisa mexer lá.
> `stm32l4xx_hal_dma.c` e `stm32l4xx_hal_uart_ex.c` já estão listados.

> **DMA request:** o STM32L476 não tem DMAMUX, então `Init.Request` é o valor
> de seleção de canal (0..7) do `DMA_CSELR`, não um símbolo `DMA_REQUEST_USART3_RX`
> (que nem existe neste pacote HAL). Conforme RM0351 Table 38, USART3_RX é
> **DMA1 Channel3 / Request 2** (`DMA_REQUEST_2`). O arquivo
> `hermes_adsb.c` já usa esse valor.

## 2. `Core/Inc/customTypes.h`

Em `EventType_t`, acrescente ao final:

```c
	EVT_HERMES_DONE,
```

Antes de `EventData_t`, defina o resumo que vai na fila de eventos:

```c
typedef struct {
	uint32_t sequence;   /* numero do bloco HERMES */
	uint32_t count;      /* avioes ativos no bloco */
	uint32_t met_ms;     /* timestamp de missao em ms */
	int32_t  icao;       /* primeiro aviao ativo (-1 se nenhum) */
} HermesSummary_t;
```

Dentro de `EventData_t`, acrescente:

```c
	HermesSummary_t hermes;
```

> Isso aumenta `sizeof(Event_t)` (de 12 para 24 bytes) e o `eventQueue` de
> 16×12 para 16×24 = 384 bytes, mas **não** muda `sizeof(Telemetry_t)` (85).
>
> O heap FreeRTOS é apertado: `configTOTAL_HEAP_SIZE = 8192`, com 9 tasks
> (a nova `hermesTask` usa 128 palavras = 512 bytes, o mesmo das outras) e
> `configUSE_NEWLIB_REENTRANT = 1` (cada TCB carrega um `struct _reent`).
> Meça antes e depois com `xPortGetFreeHeapSize()` via `debugPrintf()` na
> `StartMainTask`. Se sobrar menos de ~500 bytes, aumente
> `configTOTAL_HEAP_SIZE` para 10240. O array de 4080 bytes da task é
> `static` (`.bss`), não entra na stack nem no heap.

## 3. `Core/Src/main.c`

### 3.1 Includes

```c
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <stdarg.h>
#include <stdbool.h>
#include "customTypes.h"
#include "BME280.h"
#include "DS3231.h"
#include "hermes_adsb.h"     /* NOVO */
/* USER CODE END Includes */
```

### 3.2 Task

Depois da definição de `missionTask`:

```c
/* Definitions for hermesTask */
osThreadId_t hermesTaskHandle;
const osThreadAttr_t hermesTask_attributes = {
  .name = "hermesTask",
  .stack_size = 128 * 4,
  .priority = (osPriority_t) osPriorityLow,
};
```

### 3.3 Protótipo

```c
/* USER CODE BEGIN PFP */
static void debugPrintf(const char *format, ...);
static void handleTelecommand(uint8_t tc, SatMode_t *mode,
		bool *deployInProgress, volatile bool *deployCompleted);
void StartHermesTask(void *argument);   /* NOVO */
/* USER CODE END PFP */
```

### 3.4 Braçar o DMA (USER CODE 2, depois do `MX_USARTx_UART_Init()`)

```c
  /* USER CODE BEGIN 2 */
  HermesAdsb_Init(&HERMES_ADSB_UART_HANDLE, NULL);  /* a task registra depois */
  /* USER CODE END 2 */
```

> O DMA já é configurado dentro de `HAL_UART_MspInit()` (item 5), então
> `HermesAdsb_Init()` só arma a primeira recepção.

### 3.5 Criar a task (USER CODE BEGIN RTOS_THREADS)

```c
  /* USER CODE BEGIN RTOS_THREADS */
  hermesTaskHandle = osThreadNew(StartHermesTask, NULL, &hermesTask_attributes);
  /* USER CODE END RTOS_THREADS */
```

### 3.6 Callbacks

Em `HAL_UART_RxCpltCallback`, **depois** do bloco `#if TC_ON_USART2 ... #endif`:

```c
    else if (huart->Instance == HERMES_ADSB_UART_INSTANCE)
    {
        HermesAdsb_RxCpltCallback(huart);
    }
```

Em `HAL_UART_ErrorCallback`, no mesmo lugar:

```c
    else if (huart->Instance == HERMES_ADSB_UART_INSTANCE)
    {
        HermesAdsb_ErrorCallback(huart);
    }
```

### 3.7 Implementação da task (USER CODE, depois de `StartMissionTask`)

```c
void StartHermesTask(void *argument)
{
    static Plane_t planes[HERMES_NUM_PLANES];   /* estatico: 4080 bytes fora da stack */

    HermesAdsb_SetNotifyTask(osThreadGetId());

    for (;;)
    {
        /* Drena fila antes de dormir: cobre o caso de a notificacao da ISR
         * ter chegado antes de a task registrar seu osThreadId. */
        while (HermesAdsb_Take(planes))
        {
            HermesSummary_t sum = {0};
            uint32_t i;

            sum.sequence = HermesAdsb_Stats()->buffers;
            sum.icao = -1;

            for (i = 0U; i < HERMES_NUM_PLANES; i++)
            {
                if (planes[i].icaoID > 0.0f)
                {
                    if (sum.icao < 0) {
                        sum.icao = (int32_t)planes[i].icaoID;
                        sum.met_ms = (uint32_t)(planes[i].met_s * 1000.0f);
                    }
                    sum.count++;
                }
            }

            Event_t evt = {
                .type = EVT_HERMES_DONE,
                .timestamp = osKernelGetTickCount(),
                .data.hermes = sum,
            };
            /* Nao travar a task se a fila estiver cheia: o timeout de resync
             * precisa continuar sendo avaliado. */
            (void)osMessageQueuePut(eventQueueHandle, &evt, 0U,
                                    HERMES_ADSB_QUEUE_TIMEOUT_MS);
        }

        /* Acorda na chegada do bloco; o polling garante o resync. */
        (void)osThreadFlagsWait(HERMES_ADSB_FLAG, osFlagsWaitAny,
                                HERMES_ADSB_TASK_POLL_MS);
        HermesAdsb_CheckTimeout(osKernelGetTickCount());
    }
}
```

### 3.8 Main task: tratar o evento

Dentro do `switch (evt.type)` de `StartMainTask`, antes do `default`:

```c
		case EVT_HERMES_DONE:
			debugPrintf("[%08lu] ads-b seq=%lu n=%lu icao=%06lX met_ms=%lu\r\n",
					evt.timestamp,
					(unsigned long)evt.data.hermes.sequence,
					(unsigned long)evt.data.hermes.count,
					(unsigned long)evt.data.hermes.icao,
					(unsigned long)evt.data.hermes.met_ms);
			break;
```

> Usei só inteiros para não exigir `-u _printf_float` no newlib.

## 4. `Core/Src/stm32l4xx_it.c`

Includes:

```c
/* USER CODE BEGIN Includes */
#include "hermes_adsb.h"
/* USER CODE END Includes */
```

Variáveis externas:

```c
/* USER CODE BEGIN EV */
extern UART_HandleTypeDef huart2;
extern UART_HandleTypeDef HERMES_ADSB_UART_HANDLE;   /* NOVO */
/* USER CODE END EV */
```

Handlers (em `/* USER CODE BEGIN 1 */`):

```c
void USART3_IRQHandler(void)            /* nome do vetor: acompanha o .ioc */
{
  HAL_UART_IRQHandler(&HERMES_ADSB_UART_HANDLE);
}

void DMA1_Channel3_IRQHandler(void)    /* nome do vetor: acompanha o DMA */
{
  HermesAdsb_DmaIrqHandler();
}
```

> Se a UART ou o canal DMA mudou, troque também os **nomes** dos handlers
> acima: eles precisam coincidir com os vetores do arquivo de startup.

## 5. `Core/Src/stm32l4xx_hal_msp.c`

Includes:

```c
/* USER CODE BEGIN Includes */
#include "hermes_adsb.h"
/* USER CODE END Includes */
```

Dentro do ramo da UART escolhida em `HAL_UART_MspInit()` (aqui, USART3):

```c
    /* USER CODE BEGIN USART3_MspInit 1 */
    HermesAdsb_InitDma(huart);
    /* USER CODE END USART3_MspInit 1 */
```

> `HAL_UART_Init()` chama `HAL_UART_MspInit()` antes do `USER CODE 2`,
> então o DMA já está pronto quando `HermesAdsb_Init()` arma a recepção.

---

## Wiring (valores padrão; ajuste se o OBC mudou)

| Pi (GPIO) | OBC examinado (STM32L476RG) | Sinal |
|---|---|---|
| pin 8 (TX) | **PC5** (USART3_RX) | payload 115200 8N1 |
| pin 10 (RX) | PC4 (USART3_TX) | opcional (OBC → Pi) |
| pin 6 (GND) | GND | comum |

Se a placa nova usar outra UART, mude apenas o mapeamento físico e os macros
em `hermes_config.h`; o formato do stream continua idêntico.

**Atenção:** os níveis do Pi são 3.3 V TTL, compatíveis com o STM32L476
(3.3 V). Não usar nível RS-232.

## Verificação

1. Compilar; conferir `_Static_assert(sizeof(Plane_t) == 204)` e
   `sizeof(Telemetry_t) == 85`. Se trocou algo em `hermes_config.h`, conferir
   também que as IRQs escolhidas existem e que o DMA escolhido atende à UART.
2. Rodar `python3 -m hermes_simples.main --sim --sim-aircraft 20 --force-on
   --port /dev/ttyUSB0` no Pi com o STM32 ligado ao UART3.
3. No ST-Link VCP (USART2) deve aparecer:
   `ads-b seq=1 n=20 icao=A00000 met_ms=...` a cada 5 s.
4. `hermes_adsb.h` expõe `HermesAdsb_Stats()`: `buffers`, `overruns`,
   `dma_errors`, `resyncs` — todos devem ficar em 0 exceto `buffers`.
