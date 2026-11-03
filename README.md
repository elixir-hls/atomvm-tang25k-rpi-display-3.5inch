# atomvm-tang25k-rpi-display-3.5inch

The 3.5inch RPi Display (ILI9486, 480 x 320, RGB565, with an XPT2046 touch panel) for
[atomvm-tang25k-hls](https://github.com/elixir-hls/atomvm-tang25k-hls) (with [elixir-hls](https://github.com/elixir-hls/elixir-hls)): a SoC
peripheral (SPI master, DC / RST, PENIRQ) and its NIFs.

```elixir
use EHLS, blocks: [EHLS.RPiDisplay35], peripherals: [:lcd]

:lcd.init()                              # reset, landscape, power / gamma registers, black
:lcd.fill(x, y, w, h, rgb565)
:lcd.text(x, y, "82.5", fg, bg, scale)   # 16 x 32 Terminus font; returns the x after it
:lcd.touch()                             # {x, y} while pressed, else false
```

The pixels go out from C (src/lcd_nif.c, polled SPI at ~12.5 MHz: a 16 x 32 character
~1 ms, the whole screen ~0.25 s); the touch is read at ~1.5 MHz, the median of 5 samples,
calibrated for this panel. Power and gamma registers as the Linux waveshare35a overlay.

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

From usb-rtl-sdr-tang25k (driver) and usb-rtl-sdr-tv (gamma).

## License

Apache-2.0 (see [LICENSE](LICENSE)). src/font.h: Terminus font, SIL Open Font License 1.1.
