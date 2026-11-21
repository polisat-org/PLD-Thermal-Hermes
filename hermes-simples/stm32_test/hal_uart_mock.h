/*
 * Mock de HAL_UART_Receive_IT para testes host.
 *
 * Simula exatamente o fluxo de interrupcao do STM32:
 *   start_it() -> HAL_UART_Receive_IT(huart, &byte, 1)
 *   byte_arrived() -> hardware recebe 1 byte -> HAL_UART_RxCpltCallback()
 *   callback -> hermes_rx_on_byte_isr() + HAL_UART_Receive_IT() (rearma)
 */
#ifndef HAL_UART_MOCK_H
#define HAL_UART_MOCK_H

#include "hermes_protocol.h"

#include <stdint.h>

typedef struct {
    hermes_rx_t *rx;
    uint8_t  byte;          /* o "buffer" de 1 byte passado ao HAL */
    int      irq_enabled;
    uint32_t arm_calls;     /* HAL_UART_Receive_IT inicial */
    uint32_t isr_calls;     /* quantas vezes o callback disparou */
    uint32_t rearm_calls;   /* quantas vezes o callback rearma a IT */
    uint32_t masked_bytes;  /* bytes que chegaram com IT desligada */
} hal_uart_mock_t;

void hal_uart_mock_init(hal_uart_mock_t *mock, hermes_rx_t *rx);
void hal_uart_mock_start_it(hal_uart_mock_t *mock);
void hal_uart_mock_byte_arrived(hal_uart_mock_t *mock, uint8_t byte);
void hal_uart_mock_disable_irq(hal_uart_mock_t *mock);
void hal_uart_mock_enable_irq(hal_uart_mock_t *mock);

#endif
