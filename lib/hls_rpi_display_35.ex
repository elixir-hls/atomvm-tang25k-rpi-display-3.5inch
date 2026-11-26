defmodule EHLS.RPiDisplay35 do
  @moduledoc """
  The 3.5inch RPi Display (ILI9486, 480 x 320, RGB565) and its XPT2046 touch panel for
  elixir-hls / atomvm-tang25k-hls (`EHLS.Blocks`; FPGA side: gateware/hls_rpi_display_35.py and
  gateware/lcd_engine.py, NIFs `:lcd`).

  A drawing engine in the FPGA owns the display: it sets the panel up after reset and runs ops from
  its clients, a transaction at a time. The clients are the CPU (the peripheral `:lcd`, its NIFs) and
  sinks of a `definstance`, so that a pipeline draws with no CPU on the way:

      use EHLS, blocks: [EHLS.RPiDisplay35], peripherals: [:lcd]
      use EHLS, blocks: [{EHLS.RPiDisplay35, pins: [dc: "j6:0"]}], peripherals: [:lcd]

  The CPU: `:lcd.init/0`, `:lcd.fill/5`, `:lcd.text/6`, `:lcd.touch/0`, `:lcd.wait/0`, `:lcd.ops/1`,
  `:lcd.clock/1` (lib/lcd.erl); `fill` and `text` return once their ops are in the engine's FIFO.

  The sinks:

    * `lcd_ops()`: ops (`u32`, gateware/lcd_engine.py), for what a `defhls` function draws itself;
      a transaction ends with an op with bit 27 set.
    * `lcd_pixels(x, y, w, h)`: RGB565 pixels (`u16`) into the window, row by row (a row a
      transaction), the first row again after the last.
    * `lcd_columns(x, y, w, h)`: a graph of `w` columns, `{col, cur, lo, hi}` (`u16` each): the
      column's value and a range, rows from the bottom of the window; bit 15 of `col` marks a
      frame's last element (the columns in any order). Each column shows the trace (joined to the
      column on its left), the band `lo..hi`, a fill under the trace, the grid; only the part of a
      column that changed is sent, and a frame that comes while one waits replaces it. One
      `lcd_columns` a SoC.

  `lcd_columns`' registers (`:hwstream.reg_write(:hw_kernels.reg(:hls_rpi_display_35, name), v)`):
  the colours `col_bg`, `col_grid`, `col_band`, `col_trace`, `col_fill` (RGB565), `fill` (0 / 1),
  `redraw` (any write: the next frame whole, after a colour changed), `grid` (`index ||| column <<< 16
  ||| on <<< 17`: a row from the bottom, or a column, of the grid on or off; every 32 rows and 48
  columns at first); read: `frames` (drawn) and `replaced` (never drawn), 16-bit counts.
  """
  @behaviour EHLS.Blocks

  @impl true
  def soc, do: :litex

  @impl true
  def blocks, do: %{lcd_ops: {:sink, 0}, lcd_pixels: {:sink, 4}, lcd_columns: {:sink, 4}}

  @impl true
  def types,
    do: %{lcd_ops: {:u32, nil}, lcd_pixels: {:u16, nil}, lcd_columns: {{:u16, :u16, :u16, :u16}, nil}}

  @impl true
  def registers do
    %{col_bg: :w, col_grid: :w, col_band: :w, col_trace: :w, col_fill: :w, fill: :w, redraw: :w,
      grid: :w, frames: :r, replaced: :r}
  end

  @impl true
  def peripherals, do: [:lcd]

  @impl true
  def gateware, do: "hls_rpi_display_35"

  @impl true
  def check_stage(block, [x, y, w, h], _table) when block in [:lcd_pixels, :lcd_columns] do
    unless Enum.all?([x, y, w, h], &is_integer/1) and x >= 0 and y >= 0 and w > 0 and h > 0 and
             x + w <= 480 and y + h <= 320 do
      raise ArgumentError, "#{block}(#{x}, #{y}, #{w}, #{h}): a window inside the 480 x 320 screen"
    end
  end

  def check_stage(_, _, _), do: :ok
end
