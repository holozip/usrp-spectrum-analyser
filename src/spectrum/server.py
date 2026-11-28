"""Local, receive-only B210 spectrum dashboard."""
import argparse
import json
import sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path

from bookmarks import BookmarkStore, default_bookmark_path
from dashboard_http import DashboardHandler
from receiver import DEFAULT_AVERAGES, MAX_AVERAGES, MAX_RATE, RX_ANTENNAS, RX_PORTS
from sweep import Sweep

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "build/spectrum"
BUNDLED_RETUNE_IMAGE = REPO_ROOT / "resources/firmware/usrp_b210_retune.bin"
UNIFIED_GAIN_ROM = REPO_ROOT / "src/firmware/rtl/control/unified_rx_gain.mem"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use simulated RF data")
    parser.add_argument("--device", default="type=b200,product=B210,num_recv_frames=128", help="UHD device arguments")
    parser.add_argument("--rate", type=float, default=MAX_RATE, help="receive rate in MS/s (default: %(default)s)")
    parser.add_argument("--gain", type=float, default=35, help="receive gain in dB")
    parser.add_argument("--averages", type=int, default=DEFAULT_AVERAGES,
                        help="FFT blocks averaged per capture (default: %(default)s)")
    parser.add_argument("--antenna", choices=(*RX_PORTS, *RX_ANTENNAS), default="RX2",
                        help="starting RX antenna; unprefixed ports use RF A")
    parser.add_argument("--fpga-band-sweep", nargs="?", type=Path, const=BUNDLED_RETUNE_IMAGE,
                        help="enable Fast Lock with the bundled image or an explicit image path")
    parser.add_argument("--hop-plan", choices=["uniform", "paired"], default="paired",
                        help="sweep center spacing (default: %(default)s)")
    parser.add_argument("--gain-table", choices=["stock", "unified"], default="stock",
                        help="receive gain table (unified requires a compatible FPGA sweep image)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bookmarks-file", default=default_bookmark_path(),
                        help="bookmark SQLite file (default: %(default)s); :memory: for an isolated demo")
    args = parser.parse_args()
    if not 4 <= args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 4 and {MAX_RATE} MS/s")
    if not 0 <= args.gain <= 76:
        parser.error("--gain must be between 0 and 76 dB")
    if not 1 <= args.averages <= MAX_AVERAGES:
        parser.error(f"--averages must be between 1 and {MAX_AVERAGES}")
    if args.antenna in RX_PORTS:
        args.antenna = f"A:{args.antenna}"
    if args.fpga_band_sweep:
        if args.demo or args.rate != MAX_RATE or not args.fpga_band_sweep.is_file():
            parser.error("--fpga-band-sweep requires an existing image and live 56 MS/s reception")
        if any(field.strip().startswith("fpga=") for field in args.device.split(",")):
            parser.error("specify the image only through --fpga-band-sweep")
        args.device += f",enable_user_regs,fpga={args.fpga_band_sweep.resolve()}"
    gain_stages = []
    if args.gain_table == "unified":
        if not args.fpga_band_sweep:
            parser.error("--gain-table unified requires --fpga-band-sweep")
        try:
            rows = (line.partition("//")[0].strip() for line in UNIFIED_GAIN_ROM.read_text().splitlines())
            words = [int(row, 16) for row in rows if row]
            if len(words) != 77 or not all(0 <= word < (1 << 24) for word in words):
                raise ValueError("expected 77 three-byte rows")
        except (OSError, ValueError) as error:
            parser.error(f"invalid FPGA gain table: {error}")
        gain_stages = [[(word >> 21) & 3, (word >> 16) & 31, (word >> 13) & 1, (word >> 8) & 31]
                       for word in words]

    class Handler(DashboardHandler):
        dashboard = DASHBOARD
        gain_table = args.gain_table
        gain_stage_data = json.dumps(gain_stages).encode()

    try:
        bookmarks = BookmarkStore(args.bookmarks_file)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.error(f"cannot open bookmark storage: {error}")
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
            sweep = Sweep(args)
            Handler.bookmark_store = bookmarks
            Handler.receiver_state = sweep
            print(f"Spectrum dashboard: http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        bookmarks.close()


if __name__ == "__main__":
    main()
