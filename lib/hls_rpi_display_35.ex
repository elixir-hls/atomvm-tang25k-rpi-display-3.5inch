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
  """
  @behaviour EHLS.Blocks

  @impl true
  def soc, do: :litex

  @impl true
  def blocks, do: %{lcd_ops: {:sink, 0}, lcd_pixels: {:sink, 4}}

  @impl true
  def types, do: %{lcd_ops: {:u32, nil}, lcd_pixels: {:u16, nil}}

  @impl true
  def peripherals, do: [:lcd]

  @impl true
  def gateware, do: "hls_rpi_display_35"

  @impl true
  def check_stage(:lcd_pixels, [x, y, w, h], _table) do
    unless Enum.all?([x, y, w, h], &is_integer/1) and x >= 0 and y >= 0 and w > 0 and h > 0 and
             x + w <= 480 and y + h <= 320 do
      raise ArgumentError, "lcd_pixels(#{x}, #{y}, #{w}, #{h}): a window inside the 480 x 320 screen"
    end
  end

  def check_stage(_, _, _), do: :ok
end
