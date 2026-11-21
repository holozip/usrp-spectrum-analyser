# B210 clone FPGA firmware

This project builds an FPGA image for the Kintex-7 B210 clone that UHD identifies as `Custom_SDR_B210` (`product=B210`, `type=b200`). The Vivado target is `xc7k325tffg676-2`. Check your board's FPGA and pinout against the [clone schematic](docs/hardware-schematic/USRP_B210.pdf) before loading the image.

## Build

On Linux, install GNU Make, Bash, Python 3, GNU coreutils, and `flock`. Install AMD Vivado 2026.x with the target Kintex-7 device files and a valid Vivado license. The clean build was verified with Vivado 2026.1, build 6511674. [AMD Vivado licensing requirements](https://docs.amd.com/r/2026.1-English/ug973-vivado-release-notes-install-license/Supported-Devices-and-Features)

Run the build from the repository root.

```sh
JOBS=8 make firmware-build
```

Set `VIVADO=/path/to/vivado` if the launcher is outside your `PATH`. The build writes `build/firmware/usrp_b210_fpga.bin` and saves its log to `build/firmware/build.log`. [Build and diagnostics guide](docs/firmware-build.md)

## Verify and install

Use UHD to probe the built image directly. Check that radio initialization and both register loopback tests pass.

```sh
uhd_usrp_probe --args "type=b200,fpga=$PWD/build/firmware/usrp_b210_fpga.bin"
```

The install target copies the image to `/usr/local/share/uhd/images/usrp_b210_fpga.bin`. With a UHD installation that uses that directory, install the image and probe again using UHD's default selection.

```sh
sudo make firmware-install
uhd_usrp_probe --args type=b200
```

## Verified RX rates

Testing used UHD `4.11.0.HEAD-0-g0d7ed3b1` on Linux with `benchmark_rate`, reception over USB 3, 16-bit complex I/Q on the wire (`sc16`), float complex samples on the host (`fc32`), and `num_recv_frames=128` in the UHD device arguments.

| RX channels | Rate per channel | Combined rate | Drop-free runs |
| --- | ---: | ---: | --- |
| 1 | 61.44 MS/s | 61.44 MS/s | 65 s and 120 s |
| 2 | 30.72 MS/s | 61.44 MS/s | 65 s and 120 s |

Both configurations recorded zero dropped samples, overruns, RX sequence errors, and RX timeouts in those runs. With the default receive queue, both peak rates produced intermittent overruns, and one channel at 56 MS/s passed a 65-second run. UHD reported a 56 MHz analog receive filter bandwidth during the 61.44 MS/s test. [UHD B2x0 manual](https://files.ettus.com/manual/page_usrp_b200.html)
