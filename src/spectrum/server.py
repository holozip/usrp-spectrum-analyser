"""Local, receive-only B210 spectrum dashboard."""
import argparse
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from fpga_sweep import BandSweep

LOW, HIGH = 70e6, 6e9
MAX_RATE = 56
AVERAGES = 8
DISPLAY_BINS = 4096
BINS_PER_OVERVIEW = 384
DETAIL_BINS = DISPLAY_BINS * BINS_PER_OVERVIEW
OVERVIEW_WIDTH = (HIGH - LOW) / DISPLAY_BINS
FFT_SIZE = 16384
PAGE = Path(__file__).with_name("index.html")
PEAK_BUCKET_SECONDS = 0.2
CONTINUOUS_UPDATE_SECONDS = 0.04
DETAIL_WIDTH = (HIGH - LOW) / DETAIL_BINS


def clock_spurs(reference_path):
    """Estimate clock-spur power above the terminated receiver noise floor."""
    with np.load(reference_path, allow_pickle=False) as archive:
        sweeps = archive["baseline"]
    if sweeps.ndim != 2 or len(sweeps) < 3 or sweeps.shape[1] != DETAIL_BINS:
        raise ValueError("expected at least three full-resolution terminated sweeps")
    reference = np.median(sweeps, axis=0)
    bin_width = (HIGH - LOW) / DETAIL_BINS
    corrections = {}
    for clock in (40e6, 56e6):
        for frequency in range(int(clock), int(HIGH), int(clock)):
            index = int((frequency - LOW) / bin_width)
            if index < 32 or index >= DETAIL_BINS - 32:
                continue
            floor_db = np.median(np.r_[reference[index - 32:index - 6],
                                       reference[index + 7:index + 33]])
            window = reference[index - 3:index + 4]
            peak = int(np.argmax(window))
            if not np.isfinite(floor_db) or not np.isfinite(window[peak]) or window[peak] < floor_db + 12:
                continue
            floor = 10 ** (float(floor_db) / 10)
            for offset, db in enumerate(window):
                if np.isfinite(db) and db >= floor_db + 3:
                    corrections[index + offset - 3] = (10 ** (float(db) / 10) - floor, floor)
    return [[index, power, floor] for index, (power, floor) in sorted(corrections.items())]


def captures(rate, bandwidth):
    # Keep clear of both filter edges and discard the immediate LO/DC region.
    edge = 0.35 * min(rate, bandwidth)
    size = FFT_SIZE
    dc_gap = 0.01 * rate
    offsets = np.fft.fftshift(np.fft.fftfreq(size, 1 / rate))
    usable = (np.abs(offsets) <= edge) & (np.abs(offsets) >= dc_gap)
    # Put the first and last display bins inside the retained FFT band.
    margin = edge - rate / size
    # The neighboring hop must cover the entire DC gap, including its far end.
    step = margin - dc_gap
    count = int(np.ceil((HIGH - LOW - 2 * margin) / step)) + 1
    return np.linspace(LOW + margin, HIGH - margin, count), offsets, usable


def scan_range(centers, focus):
    if focus is None:
        return LOW, HIGH, 0, DISPLAY_BINS, centers
    start, stop = focus
    # Align detail bins to the full-sweep grid so the spur reference still applies.
    first = max(0, min(DISPLAY_BINS - 1, int(np.floor((start - LOW) / OVERVIEW_WIDTH))))
    end = min(DISPLAY_BINS, max(first + 1, int(np.ceil((stop - LOW) / OVERVIEW_WIDTH))))
    low, high = LOW + first * OVERVIEW_WIDTH, LOW + end * OVERVIEW_WIDTH
    # Bracket the visible span with adjacent centers so they fill one another's DC gaps.
    left = max(0, min(len(centers) - 2, int(centers.searchsorted(low)) - 1))
    right = min(len(centers), max(left + 2, int(centers.searchsorted(high, side="right")) + 1))
    return low, high, first, end - first, centers[left:right]


def continuous_plan(focus, rate, bandwidth):
    if focus is None or rate != 56e6 or focus[1] - focus[0] > min(rate, bandwidth):
        return None
    # Keep the usual display grid and let pairs of bins form the overview.
    first = max(0, 2 * int(np.floor((focus[0] - LOW) / (2 * DETAIL_WIDTH))))
    end = min(DETAIL_BINS, 2 * int(np.ceil((focus[1] - LOW) / (2 * DETAIL_WIDTH))))
    low, high = LOW + first * DETAIL_WIDTH, LOW + end * DETAIL_WIDTH
    # Center the selection between the receiver's DC gap and its filter edge.
    margin = 3 * rate / FFT_SIZE
    inner = 0.01 * rate + margin
    outer = 0.35 * min(rate, bandwidth) - margin
    if high <= low or high - low > outer - inner:
        # A wide view exposes the full FFT, including DC and filter roll-off.
        center = (focus[0] + focus[1]) / 2 + rate / (2 * FFT_SIZE)
        return center, None, None, None, FFT_SIZE
    gap = (inner + outer - (high - low)) / 2
    center = low - gap if low - gap >= LOW else high + gap
    return center, low, high, first, end - first


