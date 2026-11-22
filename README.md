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

- Drag across the plot to narrow the receiver sweep. **Reset zoom** restores the full range.
- With live reception at 56 MS/s, a span of 56 MHz or less uses continuous capture. **56 MHz window** centers that span on the current view. Narrow views avoid DC and filter roll-off where possible. The full-width view includes both regions, where power readings can be less reliable.
- Hover to read frequency, power in dBFS, and the displayed bin width. **Freeze trace** pauses the display while the receiver keeps collecting samples. Use **Peak hold** to show or hide the amber trace, which retains recent peaks for roughly 200–400 ms.
- The default capture uses 56 MS/s, 56 MHz analog bandwidth, and eight 16,384-point FFT blocks per average. FFT spacing is 3.42 kHz. The stitched sweep's finest display bins are 3.77 kHz.

## Receiver options

- The default paired plan uses 208 hops at 56 MS/s. Use `--hop-plan uniform` to select the 311-hop plan. [Measured performance](docs/firmware-build.md#measured-performance)
- `--gain DB` sets the starting RX gain (default 35 dB). Use the **RX gain** slider to adjust it while receiving. With the updated unified-table patch, the readout shows the selected row’s analog stage indices and nominal gains at 2.3 GHz as you move the slider. Clock-spur suppression pauses at gains that differ from the starting gain used for the reference. With the ordinary UHD sweep, try `--rate 40` or `--rate 20` if USB reception is unstable. The FPGA sweep and continuous mode use 56 MS/s.
- `--gain-table stock` uses UHD’s band-specific tables and is the default. `--gain-table unified` uses UHD’s existing 1.3–4 GHz table across the sweep. It requires the [patched UHD library](#unified-gain-table).
- `--device ARGS` overrides the UHD device arguments. The default receive queue uses `num_recv_frames=128`. To use a deeper queue, pass `--device type=b200,product=B210,num_recv_frames=512`.
- `--spur-reference PATH` enables a display filter based on a terminated 56 MS/s reference. Use an NPZ file with a `baseline` array containing at least three raw full sweeps at the same gain and antenna port. Each row contains the 1,572,864 little-endian float32 dBFS bins from `/api/detail/<sweep-number>`. Use `--spur-strength 0.85` to subtract less reference power and preserve more of a weak signal in a corrected bin. The default is full subtraction. Turn off **Suppress clock spurs** to inspect the raw trace.


## Unified gain table

Apply [the UHD patch](resources/uhd/unified-rx-gain-table.patch) from the root of a UHD 4.11.0.0 source checkout with `git apply /path/to/unified-rx-gain-table.patch`. Build UHD with the B200 driver enabled and use a matching UHD Python module. Launch the dashboard with the patched library on the loader path.

```sh
LD_LIBRARY_PATH=/path/to/uhd/build/lib python3 src/spectrum/server.py \
  --antenna TX/RX --fpga-band-sweep --gain-table unified
```

The dashboard checks that the Python binding loaded the patched library before starting. Omit `--gain-table unified` to use stock tables. The option leaves RF input switching, calibration, bandwidth, and FFT settings in place. If you use clock-spur suppression, capture a separate reference for each gain-table mode.
