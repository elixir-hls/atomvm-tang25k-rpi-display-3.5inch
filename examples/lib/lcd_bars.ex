defmodule LcdBars do
  @moduledoc """
  The LCD test pattern of usb-rtl-sdr-tang25k's `lcd bars`: 8 colour bars (white, yellow,
  cyan, green, magenta, red, blue, black) on top, ramps of grey, red, green and blue in 32
  steps below (dark on the left), to check the panel's gamma.
  Runs on any SoC with the `:lcd` peripheral (an application with
  `use EHLS, blocks: [EHLS.RPiDisplay35], peripherals: [:lcd]`); built here with
  `../../atomvm-tang25k-hls/scripts/build_ex.sh lcd_bars LcdBars`.
  """
  import Bitwise

  @w 480
  @h 320
  @bars [0xFFFF, 0xFFE0, 0x07FF, 0x07E0, 0xF81F, 0xF800, 0x001F, 0x0000]

  def start do
    :lcd.init()
    for {c, i} <- :lists.zip(@bars, :lists.seq(0, 7)),
        do: :lcd.fill(div(i * @w, 8), 0, div(@w, 8), div(@h, 2), c)

    {w, h} = {div(@w, 32), div(@h, 8)}

    for i <- 0..31 do
      v = div(i * 255, 31)
      x = div(i * @w, 32)
      :lcd.fill(x, div(@h, 2), w, h, rgb(v, v, v))
      :lcd.fill(x, div(@h, 2) + h, w, h, rgb(v, 0, 0))
      :lcd.fill(x, div(@h, 2) + 2 * h, w, h, rgb(0, v, 0))
      :lcd.fill(x, div(@h, 2) + 3 * h, w, h, rgb(0, 0, v))
    end

    IO.puts("lcd_bars: drawn")
  end

  defp rgb(r, g, b), do: (r &&& 0xF8) <<< 8 ||| (g &&& 0xFC) <<< 3 ||| b >>> 3
end
