/* Built only when the SoC has the LCD's CPU port (lcd_csr.h, csr_headers.json). */
#if __has_include("lcd_csr.h")
/*
 * NIFs for the 3.5inch RPi Display (ILI9486, 480 x 320, RGB565) and its XPT2046 touch panel, on the
 * CPU's port of the drawing engine (gateware/hls_rpi_display_35.py CPUPort, gateware/lcd_engine.py):
 * a call puts ops into the engine's FIFO (512) and returns; it waits only while the FIFO is full.
 *
 *   lcd:init() -> ok
 *       The panel set up again and cleared to black (the engine does it after reset too); returns
 *       once it is done (~0.5 s).
 *   lcd:fill(X, Y, W, H, Color) -> ok
 *       A rectangle in RGB565 (4 ops).
 *   lcd:text(X, Y, Text :: binary(), Fg, Bg, Scale) -> NextX
 *       Text in the 16 x 32 Terminus font (ASCII 32..126, others as '?'), each pixel
 *       Scale x Scale; stops at the right edge. Returns the x after the text.
 *   lcd:touch() -> {X, Y} | false
 *       The screen position while the panel is pressed (median of 5 samples), else false.
 *   lcd:wait() -> ok
 *       Until the CPU's ops are all out.
 *   lcd:ops([Op]) -> ok
 *       Any ops (32-bit integers, as lcd_engine.py documents them); the last should end the
 *       transaction (bit 27), as the other clients wait until it does.
 *   lcd:clock(Div) -> ok
 *       The LCD's SCLK divider (>= 2): 3 by default (16.7 MHz at 50 MHz), 2 for 25 MHz.
 *
 * A transaction (a rectangle, a character, a touch reading) goes out whole; the sinks of a
 * pipeline draw between them.
 */
#include <stdint.h>
#include <string.h>

#include <defaultatoms.h>
#include <memory.h>
#include <nifs.h>
#include <portnifloader.h>
#include <term.h>

#include <zephyr/kernel.h>
#include <zephyr/sys/sys_io.h>
#include <zephyr/sys/util.h>

#include "font.h"
#include "lcd_csr.h"

#define LCD_W 480
#define LCD_H 320

#define ST_READY BIT(0)
#define ST_IDLE BIT(1)
#define ST_PENIRQ BIT(2)
#define TOUCH_VALID BIT(16)

/* the ops (gateware/lcd_engine.py) */
enum { OP_CMD = 1, OP_DATA, OP_COLS, OP_ROWS, OP_FG, OP_BG, OP_FILL, OP_BITS, OP_WAIT, OP_RST, OP_TOUCH };
#define LAST BIT(27)
#define OP(code, payload) (((uint32_t) (code) << 28) | (uint32_t) (payload))

static uint32_t room;

static void push(uint32_t op)
{
    while (room == 0) {
        room = sys_read32(CSR_LCD_ROOM_ADDR);
    }
    sys_write32(op, CSR_LCD_OP_ADDR);
    room--;
}

/* the window [x, x + w) x [y, y + h): pixels follow */
static void window(int x, int y, int w, int h)
{
    push(OP(OP_COLS, ((uint32_t) x << 9) | (uint32_t) (x + w - 1)));
    push(OP(OP_ROWS, ((uint32_t) y << 9) | (uint32_t) (y + h - 1)));
}

static void lcd_fill(int x, int y, int w, int h, uint16_t color)
{
    /* clip to the screen */
    if (x < 0) {
        w += x;
        x = 0;
    }
    if (y < 0) {
        h += y;
        y = 0;
    }
    w = MIN(w, LCD_W - x);
    h = MIN(h, LCD_H - y);
    if (w <= 0 || h <= 0) {
        return;
    }
    window(x, y, w, h);
    push(OP(OP_FG, color));
    push(OP(OP_FILL, (uint32_t) (w * h)) | LAST);
}

static void wait_idle(void)
{
    while (!(sys_read32(CSR_LCD_STATUS_ADDR) & ST_IDLE)) {
        k_yield();
    }
}

static term nif_lcd_init(Context *ctx, int argc, term argv[])
{
    UNUSED(ctx);
    UNUSED(argc);
    UNUSED(argv);
    wait_idle();
    sys_write32(1, CSR_LCD_INIT_ADDR);
    while (!(sys_read32(CSR_LCD_STATUS_ADDR) & ST_READY)) {
        k_msleep(1);
    }
    return OK_ATOM;
}

