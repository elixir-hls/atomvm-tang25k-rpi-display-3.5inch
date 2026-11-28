# atomvm-tang25k-rpi-display-3.5inch

The 3.5inch RPi Display (ILI9486, 480 x 320, RGB565, with an XPT2046 touch panel) for
[atomvm-tang25k-hls](https://github.com/elixir-hls/atomvm-tang25k-hls) (with [elixir-hls](https://github.com/elixir-hls/elixir-hls)): a drawing
engine in the FPGA, the CPU's NIFs on it, and sinks a `definstance` draws with.

In the application's mix.exs, beside its target:

```elixir
{:atomvm_tang25k_rpi_display_35inch, "~> 0.1"}
```

```elixir
use EHLS, blocks: [EHLS.RPiDisplay35], peripherals: [:lcd]

:lcd.fill(x, y, w, h, rgb565)
:lcd.text(x, y, "82.5", fg, bg, scale)   # 16 x 32 Terminus font; returns the x after it
:lcd.touch()                             # {x, y} while pressed, else false
:lcd.wait()                              # until the CPU's ops are all out
:lcd.clock(2)                            # the LCD's SCLK: sys / 2 (3 by default: 16.7 MHz)
:lcd.init()                              # the set-up again (the engine does it after reset)
```

## The engine

The engine (gateware/lcd_engine.py) owns the display's pins. After reset it sets the panel up
from a ROM (reset, landscape, 16-bit pixels, power and gamma registers as the Linux waveshare35a
overlay, black), then runs ops from its clients, a transaction at a time: the CPU and the sinks
below. An op is a 32-bit word: a command, a parameter or pixel, a window (`COLS`, `ROWS`), colours,
`FILL` (n pixels of one colour), `BITS` (up to 16 pixels of two colours: a row of a glyph), a wait,
the reset pin, a touch transfer; bit 27 ends the transaction. The LCD's SCLK is sys / 3 (16.7 MHz
at 50 MHz), with no gap between words; the touch's sys / 32.

The CPU's calls put ops into the engine's FIFO (512) and return: `fill` is 4 ops, a character of
`text` 36 at scale 1; the CPU waits only while the FIFO is full. `:lcd.ops/1` sends any ops.

## Sinks

```elixir
use EHLS, blocks: [EHLS.RPiDisplay35]

definstance picture do
  ... |> lcd_pixels(0, 0, 480, 320)
end
```

| Block | Element | |
|---|---|---|
| `lcd_ops()` | `u32` | ops, as the CPU sends them |
| `lcd_pixels(x, y, w, h)` | `u16` | RGB565 pixels into the window, row by row (a row a transaction) |

What is drawn and how (a graph, a widget) belongs to the application: its `defhls` functions
give ops to `lcd_ops()` or pixels to `lcd_pixels`.

A stopped `definstance` ends the transaction its sink is in, so the display is not held.

## Pins

Default: PMOD J6 of the Sipeed Tang Primer 25K Dock:

| Signal | Pin | Dock |
|---|---|---|
| `sclk` | j6:3 | J6-5 (H5) |
| `mosi` | j6:2 | J6-7 (H8) |
| `miso` | j6:7 | J6-6 (J5) |
| `cs_n` | j6:1 j6:6 | LCD_CS J6-9 (G7), TP_CS J6-8 (H7) |
| `dc` | j6:0 | J6-11 (F5) |
| `rst` | j6:4 | J6-12 (G5) |
| `tp_irq` | j6:5 | J6-10 (G8) |

Other pins: `use EHLS, blocks: [{EHLS.RPiDisplay35, pins: [dc: "j6:0", ...]}]`. The display's 5 V
(backlight) comes from outside.

## Example

`examples/lcd_bars.ex`: the test pattern (8 colour bars, grey / red / green / blue ramps in
32 steps) for checking the gamma; it runs on any SoC with `:lcd`.

`sim/`: simulations of the engine and its clients with a model of the panel
(`python3 sim/test_engine.py`, `test_clients.py`, `test_stalls.py`: the sinks when their instance waits or
stops; migen and LiteX).

From usb-rtl-sdr-tang25k (driver) and usb-rtl-sdr-tv (gamma).

## License

Apache-2.0 (see [LICENSE](LICENSE)). src/font.h: Terminus font, SIL Open Font License 1.1.
