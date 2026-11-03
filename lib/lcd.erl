%% Stubs of the NIFs in src/lcd_nif.c: the 3.5inch RPi Display (ILI9486, 480 x 320, RGB565)
%% and its touch panel (XPT2046) on PMOD J6.
-module(lcd).
-export([init/0, fill/5, text/6, touch/0]).

%% Reset and set up the panel (landscape 480 x 320), clear it to black (~0.6 s).
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
