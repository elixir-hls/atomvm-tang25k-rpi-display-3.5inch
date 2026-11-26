%% Stubs of the NIFs in src/lcd_nif.c: the 3.5inch RPi Display (ILI9486, 480 x 320, RGB565)
%% and its touch panel (XPT2046) on PMOD J6, through the drawing engine's CPU port: a call puts
%% ops into the engine's FIFO and returns (fill/5, text/6), the pixels go out after.
-module(lcd).
-export([init/0, fill/5, text/6, touch/0, wait/0, ops/1, clock/1]).

%% Set the panel up again (landscape 480 x 320) and clear it to black, as the engine does after
%% reset; returns once done (~0.5 s).
-spec init() -> ok.
init() -> erlang:nif_error(undefined).

%% A W x H rectangle at X, Y in Color (RGB565).
-spec fill(integer(), integer(), integer(), integer(), 0..65535) -> ok.
fill(_X, _Y, _W, _H, _Color) -> erlang:nif_error(undefined).

%% Text in the 16 x 32 font, each pixel Scale x Scale (ASCII; others as '?'); stops at the
%% right edge. Returns the x after the text.
-spec text(integer(), integer(), binary(), 0..65535, 0..65535, 1..8) -> integer().
text(_X, _Y, _Text, _Fg, _Bg, _Scale) -> erlang:nif_error(undefined).

%% {X, Y} on the screen while the panel is pressed, else false.
-spec touch() -> {integer(), integer()} | false.
touch() -> erlang:nif_error(undefined).

%% Until the CPU's ops are all out.
-spec wait() -> ok.
wait() -> erlang:nif_error(undefined).

%% Any ops (32-bit integers, gateware/lcd_engine.py); the last ends the transaction (bit 27).
-spec ops([0..4294967295]) -> ok.
ops(_Ops) -> erlang:nif_error(undefined).

%% The LCD's SCLK divider: 3 by default (16.7 MHz at 50 MHz), 2 for 25 MHz.
-spec clock(2..255) -> ok.
clock(_Div) -> erlang:nif_error(undefined).