static term nif_lcd_fill(Context *ctx, int argc, term argv[])
{
    UNUSED(argc);
    for (int i = 0; i < 5; i++) {
        VALIDATE_VALUE(argv[i], term_is_integer);
    }
    lcd_fill(term_to_int(argv[0]), term_to_int(argv[1]), term_to_int(argv[2]), term_to_int(argv[3]),
        (uint16_t) term_to_int(argv[4]));
    return OK_ATOM;
}

static term nif_lcd_text(Context *ctx, int argc, term argv[])
{
    UNUSED(argc);
    VALIDATE_VALUE(argv[0], term_is_integer);
    VALIDATE_VALUE(argv[1], term_is_integer);
    VALIDATE_VALUE(argv[2], term_is_binary);
    VALIDATE_VALUE(argv[3], term_is_integer);
    VALIDATE_VALUE(argv[4], term_is_integer);
    VALIDATE_VALUE(argv[5], term_is_integer);
    int x = term_to_int(argv[0]), y = term_to_int(argv[1]);
    const uint8_t *s = (const uint8_t *) term_binary_data(argv[2]);
    size_t len = term_binary_size(argv[2]);
    uint16_t fgc = (uint16_t) term_to_int(argv[3]), bgc = (uint16_t) term_to_int(argv[4]);
    int scale = term_to_int(argv[5]);
    if (scale < 1 || scale > 8 || x < 0 || y < 0 || y + FONT_H * scale > LCD_H) {
        RAISE_ERROR(BADARG_ATOM);
    }

    for (size_t i = 0; i < len && x + FONT_W * scale <= LCD_W; i++, x += FONT_W * scale) {
        int c = (s[i] < FONT_FIRST || s[i] > FONT_LAST) ? '?' : s[i];
        const uint8_t *g = font_bits[c - FONT_FIRST];

        /* a character a transaction: its window, the colours, a BITS op every 16 pixels */
        window(x, y, FONT_W * scale, FONT_H * scale);
        push(OP(OP_FG, fgc));
        push(OP(OP_BG, bgc));
        for (int r = 0; r < FONT_H * scale; r++) {
            const uint8_t *row = g + (r / scale) * FONT_ROW_BYTES;
            uint32_t bits = 0;
            int n = 0;

            for (int col = 0; col < FONT_W * scale; col++) {
                int b = col / scale;

                bits = (bits << 1) | ((row[b >> 3] & (0x80 >> (b & 7))) ? 1u : 0u);
                if (++n == 16) {
                    int end = (r == FONT_H * scale - 1) && (col == FONT_W * scale - 1);

                    push(OP(OP_BITS, (15u << 16) | bits) | (end ? LAST : 0));
                    bits = 0;
                    n = 0;
                }
            }
        }
    }
    return term_from_int(x);
}

/* a touch reading, one transaction: Z1, X and Y five times, then PD = 00 (PENIRQ enabled again) */
static int touch_read(int *z1, int *rx, int *ry)
{
    int v[12];

    push(OP(OP_TOUCH, 0xb1));
    for (int i = 0; i < 5; i++) {
        push(OP(OP_TOUCH, 0xd1));
        push(OP(OP_TOUCH, 0x91));
    }
    push(OP(OP_TOUCH, 0x90) | LAST);
    for (int i = 0; i < 12; i++) {
        uint32_t t;
        int64_t start = k_uptime_get();

        while (!((t = sys_read32(CSR_LCD_TOUCH_ADDR)) & TOUCH_VALID)) {
            if (k_uptime_get() - start > 100) {
                return -1;
            }
            k_yield();
        }
        sys_write32(1, CSR_LCD_TOUCH_POP_ADDR);
        v[i] = (int) (t & 0xfff);
    }
    *z1 = v[0];
    for (int i = 0; i < 5; i++) {
        rx[i] = v[1 + 2 * i];
        ry[i] = v[2 + 2 * i];
    }
    return 0;
}

static int median5(int *v)
{
    for (int i = 1; i < 5; i++) {
        for (int j = i; j > 0 && v[j - 1] > v[j]; j--) {
            int t = v[j];

            v[j] = v[j - 1];
            v[j - 1] = t;
        }
    }
    return v[2];
}

