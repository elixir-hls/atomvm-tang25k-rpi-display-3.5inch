"""
The drawing engine of the 3.5inch RPi Display (ILI9486 + XPT2046): it owns the pins and runs ops,
32-bit words, from its clients (the init ROM, the CPU's port, the sinks), a transaction at a time.

  op[31:28]  the op        last[27]  the end of the client's transaction     [26:0] payload

  CMD    c[7:0]                 the word c, DC 0
  DATA   v[15:0]                the word v, DC 1 (a parameter or a pixel)
  COLS   x0[17:9] x1[8:0]       CASET x0, x1
  ROWS   y0[17:9] y1[8:0]       PASET y0, y1, then RAMWR: pixels follow
  FG     c[15:0]                the colour FILL and BITS' ones use
  BG     c[15:0]                the colour of BITS' zeros
  FILL   n[23:0]                n pixels of FG
  BITS   n-1[19:16] b[15:0]     n pixels from b's MSB: 1 FG, 0 BG
  WAIT   us[23:0]               nothing for so long
  RST    r[0]                   the reset pin (0: reset)
  TOUCH  c[7:0]                 a 24-bit transfer to the XPT2046 (its CS, its clock): the 12 bits
                                back (bits [14:3] of the 16 after c) into `touch`, a FIFO

Every command and parameter goes out as a 16-bit word (0x00, v): the ILI9486 of this board takes
them so. SPI mode 0, MSB first; CS of the chip addressed is low from the transaction's first word
to the end of its last op. The LCD's SCLK is sys / div_lcd (a signal, >= 2: 3 gives 16.7 MHz at
50 MHz), the touch's sys / div_touch.

The arbiter grants a transaction at a time: client 0 (the init ROM) first whenever it has ops,
the others in turn, and those only once the init has run (`ready`); a client must end what it
starts (an op with `last`).
"""
from migen import Signal, If, Case, Array, Cat, Constant, Memory, Mux, Module
from litex.gen import LiteXModule
from litex.soc.interconnect import stream

OP_CMD, OP_DATA, OP_COLS, OP_ROWS, OP_FG, OP_BG, OP_FILL, OP_BITS, OP_WAIT, OP_RST, OP_TOUCH = range(1, 12)
LAST = 1 << 27


def op(code, payload=0, last=False):
    return (code << 28) | (LAST if last else 0) | payload


def cmd(c, last=False):
    return op(OP_CMD, c & 0xFF, last)


def data(v, last=False):
    return op(OP_DATA, v & 0xFFFF, last)


def cols(x0, x1, last=False):
    return op(OP_COLS, (x0 << 9) | x1, last)


def rows(y0, y1, last=False):
    return op(OP_ROWS, (y0 << 9) | y1, last)


def fg(c, last=False):
    return op(OP_FG, c & 0xFFFF, last)


def bg(c, last=False):
    return op(OP_BG, c & 0xFFFF, last)


def fill(n, last=False):
    return op(OP_FILL, n & 0xFFFFFF, last)


def bits(b, n=16, last=False):
    return op(OP_BITS, ((n - 1) << 16) | (b & 0xFFFF), last)


def wait(us, last=False):
    return op(OP_WAIT, us & 0xFFFFFF, last)


def rst(r, last=False):
    return op(OP_RST, r & 1, last)


def touch(c, last=False):
    return op(OP_TOUCH, c & 0xFF, last)


W, H = 480, 320

# power and gamma: the Linux waveshare35a overlay's (without them a grey ramp jumps)
PG = [0x0f, 0x1f, 0x1c, 0x0c, 0x0f, 0x08, 0x48, 0x98, 0x37, 0x0a, 0x13, 0x04, 0x11, 0x0d, 0x00]
NG = [0x0f, 0x32, 0x2e, 0x0b, 0x0d, 0x05, 0x47, 0x75, 0x37, 0x06, 0x10, 0x03, 0x24, 0x20, 0x00]


