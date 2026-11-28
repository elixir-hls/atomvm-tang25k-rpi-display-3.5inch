"""
The 3.5inch RPi Display (ILI9486, 480 x 320, + XPT2046 touch) for elixir-hls / atomvm-tang25k-hls
(EHLS.RPiDisplay35 on the Elixir side, EHLS.Blocks).

Whenever the package is used, the drawing engine (lcd_engine.py) owns the display's pins and sets the
panel up after reset; its clients:

  lcd          peripheral: the CPU's port (CSRs lcd_*: an op FIFO, the touch's values, the LCD's
               SCLK divider, init, status), driven by the :lcd NIFs (src/lcd_nif.c)
  lcd_ops      sink: ops (u32), as lcd_engine.py documents them; a transaction ends with `last`
  lcd_pixels   sink: RGB565 pixels (u16) into the window x, y, w, h, row by row (a row a transaction)

Default pins: PMOD J6 of the Sipeed Tang Primer 25K Dock, as in usb-rtl-sdr-tang25k
(LiteX j6 index -> Dock pin: 0..3 = pins 11, 9, 7, 5; 4..7 = pins 12, 10, 8, 6); an
application moves them with use EHLS, blocks: [{EHLS.RPiDisplay35, pins: [dc: "j6:0", ...]}]
(configure).
"""
from migen import If, Mux, Signal
from litex.build.generic_platform import IOStandard, Misc, Pins, Subsignal
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStatus, CSRStorage, CSRField

import lcd_engine as e

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


class _Pads:
    def __init__(self, spi, ctl):
        self.clk, self.mosi, self.cs_n, self.miso = spi.clk, spi.mosi, spi.cs_n, spi.miso
        self.dc, self.rst = ctl.dc, ctl.rst


def add_to_soc(soc):
    """The engine on the display's pins (soc.lcd_engine), for the CPU's port and the sinks."""
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
    pads = _Pads(soc.platform.request("lcd_spi"), soc.platform.request("lcd_ctl"))
    soc.lcd_engine = e.LCDEngine(soc.sys_clk_freq, pads)
    soc.lcd_engine.penirq = soc.platform.request("lcd_irq")


class CPUPort(LiteXModule):
    """The CPU's client (CSRs lcd_*): `op` pushes an op into a FIFO of 512, `room` what it can still
    take; `status`: the init has run, the CPU's ops are all out, PENIRQ (1: not pressed); `touch`: the
    oldest of the touch's values and whether there is one, `touch_pop` takes it; `div`: the LCD's
    SCLK divider (3: 16.7 MHz at 50 MHz; >= 2); `init` runs the set-up again."""
    DEPTH = 512

    def __init__(self, engine, penirq):
        self.op = CSRStorage(32, description="Write: an op into the FIFO.")
        self.room = CSRStatus(16, description="Ops the FIFO can still take.")
        self.status = CSRStatus(fields=[
            CSRField("ready", size=1, offset=0, description="The panel set up."),
            CSRField("idle", size=1, offset=1, description="The CPU's ops all out."),
            CSRField("penirq", size=1, offset=2, description="The touch's PENIRQ (1: not pressed)."),
        ])
        self.touch = CSRStatus(fields=[
            CSRField("value", size=12, offset=0),
            CSRField("valid", size=1, offset=16),
        ])
        self.touch_pop = CSRStorage(1, description="Write: take the oldest touch value.")
        self.div = CSRStorage(8, reset=3, description="The LCD's SCLK divider (>= 2).")
        self.init = CSRStorage(1, description="Write: set the panel up again.")

        self.fifo = fifo = stream.SyncFIFO([("data", 32)], self.DEPTH, buffered=True)
        me = engine.add_client(fifo.source)
        self.comb += [
            fifo.sink.valid.eq(self.op.re),
            fifo.sink.data.eq(self.op.storage),
            # (the buffered FIFO holds one more than DEPTH: room 0 then too)
            self.room.status.eq(Mux(fifo.level >= self.DEPTH, 0, self.DEPTH - fifo.level)),
            self.status.fields.ready.eq(engine.ready),
            self.status.fields.idle.eq(~fifo.source.valid & ~(engine.granted & (engine.owner == me))),
            self.status.fields.penirq.eq(penirq),
            self.touch.fields.value.eq(engine.touch.source.data),
            self.touch.fields.valid.eq(engine.touch.source.valid),
            engine.touch.source.ready.eq(self.touch_pop.re),
            engine.div_lcd.eq(self.div.storage),
            engine.init.start.eq(self.init.re),
        ]