static term nif_lcd_touch(Context *ctx, int argc, term argv[])
{
    UNUSED(argc);
    UNUSED(argv);
    int rx[5], ry[5], z1;

    if (sys_read32(CSR_LCD_STATUS_ADDR) & ST_PENIRQ) {
        return FALSE_ATOM; /* PENIRQ high: not pressed */
    }
    if (touch_read(&z1, rx, ry) < 0 || z1 < 80) {
        return FALSE_ATOM; /* no answer, or too light: the position is not reliable */
    }
    /* calibration (usb-rtl-sdr-tang25k docs/lcd.md): screen x follows raw Y reversed, y raw X */
    int x = CLAMP(40 + (3610 - median5(ry)) * 400 / 3110, 0, LCD_W - 1);
    int y = CLAMP(40 + (median5(rx) - 665) * 240 / 2735, 0, LCD_H - 1);

    if (UNLIKELY(memory_ensure_free(ctx, TUPLE_SIZE(2)) != MEMORY_GC_OK)) {
        RAISE_ERROR(OUT_OF_MEMORY_ATOM);
    }
    term t = term_alloc_tuple(2, &ctx->heap);
    term_put_tuple_element(t, 0, term_from_int(x));
    term_put_tuple_element(t, 1, term_from_int(y));
    return t;
}

static term nif_lcd_wait(Context *ctx, int argc, term argv[])
{
    UNUSED(ctx);
    UNUSED(argc);
    UNUSED(argv);
    wait_idle();
    return OK_ATOM;
}

static term nif_lcd_ops(Context *ctx, int argc, term argv[])
{
    UNUSED(argc);
    term l = argv[0];
    VALIDATE_VALUE(l, term_is_list);
    /* all checked first: a bad element must not leave a transaction open */
    for (term t = l; term_is_nonempty_list(t); t = term_get_list_tail(t)) {
        term h = term_get_list_head(t);
        if (!term_is_any_integer(h)) {
            RAISE_ERROR(BADARG_ATOM);
        }
        avm_int64_t v = term_maybe_unbox_int64(h);
        if (v < 0 || v > 0xffffffffLL) {
            RAISE_ERROR(BADARG_ATOM);
        }
    }
    for (term t = l; term_is_nonempty_list(t); t = term_get_list_tail(t)) {
        push((uint32_t) term_maybe_unbox_int64(term_get_list_head(t)));
    }
    return OK_ATOM;
}

static term nif_lcd_clock(Context *ctx, int argc, term argv[])
{
    UNUSED(argc);
    VALIDATE_VALUE(argv[0], term_is_integer);
    int d = term_to_int(argv[0]);
    if (d < 2 || d > 255) {
        RAISE_ERROR(BADARG_ATOM);
    }
    wait_idle();
    sys_write32((uint32_t) d, CSR_LCD_DIV_ADDR);
    return OK_ATOM;
}

static const struct Nif lcd_init_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_init };
static const struct Nif lcd_fill_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_fill };
static const struct Nif lcd_text_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_text };
static const struct Nif lcd_touch_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_touch };
static const struct Nif lcd_wait_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_wait };
static const struct Nif lcd_ops_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_ops };
static const struct Nif lcd_clock_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_clock };

static const struct Nif *lcd_get_nif(const char *nifname)
{
    if (strcmp("lcd:init/0", nifname) == 0) {
        return &lcd_init_nif;
    }
    if (strcmp("lcd:fill/5", nifname) == 0) {
        return &lcd_fill_nif;
    }
    if (strcmp("lcd:text/6", nifname) == 0) {
        return &lcd_text_nif;
    }
    if (strcmp("lcd:touch/0", nifname) == 0) {
        return &lcd_touch_nif;
    }
    if (strcmp("lcd:wait/0", nifname) == 0) {
        return &lcd_wait_nif;
    }
    if (strcmp("lcd:ops/1", nifname) == 0) {
        return &lcd_ops_nif;
    }
    if (strcmp("lcd:clock/1", nifname) == 0) {
        return &lcd_clock_nif;
    }
    return NULL;
}

REGISTER_NIF_COLLECTION(lcd, NULL, NULL, lcd_get_nif)
#endif
