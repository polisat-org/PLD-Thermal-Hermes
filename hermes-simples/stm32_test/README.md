# Receptor STM32 — testes host do modelo de interrupção de RX

Os testes aqui rodam no PC/Pi com `gcc` e **modelam** a recepção UART por
interrupção: o mock simula `HAL_UART_Receive_IT()`, a chegada de 1 byte, a
chamada de `HAL_UART_RxCpltCallback()` e o rearmamento da IT. A task só pergunta
se o bloco de 4080 bytes fechou — **não há polling da UART**.

```bash
make test       # compila com gcc e roda os testes no PC/Pi
```

## Arquivos

| Arquivo | Função |
|---|---|
| `hermes_protocol.h` | `Plane_t` (204 bytes), constantes, API do receptor |
| `hermes_protocol.c` | `hermes_rx_on_byte_isr()` — roda dentro da ISR, só acumula bytes |
| `hal_uart_mock.h` / `.c` | mock host de `HAL_UART_Receive_IT()` + `HAL_UART_RxCpltCallback()` |
| `test_receiver.c` | testes: bloco parcial, 2 blocos seguidos, overrun, ISR mock, IRQ mascarada |
| `hal_isr_example.c.example` | cola real para STM32L476 / USART3 / DMA (renomear para `.c`) |
| `../obc_integration/` | pacote drop-in completo para o OBC, com `PATCHES.md` |

## Modelo testado no host

```c
static hermes_rx_t g_rx;
static uint8_t g_byte;

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
    hermes_rx_on_byte_isr(&g_rx, g_byte);
    HAL_UART_Receive_IT(huart, &g_byte, 1);   /* rearma */
}

/* armou a 1a IT */
HAL_UART_Receive_IT(&huart, &g_byte, 1);

for (;;) {
    if (hermes_rx_complete(&g_rx)) {
        __disable_irq();
        hermes_rx_take(&g_rx, g_planes);     /* 20 Plane_t */
        __enable_irq();
    }
}
```

`hermes_rx_take()` zera `index`, `complete` e os contadores. Bytes que chegam
depois que o bloco fechou (antes do `take()`) são contados em
`hermes_rx_overruns()` e não corrompem o buffer.

## Por que o OBC real usa DMA

O `functionalCodes/stateMachineVSCode` examinado roda com **MSI a 4 MHz, sem
PLL** (`RCC_MSIRANGE_6`, `RCC_SYSCLKSOURCE_MSI`). A 115200 8N1, uma interrupção
por byte seriam 115200 IRQs/s — inviável nesse clock. A integração real em
`obc_integration/` usa DMA com IRQ de transferência completa, dois buffers de
4080 bytes e notificação via `osThreadFlagsSet()`, também **sem polling**.
Os valores padrão (USART3 + DMA1_Channel3/request 2) ficam em
`obc_integration/hermes_config.h`; se o OBC for outro, mude lá.

| USART | Uso no OBC | HERMES |
|---|---|---|
| USART1 (PA9/PA10) | telecomandos, 1 byte/IT | não mexer |
| USART2 (PA2/PA3) | debug `__io_putchar` + telemetria | não mexer |
| USART3 (PC4/PC5) | inicializado, sem uso | RX do payload |

O STM32L476 **não tem DMAMUX**: `hdma.Init.Request` é o valor `CSELR` (0..7)
do canal. Conforme RM0351 Table 38, USART3_RX é `DMA1_Channel3` + `DMA_REQUEST_2`.
O símbolo `DMA_REQUEST_USART3_RX` não existe neste pacote HAL.

`Telemetry_t` continua com 85 bytes e o `uart.py` do OBC continua válido.

## Flag de timestamp

`Plane_t.met_s` é o segundo float depois de `icaoID`:

```c
offsetof(Plane_t, icaoID) == 16
offsetof(Plane_t, met_s)  == 20
```

O instante estimado da mensagem ADS-B é:

```c
float t_msg = p->met_s - p->seen;   /* seen vem do dump1090, em segundos */
```