def add_lcd(soc):
    """The CPU's port (src/lcd_nif.c drives it)."""
    soc.lcd = CPUPort(soc.lcd_engine, soc.lcd_engine.penirq)


class LCDOps(LiteXModule):
    """Sink: ops (u32) straight to the engine. Stopped in the middle of a transaction, it ends it
    (an op that only ends it), so that the display is not held."""
    def __init__(self, soc, enable):
        self.sink = stream.Endpoint([("data", 32)])
        ep = stream.Endpoint([("data", 32)])
        inside = Signal()            # an op of a transaction without its `last` yet went out
        self.comb += [
            If(enable,
                ep.valid.eq(self.sink.valid), ep.data.eq(self.sink.data), self.sink.ready.eq(ep.ready)
            ).Else(
                ep.valid.eq(inside), ep.data.eq(e.LAST), self.sink.ready.eq(1)),
        ]
        self.sync += If(ep.valid & ep.ready, inside.eq(~ep.data[27]))
        soc.lcd_engine.add_client(ep)


class LCDPixels(LiteXModule):
    """Sink: RGB565 pixels (u16) into the window x, y, w, h, row by row; each row one transaction
    (its window, then its w pixels), so that other clients draw in between; after the last row the
    first again; a row starts once its first pixel is there (the display is not held waiting for one). Stopped, it
    ends the row it is in with black and starts again at the first, even if started again before that row is out."""
    def __init__(self, soc, enable, x, y, w, h):
        if not (0 <= x and 0 < w and x + w <= e.W and 0 <= y and 0 < h and y + h <= e.H):
            raise SystemExit(f"lcd_pixels({x}, {y}, {w}, {h}): outside the {e.W} x {e.H} screen")
        self.sink = stream.Endpoint([("data", 32)])
        ep = stream.Endpoint([("data", 32)])
        row, col = Signal(max=max(h, 2)), Signal(max=max(w, 2))
        step = Signal(2)             # 0 COLS, 1 ROWS, 2 pixels
        yrow = Signal(9)
        last_px = Signal()
        stopped = Signal()           # stopped inside a row: it is ended with black, then the first row again
        pad = Signal()
        self.comb += [
            yrow.eq(y + row),
            last_px.eq(col == w - 1),
            pad.eq(~enable | stopped),
            If(step == 0,
                ep.valid.eq(enable & self.sink.valid), ep.data.eq(e.cols(x, x + w - 1)),
            ).Elif(step == 1,
                ep.valid.eq(1), ep.data.eq((e.OP_ROWS << 28) | (yrow << 9) | yrow),
            ).Else(
                ep.valid.eq(self.sink.valid | pad),
                ep.data.eq((e.OP_DATA << 28) | (last_px << 27) | Mux(pad, 0, self.sink.data[:16])),
                self.sink.ready.eq(ep.ready & ~pad),
            ),
            If(~enable, self.sink.ready.eq(1)),
        ]
        self.sync += [
            If(~enable & (step != 0), stopped.eq(1)),
            If(ep.valid & ep.ready,
                If(step == 0, step.eq(1)
                ).Elif(step == 1, step.eq(2), col.eq(0)
                ).Else(
                    col.eq(col + 1),
                    If(last_px,
                        step.eq(0),
                        If(pad, row.eq(0), stopped.eq(0)).Else(row.eq(Mux(row == h - 1, 0, row + 1))),
                    ),
                ),
            ),
            If(~enable & (step == 0), row.eq(0)),
        ]
        soc.lcd_engine.add_client(ep)


BLOCKS = {"lcd_ops": LCDOps, "lcd_pixels": LCDPixels}
PERIPHERALS = {"lcd": add_lcd}
