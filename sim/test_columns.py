#!/usr/bin/env python3
"""Migen simulation of lcd_columns.LCDColumns(10, 5, 40, 32) on the engine, with the panel's model.
Frames of {col, cur, lo, hi} in random column order, the last marked; after each phase the window
must show the last frame as render() draws it:

1. a first frame (drawn whole);
2. the same frame again: no word sent at all;
3. three columns changed: only those columns and the next ones (their trace segment joins them)
   sent, a window each;
4. four frames back to back, faster than they are drawn: the last one shown, the others replaced;
5. a colour changed and redraw written: the same frame drawn whole in the new colour;
6. the fill off and a grid row added (registers), redraw: drawn so."""
import random
import sys

from migen import run_simulation, passive, Signal
from litex.gen import LiteXModule

from panel import Panel, e
import lcd_columns as lc

X0, Y0, W, H = 10, 5, 40, 32
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def render(frame, colours, fill=True, grid_rows=None, grid_cols=None):
    grid_rows = grid_rows if grid_rows is not None else {r for r in range(H) if r % lc.GRID_ROWS == 0}
    grid_cols = grid_cols if grid_cols is not None else {c for c in range(W) if c % lc.GRID_COLS == 0}
    fb = {}
    cl = lambda v: min(v, H - 1)
    for c in range(W):
        cur, lo, hi = (cl(v) for v in frame[c])
        left = cl(frame[c - 1][0]) if c else cur
        jl, jh = min(cur, left), max(cur, left)
        for r in range(H):
            if jl <= r <= jh:
                k = "trace"
            elif lo <= r <= hi:
                k = "band"
            elif fill and r < jl:
                k = "fill"
            elif r in grid_rows or c in grid_cols:
                k = "grid"
            else:
                k = "bg"
            fb[(X0 + c, Y0 + H - 1 - r)] = colours[k]
    return fb


def random_frame(rnd):
    f = []
    for _ in range(W):
        cur = rnd.randrange(0, H + 4)              # some above the window: clamped
        lo = max(0, cur - rnd.randrange(0, 6))
        hi = cur + rnd.randrange(0, 6)
        f.append((cur, lo, hi))
    return f


class FakeSoC:
    sys_clk_freq = 50e6


class Top(LiteXModule):
    def __init__(self):
        soc = FakeSoC()
        self.eng = soc.lcd_engine = e.LCDEngine(50e6, init=e.init_ops(ms=1, clear=(2, 2)))
        self.regs = soc.hls_rpi_display_35 = lc.ColumnRegisters()
        self.en = Signal(reset=1)
        self.cols = lc.LCDColumns(soc, self.en, X0, Y0, W, H)


top = Top()
eng, regs, dut = top.eng, top.regs, top.cols
panel = Panel()
rnd = random.Random(3)
log = []


@passive
def watch():
    while True:
        vals = []
        for sig in (eng.sclk, eng.mosi, eng.cs_n, eng.dc, eng.rst):
            vals.append((yield sig))
        panel.step(*vals)
        yield


def send(frame, gaps=True):
    order = list(range(W))
    rnd.shuffle(order)
    for i, c in enumerate(order):
        cur, lo, hi = frame[c]
        col = c | (0x8000 if i == W - 1 else 0)
        yield dut.sink.valid.eq(1)
        yield dut.sink.data.eq(col | (cur << 16) | (lo << 32) | (hi << 48))
        yield
        yield dut.sink.valid.eq(0)
        if gaps and rnd.random() < 0.2:
            for _ in range(rnd.randint(1, 20)):
                yield


def settle():
    for _ in range(4):
        yield
    while not ((yield dut.idle) and (yield eng.idle)):
        yield
    for _ in range(10):
        yield


def phase(name, colours, **kw):
    words0, cmds0 = panel.words, len(panel.cmds)
    want = render(log[-1], colours, **kw)
    got = {k: panel.fb.get(k) for k in want}
    bad = [(k, got[k], v) for k, v in want.items() if got[k] != v]
    check(not bad, f"{name}: {len(bad)} pixels wrong, e.g. {bad[:4]}")
    return words0, cmds0


def write(csr, v):
    yield csr.storage.eq(v)
    yield csr.re.eq(1)
    yield
    yield csr.re.eq(0)
    yield


stats = {}


def main():
    colours = dict(lc.COLOURS)
    while not (yield eng.ready):
        yield
    f1 = random_frame(rnd)
    log.append(f1)
    yield from send(f1)
    yield from settle()
    phase("first", colours)
    stats["first"] = panel.words
    # the same again: nothing
    w0 = panel.words
    yield from send(f1)
    yield from settle()
    phase("same", colours)
    check(panel.words == w0, f"same: {panel.words - w0} words sent")
    # three columns changed
    f3 = list(f1)
    changed = [5, 17, 30]
    for c in changed:
        cur = (f3[c][0] + 9) % H
        f3[c] = (cur, max(0, cur - 2), cur + 2)
    log.append(f3)
    w0, c0 = panel.words, len(panel.cmds)
    yield from send(f3)
    yield from settle()
    phase("three", colours)
    windows = sum(1 for c, _ in panel.cmds[c0:] if c == 0x2A)
    check(windows <= 2 * len(changed), f"three: {windows} columns sent")
    stats["three"] = panel.words - w0
    # four back to back
    r0 = yield regs.replaced.status
    for _ in range(4):
        f = random_frame(rnd)
        log.append(f)
        yield from send(f, gaps=False)
    yield from settle()
    phase("burst", colours)
    r1 = yield regs.replaced.status
    check(r1 - r0 >= 1, f"burst: {r1 - r0} frames replaced")
    # a colour changed, redraw
    colours["trace"] = 0xF800
    yield regs.col_trace.storage.eq(0xF800)
    yield from write(regs.redraw, 1)
    w0 = panel.words
    yield from send(log[-1])
    yield from settle()
    phase("colour", colours)
    stats["whole"] = panel.words - w0
    # the fill off, a grid row at 7, redraw
    yield regs.fill.storage.eq(0)
    # (the fields of a CSR come from its storage in the CSR bank only: here they are driven)
    g = regs.grid.fields
    yield g.index.eq(7)
    yield g.on.eq(1)
    yield g.column.eq(0)
    yield from write(regs.grid, 0)
    yield from write(regs.redraw, 1)
    yield from send(log[-1])
    yield from settle()
    grid_rows = {r for r in range(H) if r % lc.GRID_ROWS == 0} | {7}
    phase("grid", colours, fill=False, grid_rows=grid_rows)
    stats["frames"] = yield regs.frames.status


run_simulation(top, [watch(), main()])
errors.extend(panel.errors[:5])
print(f"{panel.clock} clocks; words: first frame {stats.get('first')}, three columns {stats.get('three')}, "
      f"whole {stats.get('whole')}; frames drawn {stats.get('frames')}")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("ok")
