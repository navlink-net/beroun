# Native port of the XCFA face renderer (see docs/visual-mode-c-port.md). Plain C, no globals.
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter
LDLIBS  ?= -lm -pthread
BUILD   ?= build

# the target of this build, <os>-<cpu>: the talking head is a native part, kept in bin/<target>/ and named in native.json
TARGET  ?= linux-$(shell uname -m | sed -e 's/aarch64/arm64/' -e 's/amd64/x86_64/')
BIN     ?= bin/$(TARGET)
BN      = ../beroun_native

TOOLS = $(BUILD)/xcfa-dump $(BUILD)/xcfa-features $(BUILD)/xcfa-select $(BUILD)/xcfa-pix $(BUILD)/ic-face $(BIN)/ic-face-view
all: $(TOOLS)

$(BUILD)/xcfa-dump: xcfa.c xcfa_dump.c xcfa.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ xcfa.c xcfa_dump.c $(LDLIBS)

$(BUILD)/xcfa-features: features.c features_tool.c features.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ features.c features_tool.c $(LDLIBS)

$(BUILD)/xcfa-select: xcfa.c library.c features.c select_tool.c xcfa.h library.h features.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ xcfa.c library.c features.c select_tool.c $(LDLIBS)

$(BUILD)/xcfa-pix: xcfa.c image.c warp.c farneback.c pix_tool.c xcfa.h image.h warp.h farneback.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ xcfa.c image.c warp.c farneback.c pix_tool.c $(LDLIBS) -ljpeg -lpng

FACE_SRC = xcfa.c library.c features.c image.c warp.c farneback.c render.c
$(BUILD)/ic-face: $(FACE_SRC) face_tool.c render.h xcfa.h library.h features.h image.h warp.h farneback.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -o $@ $(FACE_SRC) face_tool.c $(LDLIBS) -ljpeg -lpng

clean:
	rm -rf $(BUILD)

.PHONY: all clean

# the talking head on the screen: renders, plays the sound and draws it in a window of its own (face_view.c, on beroun_native: Wayland and ALSA behind it on Linux)
XDG_XML = /usr/share/wayland-protocols/stable/xdg-shell/xdg-shell.xml
$(BUILD)/xdg-shell-client-protocol.h: $(XDG_XML)
	@mkdir -p $(BUILD)
	wayland-scanner client-header $< $@
$(BUILD)/xdg-shell-protocol.c: $(XDG_XML)
	@mkdir -p $(BUILD)
	wayland-scanner private-code $< $@
$(BIN)/ic-face-view: $(FACE_SRC) face_view.c $(BN)/beroun_native.h $(BN)/beroun_native_linux.c $(BUILD)/xdg-shell-protocol.c $(BUILD)/xdg-shell-client-protocol.h render.h xcfa.h library.h features.h image.h warp.h farneback.h
	@mkdir -p $(BIN)
	$(CC) $(CFLAGS) -I$(BUILD) -I$(BN) -o $@ $(FACE_SRC) face_view.c $(BN)/beroun_native_linux.c $(BUILD)/xdg-shell-protocol.c $(LDLIBS) -ljpeg -lpng -lwayland-client -lasound
