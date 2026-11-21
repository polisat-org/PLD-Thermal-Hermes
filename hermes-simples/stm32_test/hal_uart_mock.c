#include "hal_uart_mock.h"

/* Equivale a HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart). */
static void rx_cplt_callback(hal_uart_mock_t *mock) {
    mock->isr_calls++;
    hermes_rx_on_byte_isr(mock->rx, mock->byte);

    /* HAL_UART_Receive_IT(huart, &mock->byte, 1) dentro do callback. */
    mock->byte = 0;
    mock->rearm_calls++;
}

void hal_uart_mock_init(hal_uart_mock_t *mock, hermes_rx_t *rx) {
    mock->rx = rx;
    mock->byte = 0;
    mock->irq_enabled = 0;
    mock->arm_calls = 0;
    mock->isr_calls = 0;
    mock->rearm_calls = 0;
    mock->masked_bytes = 0;
}

void hal_uart_mock_start_it(hal_uart_mock_t *mock) {
    mock->byte = 0;
    mock->irq_enabled = 1;
    mock->arm_calls++;
}

void hal_uart_mock_byte_arrived(hal_uart_mock_t *mock, uint8_t byte) {
    if (!mock->irq_enabled) {
        mock->masked_bytes++;
        return;
    }
    mock->byte = byte;
    rx_cplt_callback(mock);
}

void hal_uart_mock_disable_irq(hal_uart_mock_t *mock) {
    mock->irq_enabled = 0;
}

void hal_uart_mock_enable_irq(hal_uart_mock_t *mock) {
    mock->irq_enabled = 1;
}
