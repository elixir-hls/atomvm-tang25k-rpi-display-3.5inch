"""
The 3.5inch RPi Display (ILI9486, 480 x 320, + XPT2046 touch) for elixir-hls / atomvm-tang25k-hls
(EHLS.RPiDisplay35 on the Elixir side, EHLS.Blocks):

  lcd  peripheral: SPI master (32-bit words, CS 0 = LCD, 1 = touch; the divider CSR picks
       ~12.5 MHz for the LCD and ~1.5 MHz for the touch), DC / RST outputs, PENIRQ input
       (CSRs lcd_spi_*, lcd_ctl_out, lcd_irq_in), driven by the :lcd NIFs (src/lcd_nif.c)

Default pins: PMOD J6 of the Sipeed Tang Primer 25K Dock, as in usb-rtl-sdr-tang25k
(LiteX j6 index -> Dock pin: 0..3 = pins 11, 9, 7, 5; 4..7 = pins 12, 10, 8, 6); an
application moves them with use EHLS, blocks: [{EHLS.RPiDisplay35, pins: [dc: "j6:0", ...]}]
(configure).
"""
from migen import Cat
from litex.build.generic_platform import IOStandard, Misc, Pins, Subsignal
from litex.soc.cores.gpio import GPIOIn, GPIOOut
from litex.soc.cores.spi import SPIMaster

# signal -> LiteX connector pin(s)
PINS = {
    "sclk":   "j6:3",              # J6-5  H5
    "mosi":   "j6:2",              # J6-7  H8
    "miso":   "j6:7",              # J6-6  J5
    "cs_n":   "j6:1 j6:6",         # J6-9 LCD_CS (G7), J6-8 TP_CS (H7)
    "dc":     "j6:0",              # J6-11 F5
    "rst":    "j6:4",              # J6-12 G5
    "tp_irq": "j6:5",              # J6-10 G8
}


def configure(options):
    """options: {"pins": {signal: pins}} over PINS."""
    for k, v in options.items():
        if k != "pins":
            raise SystemExit(f"hls_rpi_display_35: unknown option {k}")
        for sig, pins in v.items():
            if sig not in PINS:
                raise SystemExit(f"hls_rpi_display_35: unknown pin {sig} (one of {', '.join(PINS)})")
            PINS[sig] = pins


def add_lcd(soc):
    """The LCD + touch on the SoC (src/lcd_nif.c drives them)."""
    soc.platform.add_extension([
        ("lcd_spi", 0,
            Subsignal("clk",  Pins(PINS["sclk"])),
            Subsignal("mosi", Pins(PINS["mosi"])),
            Subsignal("cs_n", Pins(PINS["cs_n"])),     # [0] LCD, [1] touch
            Subsignal("miso", Pins(PINS["miso"]), Misc("PULL_MODE=UP")),
            IOStandard("LVCMOS33")),
        ("lcd_ctl", 0,
            Subsignal("dc",  Pins(PINS["dc"])),
            Subsignal("rst", Pins(PINS["rst"])),
            IOStandard("LVCMOS33")),
        ("lcd_irq", 0, Pins(PINS["tp_irq"]), IOStandard("LVCMOS33"), Misc("PULL_MODE=UP")),
    ])
    soc.lcd_spi = SPIMaster(soc.platform.request("lcd_spi"), data_width=32,
                            sys_clk_freq=soc.sys_clk_freq, spi_clk_freq=12.5e6, mode="aligned")
    soc.lcd_spi.add_clk_divider()
    ctl = soc.platform.request("lcd_ctl")
    soc.lcd_ctl = GPIOOut(Cat(ctl.dc, ctl.rst))      # bit 0 DC, bit 1 RST (0 = reset)
    soc.lcd_irq = GPIOIn(soc.platform.request("lcd_irq"))


BLOCKS = {}
PERIPHERALS = {"lcd": add_lcd}
