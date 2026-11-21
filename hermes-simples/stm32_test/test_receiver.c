/*
 * Testes do lado STM32, compilaveis no PC/Pi:
 *     make test
 *
 * Dois modelos sao testados:
 *  1) bytes diretos na API da ISR;
 *  2) mock do HAL_UART_Receive_IT -> RxCpltCallback -> hermes_rx_on_byte_isr.
 */
#include "hal_uart_mock.h"
#include "hermes_protocol.h"

#include <stddef.h>
#include <stdio.h>
#include <string.h>

static unsigned failures = 0;

#define CHECK(cond) do {                                                     \
    if (!(cond)) {                                                           \
        printf("  FALHOU linha %d: %s\n", __LINE__, #cond);                  \
        failures++;                                                          \
    }                                                                        \
} while (0)

static void fill_plane(Plane_t *p, int n) {
    memset(p, 0, sizeof(*p));
    snprintf(p->flight, sizeof(p->flight), "FLY%d", n);
    snprintf(p->squawk, sizeof(p->squawk), "%04d", n);
    p->icaoID = 0xE40000 + (float)n;
    p->met_s = 12.5f + (float)n;
    p->addrtype = 1.0f;
    p->alt_baro = 1000.0f + (float)n;
    p->alt_geom = 1100.0f + (float)n;
    p->gs = 200.0f + (float)n;
    p->ias = 190.0f;
    p->tas = 210.0f;
    p->mach = 0.78f;
    p->track = 270.0f + (float)n;
    p->track_rate = 1.5f;
    p->roll = -2.0f;
    p->mag_heading = 271.0f;
    p->true_heading = 272.0f;
    p->baro_rate = -300.0f;
    p->geom_rate = -310.0f;
    p->category = 3.0f;
    p->nav_qnh = 1013.2f;
    p->nav_altitude_mcp = 5000.0f;
    p->nav_altitude_fms = 5100.0f;
    p->nav_heading = 90.0f;
    p->lat = -23.5f - (float)n * 0.01f;
    p->lon = -46.6f + (float)n * 0.01f;
    p->nic = 7.0f;
    p->rc = 25.0f;
    p->seen_pos = 0.5f;
    p->version = 2.0f;
    p->nic_baro = 1.0f;
    p->nac_p = 8.0f;
    p->nac_v = 7.0f;
    p->sil = 3.0f;
    p->sil_type = 1.0f;
    p->gva = 2.0f;
    p->sda = 2.0f;
    p->mrar_source = 1.0f;
    p->wind_speed = 15.0f;
    p->wind_dir = 180.0f;
    p->temperature = -20.5f;
    p->pressure = 1000.0f;
    p->turbulence = 1.0f;
    p->humidity = 40.0f;
    p->emergency = 0.0f;
    p->messages = 123.0f;
    p->seen = 0.2f;
    p->rssi = -3.5f;
    p->nav_modes = HNAV_ALT_HOLD | HNAV_TCAS;
    p->flags = HFLAG_MODE_A | HFLAG_MLAT;
}

static void buffer_from_planes(uint8_t *buf, const Plane_t *planes, unsigned n) {
    memset(buf, 0, HERMES_BUFFER_SIZE);
    for (unsigned i = 0; i < n; i++) {
        memcpy(buf + i * HERMES_PLANE_SIZE, &planes[i], HERMES_PLANE_SIZE);
    }
}

static void check_plane(const Plane_t *p, int n) {
    char expected[8];
    snprintf(expected, sizeof(expected), "FLY%d", n);
    CHECK(strcmp(p->flight, expected) == 0);
    CHECK(p->icaoID == 0xE40000 + (float)n);
    CHECK(p->met_s == 12.5f + (float)n);
    CHECK(p->alt_baro == 1000.0f + (float)n);
    CHECK(p->gs == 200.0f + (float)n);
    CHECK(p->track == 270.0f + (float)n);
    CHECK(p->lat == -23.5f - (float)n * 0.01f);
    CHECK(p->lon == -46.6f + (float)n * 0.01f);
    CHECK(p->seen == 0.2f);
    CHECK(p->rssi == -3.5f);
    CHECK(p->nav_modes == (HNAV_ALT_HOLD | HNAV_TCAS));
    CHECK(p->flags == (HFLAG_MODE_A | HFLAG_MLAT));
}

