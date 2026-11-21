CC ?= gcc
CFLAGS ?= -Wall -Wextra -std=c11 -O2

OBJS = test_receiver.o hermes_protocol.o hal_uart_mock.o

all: test_receiver

test_receiver: $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) -lm

%.o: %.c hermes_protocol.h hal_uart_mock.h
	$(CC) $(CFLAGS) -c -o $@ $<

test: test_receiver
	./test_receiver

clean:
	rm -f test_receiver $(OBJS)

.PHONY: all test clean