def init_ops(ms=1000, clear=(W, H)):
    """The panel's set-up as ops (one transaction): the reset pulse, software reset, sleep out,
    16-bit pixels, landscape, power and gamma, display on, the screen black. `ms`: microseconds a
    millisecond (smaller in a simulation); `clear`: the area cleared."""
    def c(code, *params):
        return [cmd(code)] + [data(p) for p in params]
    o = [rst(0), wait(20 * ms), rst(1), wait(150 * ms)]
    o += c(0x01) + [wait(150 * ms)]                # software reset
    o += c(0x11) + [wait(150 * ms)]                # sleep out
    o += c(0x3a, 0x55)                             # 16 bits a pixel
    o += c(0x36, 0x28)                             # landscape
    o += c(0xc2, 0x44) + c(0xc5, 0, 0, 0, 0) + c(0xe0, *PG) + c(0xe1, *NG) + c(0xe2, *NG)
    o += c(0x29)                                   # display on
    w, h = clear
    o += [cols(0, w - 1), rows(0, h - 1), fg(0), fill(w * h, last=True)]
    return o


class Shifter(Module):
    """One SPI word at a time, mode 0, MSB first, with no gap between words: `sink` takes a word
    (left-aligned in 24 bits, `nbits` of it) while the last bit of the one before goes out. SCLK
    is low for div - div // 2 clocks of a bit, high for div // 2; MISO is sampled as it rises.
    `rx` / `rx_valid`: the bits back of the word just finished, `rx_tgt` its target."""
    def __init__(self, div_lcd, div_touch):
        self.sink = stream.Endpoint([("data", 24), ("nbits", 5), ("dc", 1), ("tgt", 1)])
        self.sclk, self.mosi, self.miso = Signal(), Signal(), Signal()
        self.dc, self.tgt = Signal(), Signal()
        self.busy = Signal()
        self.rx, self.rx_valid, self.rx_tgt = Signal(24), Signal(), Signal()

        sh, rx = Signal(24), Signal(24)
        left, cnt, div = Signal(5), Signal(8), Signal(8)
        lo = Signal(8)
        self.comb += lo.eq(div - (div >> 1))
        last_clk = Signal()
        rx_next = Signal(24)
        self.comb += rx_next.eq(Mux(cnt == lo, Cat(self.miso, rx[:23]), rx))
        self.comb += [
            last_clk.eq(self.busy & (left == 1) & (cnt == div - 1)),
            self.sink.ready.eq(~self.busy | last_clk),
            self.sclk.eq(self.busy & (cnt >= lo)),
            self.mosi.eq(self.busy & sh[23]),
        ]
        sink = self.sink
        self.sync += [
            self.rx_valid.eq(0),
            If(self.busy,
                cnt.eq(cnt + 1),
                rx.eq(rx_next),
                If(cnt == div - 1,
                    cnt.eq(0),
                    sh.eq(sh << 1),
                    left.eq(left - 1),
                    If(left == 1,
                        self.busy.eq(0),
                        self.rx.eq(rx_next),
                        self.rx_valid.eq(1),
                        self.rx_tgt.eq(self.tgt),
                    ),
                ),
            ),
            If(sink.valid & sink.ready,
                self.busy.eq(1),
                sh.eq(sink.data), left.eq(sink.nbits), cnt.eq(0),
                rx.eq(0),
                self.dc.eq(sink.dc), self.tgt.eq(sink.tgt),
                div.eq(Mux(sink.tgt, div_touch, div_lcd)),
            ),
        ]


class InitROM(Module):
    """Client 0: the set-up ops, from reset and again at `start`; `done` once they are out."""
    def __init__(self, ops):
        self.source = stream.Endpoint([("data", 32)])
        self.start = Signal()
        self.done = Signal()
        mem = Memory(32, len(ops), init=ops)
        port = mem.get_port(async_read=True)
        self.specials += mem, port
        adr = Signal(max=max(len(ops), 2))
        running = Signal(reset=1)
        self.comb += [
            port.adr.eq(adr),
            self.source.valid.eq(running),
            self.source.data.eq(port.dat_r),
        ]
        self.sync += [
            If(self.source.valid & self.source.ready,
                adr.eq(adr + 1),
                If(adr == len(ops) - 1, running.eq(0), self.done.eq(1)),
            ),
            If(self.start, adr.eq(0), running.eq(1), self.done.eq(0)),
        ]