class Receiver:
    def __init__(self, args):
        import uhd

        self.uhd = uhd
        self.radio = uhd.usrp.MultiUSRP(args.device)
        self.radio.set_rx_rate(args.rate * 1e6, 0)
        self.rate = self.radio.get_rx_rate(0)
        self.radio.set_rx_bandwidth(min(self.rate, MAX_RATE * 1e6), 0)
        self.bandwidth = self.radio.get_rx_bandwidth(0)
        self.radio.set_rx_antenna(args.antenna, 0)
        self.radio.set_rx_gain(args.gain, 0)
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [0]
        self.stream = self.radio.get_rx_stream(stream_args)
        self.metadata = uhd.types.RXMetadata()
        self.command = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        self.size = FFT_SIZE
        self.window = np.hanning(self.size)
        self.window_sum = self.window.sum()
        self.command.num_samps = self.size * AVERAGES
        self.command.stream_now = True
        self.samples = np.empty((1, self.command.num_samps), dtype=np.complex64)

    def tune(self, center):
        self.radio.set_rx_freq(self.uhd.types.TuneRequest(float(center)), 0)
        return self.radio.get_rx_freq(0)

    def measure(self, center, offsets):
        actual_center = self.tune(center)
        self.capture(actual_center)
        return actual_center + offsets, self.spectrum()

    def capture(self, center):
        self.stream.issue_stream_cmd(self.command)
        self.receive(center)

    def stream_command(self, mode):
        command = self.uhd.types.StreamCMD(mode)
        command.stream_now = True
        self.stream.issue_stream_cmd(command)

    def start_continuous(self):
        self.stream_command(self.uhd.types.StreamMode.start_cont)

    def stop_continuous(self):
        self.stream_command(self.uhd.types.StreamMode.stop_cont)
        # Clear samples queued before the stop before issuing a new tune/burst.
        deadline = time.monotonic() + 0.25
        while time.monotonic() < deadline:
            self.stream.recv(self.samples, self.metadata, 0.02)
            if self.metadata.error_code == self.uhd.types.RXMetadataErrorCode.timeout:
                return
        raise RuntimeError("Timed out draining the continuous RX stream")

    def receive(self, center, continuous=False):
        received = 0
        while received < self.command.num_samps:
            count = self.stream.recv(self.samples[:, received:], self.metadata, 0.25)
            if continuous and self.metadata.error_code == self.uhd.types.RXMetadataErrorCode.overflow:
                return False  # Discard the partial FFT window across the missing samples.
            if not count or self.metadata.error_code != self.uhd.types.RXMetadataErrorCode.none:
                raise RuntimeError(f"RX at {center / 1e6:.2f} MHz: {self.metadata.strerror()}")
            received += count
        return True

    def spectrum(self):
        blocks = self.samples.reshape(AVERAGES, self.size)
        fft = np.fft.fftshift(np.fft.fft((blocks - blocks.mean(axis=1, keepdims=True)) * self.window), axes=1)
        return np.mean(np.abs(fft / self.window_sum) ** 2, axis=0)


class Demo:
    def __init__(self, rate):
        self.rate = rate
        self.bandwidth = min(rate, MAX_RATE * 1e6)
        self.random = np.random.default_rng()

    def measure(self, center, offsets):
        frequency = center + offsets
        power = 10 ** (self.random.normal(-93, 2, frequency.size) / 10)
        for signal, width, level in [(98e6, 1.5e6, -48), (450e6, 0.6e6, -53),
                                     (945e6, 6e6, -60), (1.8e9, 15e6, -63),
                                     (2.44e9, 11e6, -48), (5.19e9, 14e6, -56), (433.92e6, 4500, -48)]:
            power += 10 ** (level / 10) * np.exp(-0.5 * ((frequency - signal) / width) ** 2)
        return frequency, power


class RecentPeaks:
    def __init__(self):
        self.region = None
        self.bucket = None
        self.current = self.previous = None

    def update(self, region, bins, now):
        bucket = int(now / PEAK_BUCKET_SECONDS)
        if region != self.region or bucket != self.bucket:
            # Two short buckets retain a captured burst for roughly 200–400 ms.
            self.previous = self.current if region == self.region and bucket == self.bucket + 1 else None
            self.region, self.bucket = region, bucket
            self.current = bins.copy()
        else:
            np.maximum(self.current, bins, out=self.current)
        return self.current if self.previous is None else np.maximum(self.current, self.previous)


