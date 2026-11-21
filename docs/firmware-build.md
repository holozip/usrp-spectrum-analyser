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
