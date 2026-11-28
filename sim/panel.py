"""Models for the simulations: the ILI9486 of the 3.5inch RPi Display as its SPI pins show it (16-bit
words, DC, CASET / PASET / RAMWR into a picture), the XPT2046 behind the touch's CS (a value for each
command), and the ops run on a picture in Python (what the engine should draw)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gateware"))
import lcd_engine as e  # noqa: E402


class Panel:
    """Watches the pins every clock (`step`): an SCLK rise with the LCD's CS low is a bit of a word
    to the ILI9486, with the touch's CS low a bit to the XPT2046 (and MISO is driven for the next)."""
    def __init__(self, touch_values=None):
        self.fb = {}
        self.cmds = []           # (command, [parameters])
        self.words = 0
        self.rst_low = 0         # clocks with the reset pin low
        self.touch_values = touch_values or {}
        self.touch_cmds = []
        self.miso = 0
        self.errors = []
        self.word_at = []
        self.clock = 0
        self._prev = 0
        self._bits = 0
        self._n = 0
        self._tbits = 0
        self._tn = 0
        self._cs = 3
        self.xs = self.xe = self.ys = self.ye = self.x = self.y = 0
        self._ram = False

    def _word(self, dc, w):
        self.words += 1
        self.word_at.append(self.clock)
        if not dc:
            self.cmds.append((w & 0xFF, []))
            self._ram = (w & 0xFF) == 0x2C
            if self._ram:
                self.x, self.y = self.xs, self.ys
            return
        if self._ram:
            self.fb[(self.x, self.y)] = w
            self.x += 1
            if self.x > self.xe:
                self.x = self.xs
                self.y += 1
            return
        if not self.cmds:
            self.errors.append(f"data {w:#x} before any command")
            return
        c, p = self.cmds[-1]
        p.append(w & 0xFF)
        if w >> 8:
            self.errors.append(f"parameter {w:#x} of {c:#x} not 0x00..")
        if c == 0x2A and len(p) == 4:
            self.xs, self.xe = (p[0] << 8) | p[1], (p[2] << 8) | p[3]
        if c == 0x2B and len(p) == 4:
            self.ys, self.ye = (p[0] << 8) | p[1], (p[2] << 8) | p[3]

    def step(self, sclk, mosi, cs_n, dc, rst):
        self.clock += 1
        if not rst:
            self.rst_low += 1
        if (cs_n & 1) and not (self._cs & 1) and self._n:
            self.errors.append(f"LCD CS up in a word ({self._n} bits)")
        if (cs_n & 2) and not (self._cs & 2):
            if self._tn:
                self.errors.append(f"touch CS up in a transfer ({self._tn} bits)")
            self._tn = 0
        if (cs_n & 1) == 0 and (cs_n & 2) == 0:
            self.errors.append("both CS low")
        self._cs = cs_n
        rise = sclk and not self._prev
        self._prev = sclk
        if not rise:
            return
        if not (cs_n & 1):
            self._bits = (self._bits << 1) | mosi
            self._n += 1
            if self._n == 16:
                self._word(dc, self._bits)
                self._bits = self._n = 0
        elif not (cs_n & 2):
            self._tbits = (self._tbits << 1) | mosi
            self._tn += 1
            if self._tn == 8:
                self.touch_cmds.append(self._tbits & 0xFF)
            if self._tn == 24:
                self._tbits = self._tn = 0
            # the next bit back: the 16 after the command are value << 3
            i = self._tn
            if 8 <= i < 24:
                v = (self.touch_values.get(self.touch_cmds[-1], 0) << 3) & 0xFFFF
                self.miso = (v >> (23 - i)) & 1
            else:
                self.miso = 0


def draw(ops, fb=None):
    """The ops run on a picture: what the panel should show."""
    fb = {} if fb is None else fb
    st = dict(xs=0, xe=0, ys=0, ye=0, x=0, y=0, fg=0, bg=0, ram=False)

    def px(c):
        fb[(st["x"], st["y"])] = c
        st["x"] += 1
        if st["x"] > st["xe"]:
            st["x"] = st["xs"]
            st["y"] += 1
    for o in ops:
        code, p = o >> 28, o & 0x7FFFFFF
        if code == e.OP_COLS:
            st["xs"], st["xe"] = (p >> 9) & 0x1FF, p & 0x1FF
        elif code == e.OP_ROWS:
            st["ys"], st["ye"] = (p >> 9) & 0x1FF, p & 0x1FF
            st["x"], st["y"], st["ram"] = st["xs"], st["ys"], True
        elif code == e.OP_CMD:
            st["ram"] = (p & 0xFF) == 0x2C
            st["x"], st["y"] = st["xs"], st["ys"]
        elif code == e.OP_COLS:
            pass
        elif code == e.OP_FG:
            st["fg"] = p & 0xFFFF
        elif code == e.OP_BG:
            st["bg"] = p & 0xFFFF
        elif code == e.OP_FILL:
            for _ in range(p & 0xFFFFFF):
                px(st["fg"])
        elif code == e.OP_BITS:
            n = ((p >> 16) & 0xF) + 1
            for i in range(n):
                px(st["fg"] if (p >> (15 - i)) & 1 else st["bg"])
        elif code == e.OP_DATA and st["ram"]:
            px(p & 0xFFFF)
    return fb
