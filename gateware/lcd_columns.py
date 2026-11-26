"""
lcd_columns(x, y, w, h): a graph of w columns in the window x, y, w, h (a spectrum, say), drawn by
the display's engine with no CPU, only what changed.

The sink takes {col, cur, lo, hi} (u16 each): the column's value and a range, rows from the bottom
of the window (0 the bottom row; above h - 1 is h - 1); col's bit 15 marks the last element of a
frame (the columns may come in any order). Always ready.

Three buffers of columns: one written by the stream, one complete and waiting, one being drawn. A
frame that is complete while another waits replaces it (the latest wins). The renderer takes the
waiting one when it has drawn the one before.

A column's picture from its row r alone:

    trace   r between the column's value and the value of the column on its left (a line)
    band    lo <= r <= hi
    fill    under the trace (if `fill`)
    grid    a row or a column marked in the grid (every 32 rows, every 48 columns at first)
    bg      the rest

What each column shows is kept (its trace segment, lo, hi); a column is sent only if one of them
moved, from the lowest row that can have changed to the highest: a window one column wide (one
transaction), then its pixels top down. After the panel was set up (the engine's epoch) or a write
of `redraw`, the next frame is drawn whole.

Registers (the package's hls_rpi_display_35 CSRs, EHLS.RPiDisplay35 registers): the colours
(col_bg, col_grid, col_band, col_trace, col_fill), fill, redraw, grid (write: index[8:0],
column[16] (else a row), on[17]), frames (drawn), replaced (frames that never were).
"""
from migen import Signal, If, Cat, Memory, Mux, Constant
from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSRStatus, CSRStorage, CSRField

import lcd_engine as e

COLOURS = {"bg": 0x0000, "grid": 0x2945, "band": 0x0A69, "trace": 0xFFE0, "fill": 0x01A6}
GRID_ROWS, GRID_COLS = 32, 48


class ColumnRegisters(LiteXModule):
    """The CSRs of lcd_columns (soc.hls_rpi_display_35: hls_rpi_display_35_*)."""
    def __init__(self):
        for name, v in COLOURS.items():
            setattr(self, f"col_{name}", CSRStorage(16, reset=v, name=f"col_{name}",
                                                    description=f"The {name} colour (RGB565)."))
        self.fill = CSRStorage(1, reset=1, description="Fill under the trace.")
        self.redraw = CSRStorage(1, description="Write: the next frame drawn whole.")
        self.grid = CSRStorage(fields=[
            CSRField("index", size=9, offset=0),
            CSRField("column", size=1, offset=16),
            CSRField("on", size=1, offset=17),
        ], description="Write: a row (rows from the bottom) or a column of the grid on or off.")
        self.frames = CSRStatus(16, description="Frames drawn.")
        self.replaced = CSRStatus(16, description="Frames replaced while waiting (never drawn).")


def add_registers(soc):
    soc.hls_rpi_display_35 = ColumnRegisters()


