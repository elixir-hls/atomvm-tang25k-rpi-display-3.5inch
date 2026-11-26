#!/usr/bin/env python3
"""Migen simulation of lcd_engine.LCDEngine with the panel's model (panel.py):

1. the init ROM after reset: the reset pulse, the commands and parameters in order, the area cleared,
   `ready` and an `epoch` once it is out;
2. two clients giving windows of FILL / BITS / DATA at random, with random gaps and waits (not
   ready): every transaction drawn whole (their windows do not overlap, so the order does not
   matter; an interleaving would put pixels in the other's window), the picture the ops' picture;
3. TOUCH ops: the commands on the touch's CS at its clock, the values back in the FIFO;
at the LCD's divider 3 and 2."""
import random
import sys

from migen import run_simulation, passive
from litex.gen import LiteXModule
from litex.soc.interconnect import stream

from panel import Panel, draw, e

errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def rect_ops(x, y, w, h, rnd):
    """A window filled one of three ways."""
    o = [e.cols(x, x + w - 1), e.rows(y, y + h - 1)]
    kind = rnd.randrange(3)
    if kind == 0:
        o += [e.fg(rnd.getrandbits(16)), e.fill(w * h)]
    elif kind == 1:
        o += [e.fg(rnd.getrandbits(16)), e.bg(rnd.getrandbits(16))]
        left = w * h
        while left:
            n = min(left, rnd.randint(1, 16))
            o.append(e.bits(rnd.getrandbits(16), n))
            left -= n
    else:
        o += [e.data(rnd.getrandbits(16)) for _ in range(w * h)]
    o[-1] |= e.LAST
    return o


class Top(LiteXModule):
    def __init__(self, init):
        self.eng = e.LCDEngine(50e6, init=init)
        self.c1 = stream.Endpoint([("data", 32)])
        self.c2 = stream.Endpoint([("data", 32)])
        self.eng.add_client(self.c1)
        self.eng.add_client(self.c2)


def run(div):
    rnd = random.Random(div)
    init = e.init_ops(ms=1, clear=(6, 3))
    top = Top(init)
    eng = top.eng
    tv = {0xD1: 0x5A5, 0x91: 0xA5A, 0xB1: 0x123}
    panel = Panel(tv)
    # client 1: rectangles in x 0..39, client 2 in x 40..79, then touch ops from client 2
    txns1 = [rect_ops(rnd.randrange(0, 30), rnd.randrange(0, 20), rnd.randint(1, 8), rnd.randint(1, 4), rnd)
             for _ in range(6)]
    txns2 = [rect_ops(40 + rnd.randrange(0, 30), rnd.randrange(0, 20), rnd.randint(1, 8), rnd.randint(1, 4), rnd)
             for _ in range(6)]
    touch = [e.touch(0xB1), e.touch(0xD1), e.touch(0x91), e.touch(0x90, last=True)]
    state = dict(epochs=0, ready_at=None, cycles=0, touch=[])

    @passive
    def watch():
        while True:
            vals = []
            for sig in (eng.sclk, eng.mosi, eng.cs_n, eng.dc, eng.rst):
                vals.append((yield sig))
            panel.step(*vals)
            yield eng.miso.eq(panel.miso)
            if (yield eng.epoch):
                state["epochs"] += 1
            if state["ready_at"] is None and (yield eng.ready):
                state["ready_at"] = state["cycles"]
            if (yield eng.touch.source.valid):
                state["touch"].append((yield eng.touch.source.data))
            state["cycles"] += 1
            yield

    def client(ep, txns):
        yield eng.div_lcd.eq(div)
        for t in txns:
            for w in t:
                yield ep.valid.eq(1)
                yield ep.data.eq(w)
                yield
                while not (yield ep.ready):
                    yield
                if rnd.random() < 0.2:
                    yield ep.valid.eq(0)
                    for _ in range(rnd.randint(1, 60)):
                        yield
            yield ep.valid.eq(0)
            for _ in range(rnd.randint(0, 200)):
                yield
        yield ep.valid.eq(0)

    def main():
        yield eng.touch.source.ready.eq(1)
        yield from client(top.c1, txns1)
        while not state.get("second_done"):
            yield
        while not (yield eng.idle):
            yield
        for _ in range(50):
            yield

    def second():
        yield from client(top.c2, txns2 + [touch])
        state["second_done"] = True

    run_simulation(top, [watch(), main(), second()])
    # the init
    want = [(c >> 28, c & 0xFF) for c in init if c >> 28 == e.OP_CMD]
    got = [c for c, _ in panel.cmds]
    check(got[:len(want)] == [c for _, c in want], f"div {div}: init commands {[hex(c) for c in got[:len(want)]]}")
    params = {c: p for c, p in panel.cmds[:len(want)]}
    check(params.get(0x3A) == [0x55] and params.get(0x36) == [0x28] and params.get(0xE0) == e.PG
          and params.get(0xE1) == e.NG and params.get(0xC5) == [0, 0, 0, 0], f"div {div}: init parameters {params}")
    check(19 * 50 <= panel.rst_low <= 21 * 50 + 10, f"div {div}: reset low {panel.rst_low} clocks")
    check(state["epochs"] == 1, f"div {div}: {state['epochs']} epochs")
    check(state["ready_at"] is not None, f"div {div}: never ready")
    # the picture
    want_fb = draw(init)
    for t in txns1 + txns2:
        draw(t, want_fb)
    bad = [(k, panel.fb.get(k), v) for k, v in want_fb.items() if panel.fb.get(k) != v]
    check(not bad, f"div {div}: {len(bad)} pixels wrong, e.g. {bad[:3]}")
    extra = set(panel.fb) - set(want_fb)
    check(not extra, f"div {div}: pixels drawn outside: {sorted(extra)[:3]}")
    # the touch
    check(panel.touch_cmds == [0xB1, 0xD1, 0x91, 0x90], f"div {div}: touch commands {panel.touch_cmds}")
    check(state["touch"] == [0x123, 0x5A5, 0xA5A, 0], f"div {div}: touch values {state['touch']}")
    # the init's clear (a FILL) goes out with no gap: a word every 16 SCLKs
    gaps = [b - a for a, b in zip(panel.word_at, panel.word_at[1:])]
    check(min(gaps) == 16 * div and gaps.count(16 * div) >= 6 * 3 - 1, f"div {div}: word spacing {sorted(set(gaps))[:5]}")
    errors.extend(f"div {div}: {m}" for m in panel.errors[:5])
    print(f"div {div}: {state['cycles']} clocks, {panel.words} words, {len(want_fb)} pixels, ready at {state['ready_at']}")


for div in (3, 2):
    run(div)
if errors:
    print("\n".join(errors))
    sys.exit(1)
print("ok")