class Sweep:
    def __init__(self, args):
        self.args = args
        self.focus = None
        self.recent_details = deque(maxlen=4)
        self.recent_peaks = RecentPeaks()
        self.snapshot = dict(demo=args.demo, fpga=bool(args.fpga_band_sweep), trace=[], number=0, progress=0, error=None,
                             low=LOW, high=HIGH, seconds=None, detail=b"", peak_detail=b"")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        receiver = None
        while True:
            try:
                if receiver is None:
                    if self.args.demo:
                        receiver = Demo(self.args.rate * 1e6)
                    else:
                        receiver = Receiver(self.args)
                        if self.args.fpga_band_sweep:
                            centers, _, _ = captures(receiver.rate, receiver.bandwidth)
                            receiver = BandSweep(receiver, centers)
                focus = self.focus
                if focus is None and self.recent_peaks.region is not None:
                    self.recent_peaks = RecentPeaks()
                plan = None if self.args.demo else continuous_plan(focus, receiver.rate, receiver.bandwidth)
                if plan is None:
                    self.scan(receiver, focus)
                    self.cache_detail()
                else:
                    self.continuous(receiver, focus, plan)
            except Exception as error:
                self.snapshot = dict(self.snapshot, error=str(error), progress=0)
                receiver = None
                self.recent_peaks = RecentPeaks()
                time.sleep(3)

    def scan(self, receiver, focus=None):
        centers, offsets, usable = captures(receiver.rate, receiver.bandwidth)
        low, high, first, count, centers = scan_range(centers, focus)
        detail_count = count * BINS_PER_OVERVIEW
        self.snapshot = dict(self.snapshot, progress=0)
        started = time.perf_counter()
        bins = np.full(detail_count, -np.inf, dtype=np.float32)
        for index, center in enumerate(centers):
            frequency, power = receiver.measure(center, offsets)
            mask = usable & (frequency >= low) & (frequency <= high) & np.isfinite(power)
            indices = ((frequency[mask] - low) * detail_count / (high - low)).astype(int)
            np.maximum.at(bins, np.minimum(indices, detail_count - 1), power[mask])
            self.snapshot = dict(self.snapshot, progress=(index + 1) / len(centers), error=None)
        measured = np.isfinite(bins)
        bins[measured] = 10 * np.log10(np.maximum(1e-16, bins[measured]))
        peaks = None
        if focus is not None:
            peaks = self.recent_peaks.update((low, high), bins, time.perf_counter())
        self.publish(bins, peaks, low, high, first * BINS_PER_OVERVIEW, BINS_PER_OVERVIEW,
                     seconds=round(time.perf_counter() - started, 2), hops=len(centers))

    def continuous(self, receiver, focus, plan):
        center, low, high, first, size = plan
        if isinstance(receiver, BandSweep):
            actual = receiver.tune_continuous(center)
            radio = receiver.receiver
        else:
            radio = receiver
            actual = radio.tune(center)
        full_window = first is None
        if full_window:
            # Each sample in fftshift order lies at the center of a display bin.
            low = actual - radio.rate / 2 - radio.rate / (2 * FFT_SIZE)
            high = low + radio.rate
            group = 4
        else:
            offsets = np.fft.fftshift(np.fft.fftfreq(FFT_SIZE, 1 / radio.rate))
            frequency = actual + offsets
            mask = (frequency >= low) & (frequency < high)
            indices = np.minimum(((frequency[mask] - low) / DETAIL_WIDTH).astype(int), size - 1)
            if np.unique(indices).size != size:
                raise RuntimeError("The requested continuous window has unmeasured bins")
            group = 2
        bins = np.empty(size, dtype=np.float32)
        next_update = 0
        windows = 0
        gaps = 0
        self.recent_peaks = RecentPeaks()
        radio.start_continuous()
        try:
            while self.focus == focus:
                if not radio.receive(actual, continuous=True):
                    gaps += 1
                    continue
                power = radio.spectrum()
                if full_window:
                    bins[:] = 10 * np.log10(np.maximum(1e-16, power))
                else:
                    bins.fill(-np.inf)
                    np.maximum.at(bins, indices, 10 * np.log10(np.maximum(1e-16, power[mask])))
                now = time.perf_counter()
                peaks = self.recent_peaks.update((low, high), bins, now)
                windows += 1
                if now >= next_update:
                    self.publish(bins, peaks, low, high, first, group, seconds=CONTINUOUS_UPDATE_SECONDS,
                                 hops=1, continuous=True, windows=windows, gaps=gaps)
                    self.cache_detail()
                    next_update = now + CONTINUOUS_UPDATE_SECONDS
        finally:
            radio.stop_continuous()

    def publish(self, bins, peaks, low, high, first, group, seconds, hops, continuous=False, windows=0, gaps=0):
        overview = bins.reshape(-1, group).max(axis=1)
        trace = [round(float(value), 1) if np.isfinite(value) else None for value in overview]
        self.snapshot = dict(self.snapshot, trace=trace, number=self.snapshot["number"] + 1, progress=1, error=None,
                             low=low, high=high, detail_offset=first, detail_count=len(bins),
                             detail=bins.astype("<f4", copy=False).tobytes(),
                             peak_detail=peaks.astype("<f4", copy=False).tobytes() if peaks is not None else b"",
                             seconds=seconds, hops=hops, continuous=continuous, windows=windows, gaps=gaps)

    def cache_detail(self):
        self.recent_details.append((self.snapshot["number"], self.snapshot["detail"], self.snapshot["peak_detail"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use simulated RF data")
    parser.add_argument("--device", default="type=b200,product=B210,num_recv_frames=128", help="UHD device arguments")
    parser.add_argument("--rate", type=float, default=MAX_RATE, help="receive rate in MS/s (default: %(default)s)")
    parser.add_argument("--gain", type=float, default=35, help="receive gain in dB")
    parser.add_argument("--antenna", choices=["RX2", "TX/RX"], default="RX2")
    parser.add_argument("--spur-reference", type=Path,
                        help="terminated 56 MS/s reference archive for optional display suppression")
    parser.add_argument("--fpga-band-sweep", type=Path,
                        help="load the Fast Lock FPGA image and calibrate only at band transitions")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 4 <= args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 4 and {MAX_RATE} MS/s")
    if not 0 <= args.gain <= 76:
        parser.error("--gain must be between 0 and 76 dB")
    if args.fpga_band_sweep:
        if args.demo or args.rate != MAX_RATE or not args.fpga_band_sweep.is_file():
            parser.error("--fpga-band-sweep requires an existing image and live 56 MS/s reception")
        if any(field.strip().startswith("fpga=") for field in args.device.split(",")):
            parser.error("specify the image only through --fpga-band-sweep")
        args.device += f",enable_user_regs,fpga={args.fpga_band_sweep.resolve()}"
    if args.spur_reference and args.rate != MAX_RATE:
        parser.error("--spur-reference requires a 56 MS/s sweep")
    try:
        corrections = clock_spurs(args.spur_reference) if args.spur_reference else []
    except (OSError, ValueError, KeyError, EOFError) as error:
        parser.error(f"invalid spur reference: {error}")
    spur_data = json.dumps(corrections, allow_nan=False).encode()
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                kind, body = "text/html; charset=utf-8", page
            elif self.path == "/api/sweep":
                snapshot = sweep.snapshot
                frame = {key: value for key, value in snapshot.items() if key not in ("detail", "peak_detail")}
                frame["focus"] = sweep.focus
                frame["peaks"] = bool(snapshot["peak_detail"])
                kind, body = "application/json", json.dumps(frame, allow_nan=False).encode()
            elif self.path == "/api/spurs":
                kind, body = "application/json", spur_data
            elif self.path.startswith("/api/detail/"):
                path, _, query = self.path.partition("?")
                frame = sweep.snapshot
                if path == f"/api/detail/{frame['number']}" and frame["detail"]:
                    body, peaks = frame["detail"], frame["peak_detail"]
                else:
                    cached = next(((data, peaks) for number, data, peaks in sweep.recent_details.copy()
                                   if path == f"/api/detail/{number}"), None)
                    if cached is None:
                        self.send_error(404)
                        return
                    body, peaks = cached
                if query == "peaks=1" and peaks:
                    body += peaks
                kind = "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.reply(kind, body)

        def do_POST(self):
            if self.path != "/api/focus":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                    raise ValueError
                focus = json.loads(self.rfile.read(length))
                if focus is not None:
                    if (not isinstance(focus, list) or len(focus) != 2
                            or any(type(value) not in (int, float) for value in focus)
                            or not LOW <= focus[0] < focus[1] <= HIGH):
                        raise ValueError
                    focus = tuple(focus)
            except (ValueError, UnicodeError):
                self.send_error(400, "Expected a frequency range in Hz or null")
                return
            sweep.focus = focus
            self.reply("application/json", json.dumps(focus).encode())

        def reply(self, kind, body):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        sweep = Sweep(args)
        print(f"Spectrum dashboard: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