static void test_layout(void) {
    printf("test_layout\n");
    CHECK(sizeof(Plane_t) == 204u);
    CHECK(HERMES_BUFFER_SIZE == 4080u);
    CHECK(offsetof(Plane_t, icaoID) == 16u);
    CHECK(offsetof(Plane_t, met_s) == 20u);
    CHECK(offsetof(Plane_t, nav_modes) == 196u);
    CHECK(offsetof(Plane_t, flags) == 200u);
}

static void test_partial_then_complete(void) {
    printf("test_partial_then_complete\n");
    Plane_t planes[HERMES_NUM_PLANES];
    uint8_t buf[HERMES_BUFFER_SIZE];
    for (unsigned i = 0; i < HERMES_NUM_PLANES; i++) fill_plane(&planes[i], (int)i);
    buffer_from_planes(buf, planes, 3);

    hermes_rx_t rx;
    hermes_rx_init(&rx);
    for (unsigned i = 0; i < 123; i++) hermes_rx_feed_byte(&rx, buf[i]);
    CHECK(!hermes_rx_complete(&rx));
    CHECK(rx.index == 123u);
    CHECK(rx.bytes_received == 123u);

    for (unsigned i = 123; i < HERMES_BUFFER_SIZE; i++) hermes_rx_feed_byte(&rx, buf[i]);
    CHECK(hermes_rx_complete(&rx));
    CHECK(rx.bytes_received == HERMES_BUFFER_SIZE);

    Plane_t out[HERMES_NUM_PLANES];
    hermes_rx_take(&rx, out);
    check_plane(&out[0], 0);
    check_plane(&out[1], 1);
    check_plane(&out[2], 2);
    CHECK(!hermes_rx_complete(&rx));
    CHECK(out[3].icaoID == 0.0f); /* slots vazios sao zero */
    CHECK(rx.bytes_received == 0u);
}

static void test_two_buffers_back_to_back(void) {
    printf("test_two_buffers_back_to_back\n");
    Plane_t planes[HERMES_NUM_PLANES];
    uint8_t buf[HERMES_BUFFER_SIZE];
    for (unsigned i = 0; i < HERMES_NUM_PLANES; i++) fill_plane(&planes[i], 9);
    buffer_from_planes(buf, planes, HERMES_NUM_PLANES);

    hermes_rx_t rx;
    hermes_rx_init(&rx);
    hermes_rx_feed_bytes(&rx, buf, HERMES_BUFFER_SIZE);
    CHECK(hermes_rx_complete(&rx));
    Plane_t out[HERMES_NUM_PLANES];
    hermes_rx_take(&rx, out);
    check_plane(&out[0], 9);
    check_plane(&out[HERMES_NUM_PLANES - 1], 9);

    hermes_rx_feed_bytes(&rx, buf, HERMES_BUFFER_SIZE);
    CHECK(hermes_rx_complete(&rx));
    hermes_rx_take(&rx, out);
    check_plane(&out[0], 9);
}

static void test_extra_bytes_are_counted_as_overrun(void) {
    printf("test_extra_bytes_are_counted_as_overrun\n");
    hermes_rx_t rx;
    hermes_rx_init(&rx);
    for (unsigned i = 0; i < HERMES_BUFFER_SIZE + 3; i++) {
        hermes_rx_feed_byte(&rx, 0xAB);
    }
    CHECK(hermes_rx_complete(&rx));
    CHECK(hermes_rx_overruns(&rx) == 3u);
    Plane_t out[HERMES_NUM_PLANES];
    hermes_rx_take(&rx, out);
    CHECK(!hermes_rx_complete(&rx));
    CHECK(hermes_rx_overruns(&rx) == 0u); /* take() reinicia tudo */
}

