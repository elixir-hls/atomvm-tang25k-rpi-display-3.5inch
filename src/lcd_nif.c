/* Built only when the SoC has the LCD (lcd_csr.h, csr_headers.json). */
#if __has_include("lcd_csr.h")
/*
 * NIFs for the 3.5inch RPi Display (ILI9486, 480 x 320, RGB565) and its XPT2046 touch panel
 * (gateware/hls_rpi_display_35.py: lcd_spi, lcd_ctl, lcd_irq). From usb-rtl-sdr-tang25k's
 * apps/sdr/src/lcd.c (protocol and touch calibration: its docs/lcd.md).
 *
 *   lcd:init() -> ok
 *       Reset and set up the panel (landscape), clear it to black (~0.6 s).
 *   lcd:fill(X, Y, W, H, Color) -> ok
 *       A rectangle in RGB565.
 *   lcd:text(X, Y, Text :: binary(), Fg, Bg, Scale) -> NextX
 *       Text in the 16 x 32 Terminus font (ASCII 32..126, others as '?'), each pixel
 *       Scale x Scale; stops at the right edge. Returns the x after the text.
 *   lcd:touch() -> {X, Y} | false
 *       The screen position while the panel is pressed (median of 5 samples), else false.
 *
 * The SPI master is polled: a call holds the CPU while its pixels go out (a 16 x 32
 * character ~1 ms, the whole screen ~0.25 s at ~12.5 MHz).
 *
 * ILI9486 on this board: every command and parameter byte goes out as a 16-bit word (0x00, v);
 * pixels are RGB565, two per 32-bit transfer. XPT2046: 24-bit transfers (command, 16 bits
 * back, value = bits [15:3]) at ~1.6 MHz.
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

#define SPI_START BIT(0)
#define SPI_DONE BIT(0)
#define CS_MANUAL BIT(16)
#define CS_LCD BIT(0)
#define CS_TOUCH BIT(1)
#define DIV_LCD 4
#define DIV_TOUCH 32
#define CTL_DC BIT(0)
#define CTL_RST BIT(1)

static uint32_t ctl = CTL_RST;

static uint32_t spi_xfer(int bits, uint32_t data)
{
    sys_write32(data, CSR_LCD_SPI_MOSI_ADDR);
    sys_write32(SPI_START | ((uint32_t) bits << 8), CSR_LCD_SPI_CONTROL_ADDR);
    while (!(sys_read32(CSR_LCD_SPI_STATUS_ADDR) & SPI_DONE)) {
    }
    return sys_read32(CSR_LCD_SPI_MISO_ADDR);
}

static void dc(int data)
{
    ctl = data ? (ctl | CTL_DC) : (ctl & ~CTL_DC);
    sys_write32(ctl, CSR_LCD_CTL_OUT_ADDR);
}

static void lcd_select(int on)
{
    sys_write32(CS_MANUAL | (on ? CS_LCD : 0u), CSR_LCD_SPI_CS_ADDR);
}

static void lcd_cmd(uint8_t c, const uint8_t *p, int n)
{
    dc(0);
    spi_xfer(16, c);
    dc(1);
    for (int i = 0; i < n; i++) {
        spi_xfer(16, p[i]);
    }
}

/* address window [x, x + w) x [y, y + h), then memory write: pixels follow */
static void lcd_window(int x, int y, int w, int h)
{
    uint8_t col[4] = { x >> 8, x & 0xff, (x + w - 1) >> 8, (x + w - 1) & 0xff };
    uint8_t row[4] = { y >> 8, y & 0xff, (y + h - 1) >> 8, (y + h - 1) & 0xff };

    lcd_cmd(0x2a, col, 4);
    lcd_cmd(0x2b, row, 4);
    lcd_cmd(0x2c, NULL, 0);
}

/* pixels, two per 32-bit transfer */
static uint16_t pend;
static int npend;

static void px(uint16_t c)
{
    if (npend) {
        spi_xfer(32, ((uint32_t) pend << 16) | c);
        npend = 0;
    } else {
        pend = c;
        npend = 1;
    }
}

static void px_flush(void)
{
    if (npend) {
        spi_xfer(16, pend);
        npend = 0;
    }
}

static void lcd_fill(int x, int y, int w, int h, uint16_t color)
{
    uint32_t pair = ((uint32_t) color << 16) | color;

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
    int n = w * h;
    sys_write32(DIV_LCD, CSR_LCD_SPI_CLK_DIVIDER_ADDR);
    lcd_select(1);
    lcd_window(x, y, w, h);
    for (; n >= 2; n -= 2) {
        spi_xfer(32, pair);
    }
    if (n) {
        spi_xfer(16, color);
    }
    lcd_select(0);
}