class LCDColumns(LiteXModule):
    in_lane = 16

    def __init__(self, soc, enable, x, y, w, h, regs=None):
        if not (0 <= x and 0 < w <= 512 and x + w <= e.W and 0 <= y and 0 < h <= 512 and y + h <= e.H):
            raise SystemExit(f"lcd_columns({x}, {y}, {w}, {h}): outside the {e.W} x {e.H} screen")
        regs = regs if regs is not None else soc.hls_rpi_display_35
        eng = soc.lcd_engine
        self.sink = stream.Endpoint([("data", 64)])
        self.idle = Signal()       # nothing waiting, nothing being drawn
        ep = stream.Endpoint([("data", 32)])
        eng.add_client(ep)

        # the buffers: 3 x 512 columns of {cur, lo, hi}, 9 bits each
        buf = Memory(27, 3 * 512)
        bw, br = buf.get_port(write_capable=True), buf.get_port()
        # what each column shows: {jl, jh, lo, hi}
        shown = Memory(36, 512)
        sw, sr = shown.get_port(write_capable=True), shown.get_port()
        # the grid's rows and columns
        rows_init = [1 if r % GRID_ROWS == 0 else 0 for r in range(512)]
        cols_init = [1 if c % GRID_COLS == 0 else 0 for c in range(512)]
        grow, gcol = Memory(1, 512, init=rows_init), Memory(1, 512, init=cols_init)
        grw, grr = grow.get_port(write_capable=True), grow.get_port(async_read=True)
        gcw, gcr = gcol.get_port(write_capable=True), gcol.get_port(async_read=True)
        self.specials += buf, bw, br, shown, sw, sr, grow, grw, grr, gcol, gcw, gcr

        g = regs.grid.fields
        self.comb += [
            grw.adr.eq(g.index), grw.dat_w.eq(g.on), grw.we.eq(regs.grid.re & ~g.column),
            gcw.adr.eq(g.index), gcw.dat_w.eq(g.on), gcw.we.eq(regs.grid.re & g.column),
        ]

        def clamp(v):
            return Mux(v >= h, h - 1, v[:9])

        # the stream into the buffer being written
        wb, pb, rb = Signal(2, reset=0), Signal(2, reset=1), Signal(2, reset=2)
        pvalid = Signal()
        d = self.sink.data
        col, cur, lo, hi = d[0:16], d[16:32], d[32:48], d[48:64]
        take = self.sink.valid & enable
        frames, replaced = Signal(16), Signal(16)
        self.comb += [
            self.sink.ready.eq(1),
            bw.adr.eq(Cat(col[:9], wb)),
            bw.dat_w.eq(Cat(clamp(cur), clamp(lo), clamp(hi))),
            bw.we.eq(take & (col[:9] < w)),
            regs.frames.status.eq(frames), regs.replaced.status.eq(replaced),
        ]
        start = Signal()           # the renderer takes the waiting frame
        end = Signal()
        self.comb += end.eq(take & col[15])
        self.sync += [
            # the three buffers stay distinct: a frame ending goes waiting (replacing the one waiting),
            # the renderer takes the waiting one; both at once: it takes the one that waited
            If(end & start,
                rb.eq(pb), pb.eq(wb), wb.eq(rb),
            ).Elif(end,
                wb.eq(pb), pb.eq(wb), pvalid.eq(1),
                If(pvalid, replaced.eq(replaced + 1)),
            ).Elif(start,
                rb.eq(pb), pb.eq(rb), pvalid.eq(0),
            ),
        ]

        # the renderer
        c = Signal(10)
        left = Signal(9)           # the value of the column on the left
        whole, redraw_next = Signal(), Signal(reset=1)
        state = Signal(3)          # 0 idle, 1 read, 2 compute, 3 COLS, 4 ROWS, 5 pixels, 6 next
        ncur, nlo, nhi = br.dat_r[0:9], br.dat_r[9:18], br.dat_r[18:27]
        ojl, ojh, olo, ohi = sr.dat_r[0:9], sr.dat_r[9:18], sr.dat_r[18:27], sr.dat_r[27:36]
        lft = Signal(9)
        self.comb += lft.eq(Mux(c == 0, ncur, left))
        jl, jh = Signal(9), Signal(9)
        self.comb += [jl.eq(Mux(ncur < lft, ncur, lft)), jh.eq(Mux(ncur < lft, lft, ncur))]
        # the span: over the boundaries that moved, from the lower of old / new to the higher
        r0c, r1c, any_ = Signal(9), Signal(9), Signal()
        pairs = [(ojl, jl), (ojh, jh), (olo, nlo), (ohi, nhi)]
        lows, highs, moved = [], [], []
        for i, (o, n) in enumerate(pairs):
            lo_, hi_, mv = Signal(9, name=f"lo{i}"), Signal(9, name=f"hi{i}"), Signal(name=f"mv{i}")
            self.comb += [lo_.eq(Mux(o < n, o, n)), hi_.eq(Mux(o < n, n, o)), mv.eq(o != n)]
            lows.append(lo_)
            highs.append(hi_)
            moved.append(mv)
        acc_lo, acc_hi = Constant(511, 9), Constant(0, 9)
        for lo_, hi_, mv in zip(lows, highs, moved):
            acc_lo = Mux(mv & (lo_ < acc_lo), lo_, acc_lo)
            acc_hi = Mux(mv & (hi_ > acc_hi), hi_, acc_hi)
        self.comb += [
            any_.eq(whole | moved[0] | moved[1] | moved[2] | moved[3]),
            r0c.eq(Mux(whole, 0, acc_lo)),
            r1c.eq(Mux(whole, h - 1, acc_hi)),
        ]
        # latched for the column being sent
        sjl, sjh, slo, shi, scur = (Signal(9) for _ in range(5))
        r, r0 = Signal(9), Signal(9)
        ytop, ybot = Signal(9), Signal(9)
        colour = Signal(16)
        self.comb += [
            grr.adr.eq(r), gcr.adr.eq(c[:9]),
            If((r >= sjl) & (r <= sjh), colour.eq(regs.col_trace.storage)
            ).Elif((r >= slo) & (r <= shi), colour.eq(regs.col_band.storage)
            ).Elif(regs.fill.storage & (r < sjl), colour.eq(regs.col_fill.storage)
            ).Elif(grr.dat_r | gcr.dat_r, colour.eq(regs.col_grid.storage)
            ).Else(colour.eq(regs.col_bg.storage)),
            br.adr.eq(Cat(c[:9], rb)),
            sr.adr.eq(c[:9]),
            sw.adr.eq(c[:9]),
            sw.dat_w.eq(Cat(sjl, sjh, slo, shi)),
            sw.we.eq(state == 6),
            start.eq((state == 0) & pvalid),
            self.idle.eq((state == 0) & ~pvalid),
        ]
        self.comb += [
            ep.valid.eq((state == 3) | (state == 4) | (state == 5)),
            If(state == 3, ep.data.eq((e.OP_COLS << 28) | ((x + c[:9]) << 9) | (x + c[:9]))
            ).Elif(state == 4, ep.data.eq((e.OP_ROWS << 28) | (ytop << 9) | ybot)
            ).Else(ep.data.eq((e.OP_DATA << 28) | ((r == r0) << 27) | colour)),
        ]
        self.sync += [
            If(eng.epoch | regs.redraw.re, redraw_next.eq(1)),
            If(state == 0,
                If(pvalid, c.eq(0), whole.eq(redraw_next), redraw_next.eq(eng.epoch | regs.redraw.re), state.eq(1)),
            ).Elif(state == 1,
                state.eq(2),                     # the memories read at c
            ).Elif(state == 2,
                sjl.eq(jl), sjh.eq(jh), slo.eq(nlo), shi.eq(nhi), scur.eq(ncur),
                r.eq(r1c), r0.eq(r0c),
                ytop.eq(y + h - 1 - r1c), ybot.eq(y + h - 1 - r0c),
                If(any_, state.eq(3)).Else(state.eq(6)),
            ).Elif(state == 3,
                If(ep.ready, state.eq(4)),
            ).Elif(state == 4,
                If(ep.ready, state.eq(5)),
            ).Elif(state == 5,
                If(ep.ready, If(r == r0, state.eq(6)).Else(r.eq(r - 1))),
            ).Elif(state == 6,
                left.eq(scur),
                c.eq(c + 1),
                If(c == w - 1, state.eq(0), frames.eq(frames + 1)).Else(state.eq(1)),
            ),
        ]
