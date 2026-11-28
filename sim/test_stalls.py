#!/usr/bin/env python3
"""Migen simulation of the sinks when their instance waits or stops (hls_rpi_display_35.py with the engine and the
panel's model), each scenario from reset:

- px_idle: lcd_pixels enabled, no pixel yet: the CPU's rectangle still goes out (a row starts with its first pixel);
- ops_drop / px_drop: a sink picked by the arbiter, its instance stopped the clock after: the grant goes back, the
  CPU's rectangle goes out;
- reenable: lcd_pixels stopped after 7 pixels of a frame and started again at once: the next frame from the top left.
Prints "ok" or what went wrong."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from migen import run_simulation, Signal, passive  # noqa: E402
from litex.gen import LiteXModule  # noqa: E402
from panel import Panel, e  # noqa: E402
import hls_rpi_display_35 as d  # noqa: E402

errors = []


class FakeSoC:
    sys_clk_freq = 50e6


class Top(LiteXModule):
    def __init__(self):
        soc = FakeSoC()
        self.eng = soc.lcd_engine = e.LCDEngine(50e6, init=e.init_ops(ms=1, clear=(4, 2)))
        self.cpu = d.CPUPort(self.eng, Signal(reset=1))
        self.en_ops, self.en_px = Signal(), Signal()
        self.ops = d.LCDOps(soc, self.en_ops)
        self.px = d.LCDPixels(soc, self.en_px, 20, 4, 5, 3)


def scenario(name):
    top = Top()
    eng, cpu = top.eng, top.cpu
    panel = Panel()
    res = {}

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
            yield cpu.op.storage.eq(o)
            yield cpu.op.re.eq(1)
            yield
            yield cpu.op.re.eq(0)
            yield

    def send(ep, words):
        for w in words:
            yield ep.valid.eq(1)
            yield ep.data.eq(w)
            yield
            while not (yield ep.ready):
                yield
            yield ep.valid.eq(0)

    rect = [e.cols(0, 3), e.rows(8, 8), e.fg(1), e.fill(4, last=True)]

    def main():
        while not (yield cpu.status.fields.ready):
            yield
        if name == "px_idle":
            yield top.en_px.eq(1)
            for _ in range(50):
                yield
        elif name == "ops_drop":
            yield top.ops.sink.valid.eq(1)
            yield top.ops.sink.data.eq(e.cols(0, 1))
            yield top.en_ops.eq(1)
            yield
            yield top.en_ops.eq(0)
            yield top.ops.sink.valid.eq(0)
            for _ in range(5):
                yield
        elif name == "px_drop":
            yield top.en_px.eq(1)
            yield
            yield top.en_px.eq(0)
            for _ in range(5):
                yield
        elif name == "reenable":
            yield top.en_px.eq(1)
            yield from send(top.px.sink, list(range(100, 107)))
            yield top.en_px.eq(0)
            for _ in range(3):
                yield
            yield top.en_px.eq(1)
            yield from send(top.px.sink, list(range(200, 215)))
            for _ in range(3000):
                yield
            res["rows"] = [[panel.fb.get((20 + c, 4 + r)) for c in range(5)] for r in range(3)]
            return
        yield from cpu_write(rect)
        for _ in range(20000):
            yield
            if (yield cpu.status.fields.idle):
                res["idle"] = True
                break

    run_simulation(top, [main(), watch()])
    errors.extend(f"{name}: {m}" for m in panel.errors[:3])
    if name == "reenable":
        want = [list(range(200 + 5 * r, 205 + 5 * r)) for r in range(3)]
        if res["rows"] != want:
            errors.append(f"reenable: rows {res['rows']}, want {want}")
    elif not res.get("idle"):
        errors.append(f"{name}: the CPU's rectangle did not go out")
    elif any(panel.fb.get((x, 8)) != 1 for x in range(4)):
        errors.append(f"{name}: the CPU's rectangle not drawn")


for s in ["px_idle", "ops_drop", "px_drop", "reenable"]:
    scenario(s)

for m in errors:
    print("ERROR", m)
print("ok" if not errors else "errors")
sys.exit(1 if errors else 0)
