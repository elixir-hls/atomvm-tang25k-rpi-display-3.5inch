defmodule EHLS.RPiDisplay35 do
  @moduledoc """
  The 3.5inch RPi Display (ILI9486, 480 x 320, RGB565) and its XPT2046 touch panel for
  elixir-hls / atomvm-tang25k-hls (`EHLS.Blocks`; FPGA side: gateware/hls_rpi_display_35.py, NIFs `:lcd`):

      use EHLS, blocks: [EHLS.RPiDisplay35], peripherals: [:lcd]
      use EHLS, blocks: [{EHLS.RPiDisplay35, pins: [dc: "j6:0"]}], peripherals: [:lcd]

  Then `:lcd.init/0`, `:lcd.fill/5`, `:lcd.text/6`, `:lcd.touch/0` (lib/lcd.erl).
  """
  @behaviour EHLS.Blocks

  @impl true
  def soc, do: :litex

  @impl true
  def blocks, do: %{}

  @impl true
  def peripherals, do: [:lcd]

  @impl true
  def gateware, do: "hls_rpi_display_35"
end
