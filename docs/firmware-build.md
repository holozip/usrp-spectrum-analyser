# B210 FPGA firmware build

`src/firmware` contains the Vivado project and three source directories. `rtl/` holds the top-level design and grouped logic, `constraints/` holds the board and clock constraints, and `ip/` holds the clock generator configuration and half-band filter files.

## Requirements

- AMD Vivado 2026.x with the Kintex-7 `xc7k325tffg676-2` device files installed.
- Bash, Python 3, GNU coreutils, and `flock`.
- Enough memory and disk space for a full Vivado implementation run.

Point `VIVADO` at the Vivado launcher if it is not on `PATH`. `JOBS` sets the implementation parallelism.

## Build

```sh
JOBS=8 make firmware-build
```

The build stages a Vivado project in `build/firmware/project/`. It references RTL, constraints, and netlists in `src/firmware` and stages the active IP definitions so Vivado can upgrade them in place. Project caches and generated IP stay beside the staged project. Open `build/firmware/project/B210_Project_Firmwire.xpr` to inspect the project in Vivado after a build.

Each build places and routes from scratch and reuses the same `synth_1` and `impl_1` run directories. The build publishes one raw UHD image after bitstream generation and timing closure.

```text
build/firmware/usrp_b210_fpga.bin
```

Vivado output is shown in the terminal and saved to `build/firmware/build.log`. Individual run logs and reports are under `build/firmware/runs/`. If a build fails, the last successful UHD image remains available. Generated Vivado files, reports, and the published image are ignored by Git.

## Diagnose a failed route

After an implementation run fails during routing, generate detailed reports before starting another build:

```sh
make firmware-diagnose
```

This refreshes the congestion, complexity, route status, fanout, timing, and utilization reports in `build/firmware/route-diagnostics/`. It reads the failed checkpoint from the current `impl_1` run and saves Vivado output to `build/firmware/diagnose.log`.

## Verify and install

Probe a newly built image on the B210 before copying it to the UHD image directory:

```sh
uhd_usrp_probe --args "type=b200,fpga=$PWD/build/firmware/usrp_b210_fpga.bin"
```

UHD loads the specified image into the FPGA for the probe. Check that radio initialization and both register loopback tests pass.

```sh
make firmware-install
```

`firmware-install` copies the built image to `/usr/local/share/uhd/images/usrp_b210_fpga.bin`. Run it with the system permissions needed to write there. Building never installs or changes the system UHD image.

## Board reference files

- [Hardware schematic (PDF)](hardware-schematic/USRP_B210.pdf)
- [Mechanical drawing (DWG)](mechanical-drawings/PCB.DWG) and [3D model (STEP)](mechanical-drawings/PCB.step)

## Install the prebuilt Fast Lock image

For the Kintex-7 clone documented in this repository, use `sudo make firmware-install-retune` to copy the checked-in, checksum-verified image to `/usr/local/share/uhd/images/usrp_b210_retune.bin`. Choose that file explicitly with the dashboard's `--fpga-band-sweep` option, or supply the flag by itself to load the copy in `resources/firmware/` directly. The standard UHD filename `usrp_b210_fpga.bin` remains available for normal operation.

The bundled Vivado 2026.1 image is the timing-clean calibration-polling candidate used for the live calibration and dashboard tests below.

## Two-hop RX Fast Lock prototype

The prototype adds a tagged command and status mailbox to radio 0's UHD user settings. The FPGA owns the existing SPI core while executing a command. It recalls a preloaded AD9361 RX profile, waits 50 µs, and polls the RX PLL status with a finite retry limit. The host still requests each hop, waits for the FPGA response, reads the IQ burst, and runs the dashboard's eight 16,384-point FFTs. Profile calibration and loading are outside the timed loop.

Build the candidate into a separate directory to preserve the normal FPGA image:

```sh
python3 scripts/stage-vivado-project.py src/firmware/B210_Project_Firmwire.xpr build/retune-prototype-v2/project
(
  cd build/retune-prototype-v2
  vivado -mode batch -nolog -nojournal -source ../../scripts/vivado-build.tcl \
    -tclargs "$PWD/project/B210_Project_Firmwire.xpr" "$PWD/usrp_b210_retune.bin" 12
)
```

The hardware comparison used the known 920 MHz marker from the terminated TX/RX input on the development board.

Both timed paths use 56 MS/s, 56 MHz analog bandwidth, 35 dB receive gain, and the same two adjacent sweep centers (906.708498 and 925.711101 MHz). They report median and 95th-percentile tune, capture, FFT, and total latency. The FPGA test also reports the FPGA-local recall time and checks marker frequency and level at both centers. Reopen the dashboard using its usual device arguments after the test to reload the normal image and reset the codec state.

### Live prototype results

The routed candidate met the specified timing constraints with +2.140 ns setup slack and +0.031 ns hold slack. The benchmark ran 80 measured alternating hops per method on the same loaded candidate at 56 MS/s and 56 MHz bandwidth with the TX/RX input terminated.

| Stage | UHD median / p95 (ms) | FPGA median / p95 (ms) |
| --- | ---: | ---: |
| Retune including host control | 2.568 / 2.641 | 0.139 / 0.164 |
| Receive burst | 2.509 / 2.525 | 2.488 / 2.502 |
| FFT and averaging | 1.420 / 2.718 | 0.879 / 1.218 |
| Total per hop | 6.493 / 7.808 | 3.501 / 3.854 |

The median FPGA-local recall took 55.17 µs, including its 50 µs guard. The 920 MHz marker landed in the same FFT bins with level differences of 0.18 and 0.13 dB at the two centers. This result covers two preloaded profiles within one receiver band. Profile refills, band changes and full-range performance remain unmeasured. The normal dashboard image was reloaded after the benchmark.