/* power and gamma (the Linux waveshare35a overlay's values; without them a grey ramp jumps) */
static void lcd_gamma_init(void)
{
    static const uint8_t pwr3 = 0x44, vcom[4] = { 0, 0, 0, 0 };
    static const uint8_t pg[15] = { 0x0f, 0x1f, 0x1c, 0x0c, 0x0f, 0x08, 0x48, 0x98, 0x37, 0x0a, 0x13,
        0x04, 0x11, 0x0d, 0x00 };
    static const uint8_t ng[15] = { 0x0f, 0x32, 0x2e, 0x0b, 0x0d, 0x05, 0x47, 0x75, 0x37, 0x06, 0x10,
        0x03, 0x24, 0x20, 0x00 };

    lcd_cmd(0xc2, &pwr3, 1);
    lcd_cmd(0xc5, vcom, 4);
    lcd_cmd(0xe0, pg, 15);
    lcd_cmd(0xe1, ng, 15);
    lcd_cmd(0xe2, ng, 15);
}

static term nif_lcd_init(Context *ctx, int argc, term argv[])
{
    UNUSED(ctx);
    UNUSED(argc);
    UNUSED(argv);
    static const uint8_t pixfmt = 0x55, madctl = 0x28;

    sys_write32(DIV_LCD, CSR_LCD_SPI_CLK_DIVIDER_ADDR);
    lcd_select(0);
    ctl = 0; /* hardware reset pulse */
    sys_write32(ctl, CSR_LCD_CTL_OUT_ADDR);
    k_msleep(20);
    ctl = CTL_RST;
    sys_write32(ctl, CSR_LCD_CTL_OUT_ADDR);
    k_msleep(150);
    lcd_select(1);
    lcd_cmd(0x01, NULL, 0); /* software reset */
    k_msleep(150);
    lcd_cmd(0x11, NULL, 0); /* sleep out */
    k_msleep(150);
    lcd_cmd(0x3a, &pixfmt, 1); /* 16 bit / pixel */
    lcd_cmd(0x36, &madctl, 1); /* landscape */
    lcd_gamma_init();
    lcd_cmd(0x29, NULL, 0); /* display on */
    lcd_select(0);
    lcd_fill(0, 0, LCD_W, LCD_H, 0);
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
    uint16_t fg = (uint16_t) term_to_int(argv[3]), bg = (uint16_t) term_to_int(argv[4]);
    int scale = term_to_int(argv[5]);
    if (scale < 1 || scale > 8 || x < 0 || y < 0 || y + FONT_H * scale > LCD_H) {
        RAISE_ERROR(BADARG_ATOM);
    }

    sys_write32(DIV_LCD, CSR_LCD_SPI_CLK_DIVIDER_ADDR);
    lcd_select(1);
    for (size_t i = 0; i < len && x + FONT_W * scale <= LCD_W; i++, x += FONT_W * scale) {
        int c = (s[i] < FONT_FIRST || s[i] > FONT_LAST) ? '?' : s[i];
        const uint8_t *g = font_bits[c - FONT_FIRST];

        lcd_window(x, y, FONT_W * scale, FONT_H * scale);
        for (int r = 0; r < FONT_H * scale; r++) {
            const uint8_t *row = g + (r / scale) * FONT_ROW_BYTES;

            for (int col = 0; col < FONT_W * scale; col++) {
                int b = col / scale;

                px((row[b >> 3] & (0x80 >> (b & 7))) ? fg : bg);
            }
        }
        px_flush();
    }
    lcd_select(0);
    return term_from_int(x);
}

static int touch_cmd(uint8_t c)
{
    return (spi_xfer(24, (uint32_t) c << 16) & 0xffff) >> 3;
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

    if (sys_read32(CSR_LCD_IRQ_IN_ADDR) & 1) {
        return FALSE_ATOM; /* PENIRQ high: not pressed */
    }
    sys_write32(DIV_TOUCH, CSR_LCD_SPI_CLK_DIVIDER_ADDR);
    sys_write32(CS_MANUAL | CS_TOUCH, CSR_LCD_SPI_CS_ADDR);
    z1 = touch_cmd(0xb1);
    for (int i = 0; i < 5; i++) {
        rx[i] = touch_cmd(0xd1);
        ry[i] = touch_cmd(0x91);
    }
    touch_cmd(0x90); /* PD = 00: PENIRQ enabled again */
    sys_write32(CS_MANUAL, CSR_LCD_SPI_CS_ADDR);
    if (z1 < 80) {
        return FALSE_ATOM; /* too light: the position is not reliable */
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

static const struct Nif lcd_init_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_init };
static const struct Nif lcd_fill_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_fill };
static const struct Nif lcd_text_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_text };
static const struct Nif lcd_touch_nif = { .base.type = NIFFunctionType, .nif_ptr = nif_lcd_touch };

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
    return NULL;
}

REGISTER_NIF_COLLECTION(lcd, NULL, NULL, lcd_get_nif)
#endif
