#!/usr/bin/env python3
"""Migen simulation of the engine's clients (hls_rpi_display_35.py) drawing at once, in disjoint
parts of the screen, with the panel's model:

- the CPU's port: ops written to its CSR (a rectangle, a glyph of BITS), `idle` once they are out;
- lcd_ops: a stream of ops (two rectangles);
- lcd_pixels(20, 4, 5, 3): two pictures of 15 pixels with gaps, then a third stopped after 7 pixels:
  its row ended with black, the CPU's next rectangle still drawn (the display not held), and once
  started again the first row first.
The picture must be what the ops and pixels give."""
import random
import sys

from migen import run_simulation, passive, Signal
from litex.gen import LiteXModule

from panel import Panel, draw, e
import hls_rpi_display_35 as d

errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


class FakeSoC:
    sys_clk_freq = 50e6


class Top(LiteXModule):
    def __init__(self):
        soc = FakeSoC()
        self.eng = soc.lcd_engine = e.LCDEngine(50e6, init=e.init_ops(ms=1, clear=(4, 2)))
        self.penirq = Signal(reset=1)
        self.cpu = d.CPUPort(self.eng, self.penirq)
        self.en_ops, self.en_px = Signal(reset=1), Signal(reset=1)
        self.ops = d.LCDOps(soc, self.en_ops)
        self.px = d.LCDPixels(soc, self.en_px, 20, 4, 5, 3)


top = Top()
eng, cpu = top.eng, top.cpu
panel = Panel()
rnd = random.Random(5)
st = {"done": set()}

cpu_ops1 = [e.cols(0, 3), e.rows(8, 10), e.fg(0x1234), e.fill(12, last=True),
            e.cols(0, 15), e.rows(12, 12), e.fg(0xFFFF), e.bg(0x0001), e.bits(0xA55A, last=True)]
cpu_ops2 = [e.cols(0, 2), e.rows(14, 14), e.fg(0x4321), e.fill(3, last=True)]
stream_ops = [e.cols(40, 42), e.rows(0, 1), e.fg(0x0F0F), e.fill(6, last=True),
              e.cols(44, 44), e.rows(0, 3), e.data(1), e.data(2), e.data(3), e.data(4, last=True)]
pics = [[rnd.getrandbits(16) for _ in range(15)] for _ in range(2)]
stopped = [rnd.getrandbits(16) for _ in range(7)]
restart = [rnd.getrandbits(16) for _ in range(5)]


@passive
def watch():
    while True:
        vals = []
        for sig in (eng.sclk, eng.mosi, eng.cs_n, eng.dc, eng.rst):
            vals.append((yield sig))
        panel.step(*vals)
        yield


def cpu_write(ops):
    for o in ops:
        while (yield cpu.room.status) == 0:
            yield
        yield cpu.op.storage.eq(o)
        yield cpu.op.re.eq(1)
        yield
        yield cpu.op.re.eq(0)
        yield


def wait_idle():
    yield
    yield
    while not (yield cpu.status.fields.idle):
        yield


def send(ep, words, gaps=True):
    for w in words:
        yield ep.valid.eq(1)
        yield ep.data.eq(w)
        yield
        while not (yield ep.ready):
            yield
        yield ep.valid.eq(0)
        if gaps and rnd.random() < 0.3:
            for _ in range(rnd.randint(1, 80)):
                yield
    yield ep.valid.eq(0)


def main():
    while not (yield cpu.status.fields.ready):
        yield
    yield from cpu_write(cpu_ops1)
    yield from wait_idle()
    while "px" not in st["done"] or "ops" not in st["done"]:
        yield
    # stop lcd_pixels after 7 pixels of its third picture: the row is ended, the CPU still draws
    while "stop" not in st:
        yield
    yield top.en_px.eq(0)
    for _ in range(400):
        yield
    yield from cpu_write(cpu_ops2)
    yield from wait_idle()
    yield top.en_px.eq(1)
    st["restart"] = True
    while "px2" not in st["done"]:
        yield
    for _ in range(4000):
        yield


def pixels():
    for p in pics:
        yield from send(top.px.sink, p)
    st["done"].add("px")
    yield from send(top.px.sink, stopped, gaps=False)
    st["stop"] = True
    while "restart" not in st:
        yield
    yield from send(top.px.sink, restart)
    st["done"].add("px2")


def ops():
    yield from send(top.ops.sink, stream_ops)
    st["done"].add("ops")


run_simulation(top, [watch(), main(), pixels(), ops()])

want = draw(e.init_ops(ms=1, clear=(4, 2)))
draw(cpu_ops1 + stream_ops + cpu_ops2, want)
for p in pics:
    for i, c in enumerate(p):
        want[(20 + i % 5, 4 + i // 5)] = c
# the stopped picture: its first row, then the second row's first 2 pixels and black after them
for i, c in enumerate(stopped):
    want[(20 + i % 5, 4 + i // 5)] = c
for i in range(7, 10):
    want[(20 + i % 5, 4 + i // 5)] = 0
# started again: the first row
for i, c in enumerate(restart):
    want[(20 + i, 4)] = c
bad = [(k, panel.fb.get(k), v) for k, v in want.items() if panel.fb.get(k) != v]
check(not bad, f"{len(bad)} pixels wrong, e.g. {bad[:4]}")
extra = sorted(set(panel.fb) - set(want))
check(not extra, f"pixels outside: {extra[:4]}")
errors.extend(panel.errors[:5])
print(f"{panel.clock} clocks, {panel.words} words, {len(want)} pixels")
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("ok")
