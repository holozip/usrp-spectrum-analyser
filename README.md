# USRP B210 spectrum analyser

A receive-only web dashboard for the **Kintex-7 B210 clone** identified by UHD as `Custom_SDR_B210` (`type=b200,product=B210`). It sweeps 70 MHz–6 GHz and switches to continuous capture when the zoom fits within 56 MHz.

Before loading the bundled FPGA image, verify that your board matches the `xc7k325tffg676-2` FPGA and pinout in the [clone schematic](docs/hardware-schematic/USRP_B210.pdf).

## Run the dashboard

Install UHD with its Python bindings, plus NumPy, on the computer connected to the radio. From the checkout, run:

```sh
python3 src/spectrum/server.py --antenna TX/RX --fpga-band-sweep
```

Open the [local dashboard](http://127.0.0.1:8000). The command selects channel 0's TX/RX input and loads the bundled FPGA image into RAM. Use `--antenna RX2` for the other input. Omit `--fpga-band-sweep` for ordinary UHD operation, or use `--demo` to try the UI with NumPy alone.

Vivado is needed only to rebuild the image. To install a system-wide copy, run `sudo make firmware-install-retune` and pass its path explicitly with `--fpga-band-sweep /usr/local/share/uhd/images/usrp_b210_retune.bin`. The target checks the bundled SHA-256 and installs alongside UHD's normal image. Stop the dashboard before opening the radio in another application. [Firmware build and validation](docs/firmware-build.md)

## Inspect a signal

- Scroll over the spectrum or waterfall to zoom around the pointer. Drag to pan, or hold **Shift** while dragging to select a range. **Full spectrum** returns to 70 MHz–6 GHz.
- With the default live settings, a span of 56 MHz or less uses continuous capture. **56 MHz window** centers that span on the current view. Narrow views avoid DC and filter roll-off where possible. The full-width view includes both regions, where power readings can be less reliable.
- Hover to read frequency, power in dBFS, and the displayed bin width. The plot toolbox contains **Peak hold** and **Freeze trace**. Freezing pauses the display while the receiver keeps collecting samples. Use **Peak hold** to show or hide the amber trace, which retains recent peaks for roughly 200–400 ms. The waterfall shows frequency horizontally and recent sweeps vertically, with the newest row at the top.
- The sweep uses 56 MS/s, 56 MHz analog bandwidth, and eight 16,384-point FFT blocks per average by default. FFT spacing is 3.42 kHz. The stitched sweep's finest display bins are 3.77 kHz. Continuous views at 2 MHz or narrower decimate to 14 MS/s and use a 32,768-point FFT (427 Hz spacing). At 500 kHz or narrower, a 65,536-point FFT gives 214 Hz spacing. The master clock and analog bandwidth stay at 56 MHz, with eight FFT blocks per average.

## Receiver options

- The default paired plan uses 208 hops at 56 MS/s. Use `--hop-plan uniform` to select the 311-hop plan. [Measured performance](docs/firmware-build.md#measured-performance)
- `--gain DB` sets the starting RX gain (default 35 dB). Use the **RX gain** slider to adjust it while receiving. In unified mode, the readout shows the selected row’s analog stage indices and nominal gains at 2.3 GHz as you move the slider. With the ordinary UHD sweep, try `--rate 40` or `--rate 20` if USB reception is unstable. FPGA sweeping and continuous capture require the default `--rate 56` setting.
- `--gain-table stock` uses UHD’s band-specific tables and is the default. `--gain-table unified` uses UHD’s existing 1.3–4 GHz table across the sweep. It requires the [FPGA sweep image](#unified-gain-table) and works with stock UHD.
- `--device ARGS` overrides the UHD device arguments. The default receive queue uses `num_recv_frames=128`. To use a deeper queue, pass `--device type=b200,product=B210,num_recv_frames=512`.

## Unified gain table

Use stock UHD with the bundled sweep image.

```sh
python3 src/spectrum/server.py --antenna TX/RX --fpga-band-sweep --gain-table unified
```

The FPGA supplies the 1.3–4 GHz AD9361 table whenever UHD loads a gain table. UHD still selects the RF input and gain index and runs calibration. Omit `--gain-table unified` to use stock tables.
