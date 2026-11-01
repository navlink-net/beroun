# ic-screen: one picture on the whole screen (the farewell and boot screens of the power button). Plain C.
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
BUILD   ?= build
XDG_XML = /usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml

all: $(BUILD)/ic-screen

$(BUILD)/xdg-shell-client-protocol.h: $(XDG_XML)
	@mkdir -p $(BUILD)
	wayland-scanner client-header $< $@
$(BUILD)/xdg-shell-protocol.c: $(XDG_XML)
	@mkdir -p $(BUILD)
	wayland-scanner private-code $< $@
$(BUILD)/ic-screen: ic-screen.c $(BUILD)/xdg-shell-protocol.c $(BUILD)/xdg-shell-client-protocol.h
	$(CC) $(CFLAGS) -I$(BUILD) -o $@ ic-screen.c $(BUILD)/xdg-shell-protocol.c -lwayland-client -lpng