static void test_hal_rx_interrupt_mock(void) {
    printf("test_hal_rx_interrupt_mock\n");
    Plane_t planes[HERMES_NUM_PLANES];
    uint8_t buf[HERMES_BUFFER_SIZE];
    for (unsigned i = 0; i < HERMES_NUM_PLANES; i++) fill_plane(&planes[i], (int)i);
    buffer_from_planes(buf, planes, 3);

    hermes_rx_t rx;
    hal_uart_mock_t uart;
    hermes_rx_init(&rx);
    hal_uart_mock_init(&uart, &rx);

    /* Equivale a HAL_UART_Receive_IT(&huart, &byte, 1) no main(). */
    hal_uart_mock_start_it(&uart);
    CHECK(uart.arm_calls == 1u);

    /* Cada byte chega como evento de hardware e dispara a interrupcao. */
    for (unsigned i = 0; i < HERMES_BUFFER_SIZE; i++) {
        hal_uart_mock_byte_arrived(&uart, buf[i]);
    }

    CHECK(uart.isr_calls == HERMES_BUFFER_SIZE);
    CHECK(uart.rearm_calls == HERMES_BUFFER_SIZE);
    CHECK(rx.bytes_received == HERMES_BUFFER_SIZE);
    CHECK(hermes_rx_complete(&rx));
    CHECK(hermes_rx_overruns(&rx) == 0u);

    Plane_t out[HERMES_NUM_PLANES];
    hermes_rx_take(&rx, out);
    check_plane(&out[0], 0);
    check_plane(&out[1], 1);
    check_plane(&out[2], 2);
    CHECK(out[3].icaoID == 0.0f);
}

static void test_irq_masked_then_resumed(void) {
    printf("test_irq_masked_then_resumed\n");
    Plane_t planes[HERMES_NUM_PLANES];
    uint8_t buf[HERMES_BUFFER_SIZE];
    for (unsigned i = 0; i < HERMES_NUM_PLANES; i++) fill_plane(&planes[i], 4);
    buffer_from_planes(buf, planes, 1);

    hermes_rx_t rx;
    hal_uart_mock_t uart;
    hermes_rx_init(&rx);
    hal_uart_mock_init(&uart, &rx);
    hal_uart_mock_start_it(&uart);

    hal_uart_mock_disable_irq(&uart);
    for (unsigned i = 0; i < 17; i++) hal_uart_mock_byte_arrived(&uart, buf[i]);
    CHECK(uart.masked_bytes == 17u);
    CHECK(uart.isr_calls == 0u);
    CHECK(rx.bytes_received == 0u);
    CHECK(!hermes_rx_complete(&rx));

    hal_uart_mock_enable_irq(&uart);
    for (unsigned i = 17; i < HERMES_BUFFER_SIZE; i++) {
        hal_uart_mock_byte_arrived(&uart, buf[i]);
    }
    /* Faltam os 17 bytes perdidos: o bloco ainda nao pode fechar. */
    CHECK(!hermes_rx_complete(&rx));
    CHECK(rx.index == HERMES_BUFFER_SIZE - 17u);

    /* Bloco incompleto: descarta e espera o proximo bloco inteiro. */
    hermes_rx_init(&rx);
    CHECK(!hermes_rx_complete(&rx));
    for (unsigned i = 0; i < HERMES_BUFFER_SIZE; i++) {
        hal_uart_mock_byte_arrived(&uart, buf[i]);
    }
    CHECK(hermes_rx_complete(&rx));
    CHECK(uart.isr_calls == (HERMES_BUFFER_SIZE - 17u) + HERMES_BUFFER_SIZE);

    Plane_t out[HERMES_NUM_PLANES];
    hermes_rx_take(&rx, out);
    check_plane(&out[0], 4);
}

int main(void) {
    printf("Plane_t = %zu bytes, buffer = %zu bytes\n",
           sizeof(Plane_t), (size_t)HERMES_BUFFER_SIZE);
    test_layout();
    test_partial_then_complete();
    test_two_buffers_back_to_back();
    test_extra_bytes_are_counted_as_overrun();
    test_hal_rx_interrupt_mock();
    test_irq_masked_then_resumed();
    if (failures) {
        printf("FALHAS: %u\n", failures);
        return 1;
    }
    printf("TODOS OS TESTES PASSARAM\n");
    return 0;
}
