# FPGA firmware build and validation

The Vivado project targets the `xc7k325tffg676-2` B210 clone. Board references include the [schematic](hardware-schematic/USRP_B210.pdf), [PCB drawing](mechanical-drawings/PCB.DWG), and [3D model](mechanical-drawings/PCB.step). For the prebuilt image and dashboard commands, see the [README](../README.md#run-the-dashboard).

## Build and probe

Install GNU Make, Bash, Python 3, GNU coreutils, `flock`, and Vivado 2026.x with the Kintex-7 device files and a valid license. Builds were verified with Vivado 2026.1. Set `VIVADO=/path/to/vivado` if the launcher is outside your `PATH`.

```sh
JOBS=8 make firmware-build
uhd_usrp_probe --args "type=b200,fpga=$PWD/build/firmware/usrp_b210_fpga.bin"
```

The build stages the project under `build/firmware/project/` and writes a raw UHD image after bitstream generation and timing closure. A failed build leaves the last successful image available. Check that UHD initializes the radio and passes both register loopback tests before using the image.

- Build log and implementation reports live under `build/firmware/build.log` and `build/firmware/runs/`.
- After a routing failure, run `make firmware-diagnose` before rebuilding. It uses the current failed checkpoint and writes reports to `build/firmware/route-diagnostics/`.
- Test a rebuilt sweep image with `--fpga-band-sweep build/firmware/usrp_b210_fpga.bin`. `sudo make firmware-install` replaces UHD's default clone image at `/usr/local/share/uhd/images/usrp_b210_fpga.bin`. Use explicit image selection to keep the day-to-day image in place.
- The [bundled image](../resources/firmware/usrp_b210_retune.bin) has a [SHA-256 checksum](../resources/firmware/SHA256SUMS). Generated builds and local benchmark runners live under the Git-ignored `build/` directory.

## How the FPGA sweep works

The host prepares Fast Lock profiles with UHD when it opens the radio. On a full sweep, UHD tunes and calibrates at the first hop and at the 1.3, 2.2, and 4 GHz receiver transitions. For intervening hops, the FPGA programs and verifies the inactive Fast Lock slot, recalls it over SPI, and checks PLL lock. The host requests each hop, receives IQ, computes the FFTs, and stitches the trace.

Unified gain-table mode substitutes the FPGA ROM bytes in UHD’s AD9361 gain-table load sequence. UHD still sets the row address, clocks the writes, and calibrates. The host crosses a table boundary before preparing profiles so the unified table is present on the first capture. To skip repeat gain-table writes, build the current source and pass `--fpga-band-sweep build/firmware/usrp_b210_fpga.bin`. With that image, the FPGA skips subsequent table writes after the first complete load. UHD still sends its usual control commands. The override and cached-table state clear on FPGA or codec reset. The ROM reproduces the UHD 4.11 `gain_table_1300mhz_to_4000mhz` table (Ettus Research, GPL-3.0-or-later) and supplies the dashboard’s gain-stage readout.

For RF DC calibration, the optional FPGA poller watches the AD9361 busy bit after UHD starts calibration. It releases the host SPI path when calibration completes or after a 50 ms timeout. FPGA reset leaves polling disabled. The bundled candidate also passed ordinary single-channel UHD RX at 56 MS/s. TX, dual-channel RX, and external clocking on this candidate remain unverified.

## Measured performance

Live tests used 56 MS/s, 56 MHz analog bandwidth, and eight 16,384-point FFT blocks per hop. Full passes populated all 1,572,864 detail bins.

| Comparison | Reference | FPGA-assisted |
| --- | ---: | ---: |
| Two-hop median retune, including host control | 2.568 ms UHD | 0.139 ms Fast Lock |
| Full pass, UHD polling vs FPGA calibration polling | 4.270 s | 4.003 s |
| Full pass, 100 MHz cadence vs band-only calibration | 4.015 s, 52 calibrations | 1.630 s, 4 calibrations |
| Band-only pass, uniform vs paired centers | 1.615 s, 311 hops | 1.160 s, 208 hops |

The calibration-polling run measured a 46.64 ms median RF DC busy time across 156 events, with no timeouts. Ordinary UHD took 4.834 s per pass in that comparison. Each full-pass timing is the median of three passes and excludes about 3.8 s of one-time profile preparation.

Band-only calibration shifted six terminated-input peaks by at most 0.38 dB, with each peak staying in the same FFT bin. In five interleaved antenna passes per mode, 366 repeatable weak peaks had a median level change of +0.09 dB, and 95% changed by no more than 1.59 dB. Sensitivity to a controlled weak tone and temperature drift remain unmeasured.

The paired-plan figures are medians of six live antenna passes per plan. Its centers alternate 19 and 38 MHz spacing. Both plans calibrated four times per sweep. With identical input captures, all 29 repeatable weak peaks within 600 kHz of wider joins remained detectable. Separate live passes retained 12 of 14 such peaks in at least five of six passes. A controlled weak tone remains untested.

With stock UHD and FPGA unified mode, all 77 gain-table rows read back correctly at 900 MHz and 4.5 GHz. The 208-hop sweep took a median 1.16 s over six passes, with four band calibrations per pass. Stock gain-table mode remained available on the same image. Suppressing repeat loads removed 1,926 AD9361 SPI writes per sweep. Over eight steady passes per image, medians were 1.159 s before and 1.158 s after. Stock UHD still sends the control commands.

A full 56 MHz continuous window sustained about 426 FFT averages per second over 12 seconds with no reported receive gaps, using a 512-frame USB receive queue.

Earlier tests of the normal firmware and UHD 4.11 recorded no drops over 65- and 120-second USB 3 runs at 61.44 MS/s on one channel or 30.72 MS/s each on two channels. Those runs used `sc16` on the wire, `fc32` on the host, and `num_recv_frames=128`. The radio reported 56 MHz analog bandwidth at 61.44 MS/s.