## Full-band RX Fast Lock benchmark

The full-band prototype keeps the dashboard's 311-center, 70 MHz–6 GHz plan. A preparation pass uses UHD to calibrate the receiver and read a Fast Lock profile for each FPGA-controlled center. The profiles are kept for the current device session. At each group boundary the host returns the chip to normal tuning and uses UHD for the anchor capture. This preserves the receiver gain-table and RF-input transitions at 1.3, 2.2, and 4 GHz and retains UHD's periodic RF DC calibration. The FPGA refills and verifies the profile slots for the rest of the group and recalls them for the subsequent captures.

Build the current source into a separate directory to preserve the normal FPGA image:

```sh
python3 scripts/stage-vivado-project.py src/firmware/B210_Project_Firmwire.xpr build/retune-full/project
(
  cd build/retune-full
  vivado -mode batch -nolog -nojournal -source ../../scripts/vivado-build.tcl \
    -tclargs "$PWD/project/B210_Project_Firmwire.xpr" "$PWD/usrp_b210_retune.bin" 12
)
```

The benchmark times full captures, all profile refills and readback checks, the UHD anchor transitions, FFT averaging, and spectrum stitching. It reports the preparation cost separately and checks all 1,572,864 detail bins for coverage. Reopening the normal dashboard reloads its usual FPGA image.

### Live full-band results

Three passes per method on the same timing-clean candidate used the terminated TX/RX input, 56 MS/s, a 56 MHz analog filter, and eight 16,384-point FFT blocks at each center.

| Method | Full-pass times (s) | Median (s) |
| --- | --- | ---: |
| UHD | 4.923, 4.871, 4.857 | 4.871 |
| FPGA Fast Lock with UHD anchors | 4.317, 4.313, 4.320 | 4.317 |

The hybrid sweep was 11.4% faster after 3.797 s of one-time profile preparation. Each pass used 55 UHD anchors and 256 FPGA refills and recalls. The median FPGA-assisted pass spent about 2.832 s in anchors and their transitions, 0.166 s refilling and verifying profiles, and 0.033 s issuing and acknowledging recalls. Each pass populated every detail bin. Six terminated-input markers stayed in the same bins and changed by at most 0.33 dB. The largest absolute band-median difference was 0.07 dB. This is a terminated-input fidelity comparison with the host still initiating hops and UHD still applying band changes and periodic calibrations.

## FPGA polling for RF DC calibration

The optional FPGA poller observes UHD's write that starts RF DC calibration on the AD9361. UHD still chooses when to calibrate, programs the gain and RF-input settings, and enables tracking afterward. While calibration runs, the FPGA holds the host SPI path and reads the AD9361 busy bit. Once it clears, UHD's pending read can complete without its 50 ms polling sleep. A 50 ms FPGA timeout releases the host path so UHD can continue polling. The feature starts disabled on FPGA reset.

The timing counter starts when the calibration-start SPI transaction finishes and stops when the FPGA receives the first read with the busy bit clear. Measurements include the last SPI read. Calibration timing uses the 100 MHz FPGA bus clock. Benchmark runners and transient test output are kept in the ignored `build/` directory.

### Live calibration-poller results

At 56 MS/s and 56 MHz analog bandwidth with eight 16,384-point FFT blocks per hop, hardware telemetry measured 156 RF DC calibrations across three full sweeps. Calibration took 41.463–46.636 ms with a 46.636 ms median and zero FPGA poll timeouts.

| Method | Full-pass times (s) | Median (s) |
| --- | --- | ---: |
| UHD | 4.886, 4.834, 4.829 | 4.834 |
| Fast Lock with UHD calibration polling | 4.267, 4.270, 4.273 | 4.270 |
| Fast Lock with FPGA calibration polling | 4.003, 4.002, 4.003 | 4.003 |

Every pass populated all detail bins. Six terminated-input markers stayed in the same bins and changed by at most 0.77 dB relative to UHD. The new image met Vivado timing. The normal image and dashboard were restored after the benchmark.

### Calibration cadence comparison

Three interleaved passes per cadence used the same FPGA polling image, rolling two-slot refills, terminated TX/RX input, 56 MS/s, 56 MHz analog bandwidth, and eight 16,384-point FFT blocks at each center. Both modes populated all detail bins.

| Cadence | RF DC calibrations per pass | Full-pass times (s) | Median (s) |
| --- | ---: | --- | ---: |
| 100 MHz window and band transitions | 52 | 4.171, 4.011, 4.015 | 4.015 |
| First hop and band transitions only | 4 | 1.630, 1.630, 1.636 | 1.630 |

Band-only calibration occurred at 89.597, 1305.763, 2217.888, and 4004.133 MHz. The band-only median was 59% shorter. Six terminated-input markers landed in the same bins and differed by at most 0.38 dB. The largest absolute band-median difference was 0.25 dB, and the 95th-percentile absolute bin difference was 2.26–2.32 dB across the four bands. This is a terminated-input comparison. Fidelity with a weak external signal or across temperature changes remains unmeasured.

With the antenna reconnected, five interleaved raw passes per cadence took a median of 4.007 s at 100 MHz and 1.626 s band-only. Spur suppression was bypassed. Among 366 weak features seen at least four times in both modes, the median band-only level change was +0.09 dB, and 95% changed by no more than 1.59 dB. A weak feature was defined as 6–15 dB above the local floor in the reference spectrum. Features near known clock harmonics were excluded. Many other features appeared inconsistently in both directions, so this comparison qualifies repeatable antenna signals and does not establish sensitivity to a controlled weak input.