class LCDEngine(LiteXModule):
    """The engine: pads (sclk, mosi, miso, cs_n[2], dc, rst), the init ROM as client 0; others
    with add_client(endpoint) before the end of the build. `div_lcd`: the LCD's SCLK divider
    (a signal the CPU's port drives, reset 3); `ready`: the init has run; `epoch` a pulse each time
    it has (what a sink drew before is gone); `touch`: a FIFO of the touch's 12-bit values."""
    def __init__(self, sys_clk_freq, pads=None, init=None, div_touch=32):
        self.pads = pads
        self.div_lcd = Signal(8, reset=3)
        self.ready = Signal()
        self.epoch = Signal()
        self.idle = Signal()
        self.init = InitROM(init if init is not None else init_ops())
        self.touch = stream.SyncFIFO([("data", 12)], 16)
        self.sclk, self.mosi, self.miso = Signal(), Signal(), Signal()
        self.cs_n, self.dc, self.rst = Signal(2, reset=3), Signal(), Signal(reset=1)
        self.clk_mhz = int(round(sys_clk_freq / 1e6))
        self._div_touch = div_touch
        self._clients = [self.init.source]

    def add_client(self, ep):
        """A client: an endpoint of 32-bit ops (data); returns its index."""
        self._clients.append(ep)
        return len(self._clients) - 1

    def do_finalize(self):
        clients = self._clients
        n = len(clients)
        self.shifter = sh = Shifter(self.div_lcd, Constant(self._div_touch, 8))

        # the arbiter: one transaction at a time
        granted, idx, rr = Signal(), Signal(max=max(n, 2)), Signal(max=max(n, 2))
        done_txn = Signal()
        op_valid, op_ready, op_data = Signal(), Signal(), Signal(32)
        valids = Array(c.valid for c in clients)
        datas = Array(c.data for c in clients)
        self.comb += [op_valid.eq(granted & valids[idx]), op_data.eq(datas[idx])]
        for i, c in enumerate(clients):
            self.comb += c.ready.eq(granted & (idx == i) & op_ready)
        # the next client: 0 if it has ops, else the first with ops from rr on (the others only once ready)
        pick = Signal(max=max(n, 2))
        any_ = Signal()
        rot = [Signal(max=max(n, 2), name=f"rot{k}") for k in range(n)]
        for k in range(n):
            self.comb += rot[k].eq(Mux(rr + k >= n, rr + k - n, rr + k))
        sel = None
        for k in reversed(range(n)):
            cand = rot[k]
            cond = (cand != 0) & valids[cand] & self.ready
            sel = If(cond, pick.eq(cand), any_.eq(1)) if sel is None else If(cond, pick.eq(cand), any_.eq(1)).Else(sel)
        self.comb += If(clients[0].valid, pick.eq(0), any_.eq(1)).Else(sel)
        self.sync += [
            If(~granted & any_, granted.eq(1), idx.eq(pick)),
            If(done_txn, granted.eq(0), rr.eq(Mux(idx == n - 1, 0, idx + 1))),
        ]

        # the sequencer: an op into words
        o = Signal(32)
        code, last = o[28:32], o[27]
        k, nwords = Signal(24), Signal(24)
        fgc, bgc, bitsr = Signal(16), Signal(16), Signal(16)
        wcnt = Signal(32)
        active, cur_tgt = Signal(), Signal()
        state = Signal(2)          # 0 take an op, 1 words, 2 wait, 3 end of the transaction
        a, b = o[9:18], o[0:9]
        wsel = Array([
            Cat(Mux(code == OP_COLS, 0x2a, 0x2b), Constant(0, 8)), a[8:], a[:8], b[8:], b[:8], Constant(0x2c, 9)])
        word, wdc, wtgt, wbits = Signal(16), Signal(), Signal(), Signal(5)
        self.comb += [
            wtgt.eq(code == OP_TOUCH),
            wbits.eq(Mux(code == OP_TOUCH, 24, 16)),
            Case(code, {
                OP_CMD:  [word.eq(o[:8]), wdc.eq(0)],
                OP_DATA: [word.eq(o[:16]), wdc.eq(1)],
                OP_COLS: [word.eq(wsel[k[:3]]), wdc.eq((k != 0) & (k != 5))],
                OP_ROWS: [word.eq(wsel[k[:3]]), wdc.eq((k != 0) & (k != 5))],
                OP_FILL: [word.eq(fgc), wdc.eq(1)],
                OP_BITS: [word.eq(Mux(bitsr[15], fgc, bgc)), wdc.eq(1)],
                "default": [word.eq(o[:8]), wdc.eq(0)],
            }),
            sh.sink.data.eq(Mux(wtgt, Cat(Constant(0, 16), o[:8]), Cat(Constant(0, 8), word))),
            sh.sink.nbits.eq(wbits), sh.sink.dc.eq(wdc), sh.sink.tgt.eq(wtgt),
            sh.sink.valid.eq(state == 1),
            op_ready.eq(state == 0),
            done_txn.eq((state == 3) & ~sh.busy),
            self.idle.eq(~granted & (state == 0) & ~sh.busy),
        ]
        after = If(last, state.eq(3)).Else(state.eq(0))
        self.sync += [
            If(state == 0,
                If(op_valid,
                    o.eq(op_data), k.eq(0),
                    Case(op_data[28:32], {
                        OP_FG: [fgc.eq(op_data[:16]), If(op_data[27], state.eq(3))],
                        OP_BG: [bgc.eq(op_data[:16]), If(op_data[27], state.eq(3))],
                        OP_RST: [self.rst.eq(op_data[0]), If(op_data[27], state.eq(3))],
                        OP_WAIT: [wcnt.eq(op_data[:24] * self.clk_mhz), state.eq(2)],
                        OP_CMD: [nwords.eq(1), state.eq(1)],
                        OP_DATA: [nwords.eq(1), state.eq(1)],
                        OP_TOUCH: [nwords.eq(1), state.eq(1)],
                        OP_COLS: [nwords.eq(5), state.eq(1)],
                        OP_ROWS: [nwords.eq(6), state.eq(1)],
                        OP_FILL: [nwords.eq(op_data[:24]),
                                  If(op_data[:24] == 0, If(op_data[27], state.eq(3))).Else(state.eq(1))],
                        OP_BITS: [nwords.eq(op_data[16:20] + 1), bitsr.eq(op_data[:16]), state.eq(1)],
                        "default": If(op_data[27], state.eq(3)),
                    }),
                ),
            ).Elif(state == 1,
                If(sh.sink.ready,
                    active.eq(1), cur_tgt.eq(wtgt),
                    k.eq(k + 1), bitsr.eq(bitsr << 1),
                    If(k == nwords - 1, after),
                ),
            ).Elif(state == 2,
                If(wcnt <= 1, after).Else(wcnt.eq(wcnt - 1)),
            ).Elif(state == 3,
                If(~sh.busy, active.eq(0), state.eq(0)),
            ),
        ]

        # init done: ready, and an epoch for the sinks
        self.sync += [
            self.epoch.eq(0),
            If(self.init.start, self.ready.eq(0)),
            If(done_txn & (idx == 0) & self.init.done, self.ready.eq(1), self.epoch.eq(1)),
        ]

        # the touch's values: bits [14:3] of the 16 after the command
        self.comb += [
            self.touch.sink.valid.eq(sh.rx_valid & sh.rx_tgt),
            self.touch.sink.data.eq(sh.rx[3:15]),
        ]

        # the pins, registered together
        self.sync += [
            self.sclk.eq(sh.sclk), self.mosi.eq(sh.mosi), self.dc.eq(sh.dc),
            self.cs_n.eq(~Cat(active & ~cur_tgt, active & cur_tgt)),
        ]
        self.comb += sh.miso.eq(self.miso)
        if self.pads is not None:
            p = self.pads
            self.comb += [p.clk.eq(self.sclk), p.mosi.eq(self.mosi), p.cs_n.eq(self.cs_n),
                          p.dc.eq(self.dc), p.rst.eq(self.rst), self.miso.eq(p.miso)]
