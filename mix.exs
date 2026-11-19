defmodule AtomVMTang25KRPiDisplay35.MixProject do
  use Mix.Project

  def project do
    [
      app: :atomvm_tang25k_rpi_display_35inch,
      version: "0.1.0",
      elixir: "~> 1.18",
      elixirc_paths: ["lib"],
      deps: deps(),
      description: "The 3.5inch RPi Display (ILI9486 with an XPT2046 touch panel) for Elixir HLS on the Tang Primer 25K: SPI, its NIFs",
      package: [
        licenses: ["Apache-2.0"],
        links: %{"GitHub" => "https://github.com/elixir-hls/atomvm-tang25k-rpi-display-3.5inch"},
        files: ~w(csr_headers.json gateware lib src mix.exs README.md LICENSE),
        exclude_patterns: [~r/__pycache__/, ~r/\.pyc$/]
      ]
    ]
  end

  def application, do: []

  defp deps do
    [
      {:elixir_hls, "~> 0.1"}
    ]
  end
end
