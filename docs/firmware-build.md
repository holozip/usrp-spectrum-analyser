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

For RF DC calibration, the optional FPGA poller watches the AD9361 busy bit after UHD starts calibration. It releases the host SPI path when calibration completes or after a 50 ms timeout. FPGA reset leaves polling disabled. The bundled candidate also passed ordinary single-channel UHD RX at 56 MS/s. TX, dual-channel RX, and external clocking on this candidate remain unverified.

## Measured performance

Live tests used 56 MS/s, 56 MHz analog bandwidth, and eight 16,384-point FFT blocks per hop. Full passes covered 311 hops and populated all 1,572,864 detail bins.

| Comparison | Reference | FPGA-assisted |
| --- | ---: | ---: |
| Two-hop median retune, including host control | 2.568 ms UHD | 0.139 ms Fast Lock |
| Full pass, UHD polling vs FPGA calibration polling | 4.270 s | 4.003 s |
| Full pass, 100 MHz cadence vs band-only calibration | 4.015 s, 52 calibrations | 1.630 s, 4 calibrations |

The calibration-polling run measured a 46.64 ms median RF DC busy time across 156 events, with no timeouts. Ordinary UHD took 4.834 s per pass in that comparison. Each full-pass timing is the median of three passes and excludes about 3.8 s of one-time profile preparation.

Band-only calibration shifted six terminated-input peaks by at most 0.38 dB, with each peak staying in the same FFT bin. In five interleaved antenna passes per mode, 366 repeatable weak peaks had a median level change of +0.09 dB, and 95% changed by no more than 1.59 dB. Sensitivity to a controlled weak tone and temperature drift remain unmeasured.

A full 56 MHz continuous window sustained about 426 FFT averages per second over 12 seconds with no reported receive gaps, using a 512-frame USB receive queue.

Earlier tests of the normal firmware and UHD 4.11 recorded no drops over 65- and 120-second USB 3 runs at 61.44 MS/s on one channel or 30.72 MS/s each on two channels. Those runs used `sc16` on the wire, `fc32` on the host, and `num_recv_frames=128`. The radio reported 56 MHz analog bandwidth at 61.44 MS/s.
