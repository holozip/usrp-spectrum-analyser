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
NARROW_RATE = MAX_RATE * 1e6 / 4
AVERAGES = 8
DISPLAY_BINS = 4096
BINS_PER_OVERVIEW = 384
DETAIL_BINS = DISPLAY_BINS * BINS_PER_OVERVIEW
OVERVIEW_WIDTH = (HIGH - LOW) / DISPLAY_BINS
FFT_SIZE = 16384
PAGE = Path(__file__).with_name("index.html")
REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_RETUNE_IMAGE = REPO_ROOT / "resources/firmware/usrp_b210_retune.bin"
UNIFIED_GAIN_ROM = REPO_ROOT / "src/firmware/rtl/control/unified_rx_gain.mem"
PEAK_BUCKET_SECONDS = 0.2
CONTINUOUS_UPDATE_SECONDS = 0.04
DETAIL_WIDTH = (HIGH - LOW) / DETAIL_BINS


def captures(rate, bandwidth, paired=False):
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
    span = HIGH - LOW - 2 * margin
    if paired:
        # The close neighbor covers each DC gap. Across pairs, keep the
        # shared passband wider than the FFT spacing and allow for filter roll-off.
        wide = 2 * (margin - dc_gap)
        pairs = int(np.ceil((span + wide) / (step + wide)))
        increments = np.resize([step, wide], 2 * pairs - 1)
        positions = np.r_[0, np.cumsum(increments)]
        centers = LOW + margin + positions * (span / positions[-1])
    else:
        count = int(np.ceil(span / step)) + 1
        centers = np.linspace(LOW + margin, HIGH - margin, count)
    return centers, offsets, usable


def offset_hop_plan(centers):
    # Move both hops in each pair together and pin the pairs at the band edges.
    pair = np.arange(len(centers) // 2)
    taper = np.minimum(1, np.minimum(pair, pair[-1] - pair) / 6)
    return centers + np.repeat(5e6 * taper, 2)


def minimum_trace(first, second):
    # A missing observation must not overwrite a measured bin.
    both = np.isfinite(first) & np.isfinite(second)
    return np.where(both, np.minimum(first, second), np.fmax(first, second))


def scan_range(centers, focus, paired=False):
    if focus is None:
        return LOW, HIGH, 0, DISPLAY_BINS, centers
    start, stop = focus
    # Align focused sweeps to the full-sweep display grid.
    first = max(0, min(DISPLAY_BINS - 1, int(np.floor((start - LOW) / OVERVIEW_WIDTH))))
    end = min(DISPLAY_BINS, max(first + 1, int(np.ceil((stop - LOW) / OVERVIEW_WIDTH))))
    low, high = LOW + first * OVERVIEW_WIDTH, LOW + end * OVERVIEW_WIDTH
    # Bracket the visible span with adjacent centers so they fill one another's DC gaps.
    left = max(0, min(len(centers) - 2, int(centers.searchsorted(low)) - 1))
    right = min(len(centers), max(left + 2, int(centers.searchsorted(high, side="right")) + 1))
    if paired:
        # Keep the partner of every retained center even at focused-span edges.
        left -= left % 2
        right += right % 2
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
        if args.rate == MAX_RATE:
            self.radio.set_master_clock_rate(MAX_RATE * 1e6)
        self.radio.set_rx_rate(args.rate * 1e6, 0)
        self.rate = self.radio.get_rx_rate(0)
        self.stream_rate = self.rate
        self.master_clock = self.radio.get_master_clock_rate()
        self.radio.set_rx_bandwidth(min(self.rate, MAX_RATE * 1e6), 0)
        self.bandwidth = self.radio.get_rx_bandwidth(0)
        self.radio.set_rx_antenna(args.antenna, 0)
        self.radio.set_rx_gain(args.gain, 0)
        stream_args = uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [0]
        self.stream = self.radio.get_rx_stream(stream_args)
        self.metadata = uhd.types.RXMetadata()
        self.command = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        self.command.stream_now = True
        self.size = 0
        self.set_fft_size(FFT_SIZE)

    def set_fft_size(self, size):
        if self.size == size:
            return
        self.size = size
        self.window = np.hanning(size)
        self.window_sum = self.window.sum()
        self.command.num_samps = size * AVERAGES
        self.samples = np.empty((1, self.command.num_samps), dtype=np.complex64)

    def set_stream_rate(self, rate):
        if self.stream_rate == rate:
            return
        self.radio.set_rx_rate(rate, 0)
        if (self.radio.get_rx_rate(0) != rate or self.radio.get_master_clock_rate() != self.master_clock
                or self.radio.get_rx_bandwidth(0) != self.bandwidth):
            raise RuntimeError(f"Could not set {rate / 1e6:g} MS/s at the current master clock and analog bandwidth")
        self.stream_rate = rate

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
        self.requested_gain = args.gain
        self.capture_gain = None
        self.image_reject_available = False
        self.image_reject_requested = False
        self.image_reference = None
        self.image_region = None
        self.shifted_peaks = RecentPeaks()
        self.recent_details = deque(maxlen=4)
        self.recent_peaks = RecentPeaks()
        self.snapshot = dict(demo=args.demo, fpga=bool(args.fpga_band_sweep), trace=[], number=0, progress=0, error=None,
                             low=LOW, high=HIGH, seconds=None, detail=b"", peak_detail=b"", pair_detail=b"")
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        receiver = None
        paired = self.args.hop_plan == "paired"
        while True:
            try:
                if receiver is None:
                    if self.args.demo:
                        receiver = Demo(self.args.rate * 1e6)
                    else:
                        receiver = Receiver(self.args)
                        applied_request = self.args.gain
                        self.capture_gain = receiver.radio.get_rx_gain(0)
                        self.capture_rate = receiver.rate
                        self.capture_bandwidth = receiver.bandwidth
                        self.master_clock = receiver.master_clock
                    centers, offsets, usable = captures(receiver.rate, receiver.bandwidth, paired)
                    self.image_reject_available = (
                        not self.args.demo and paired and receiver.rate == receiver.bandwidth == 56e6
                    )
                    shifted = offset_hop_plan(centers) if self.image_reject_available else centers
                    if self.args.fpga_band_sweep:
                        receiver = BandSweep(receiver, np.union1d(centers, shifted),
                                             unified_gain=self.args.gain_table == "unified")
                gain = self.requested_gain
                if not self.args.demo and gain != applied_request:
                    if isinstance(receiver, BandSweep):
                        receiver.control.leave()
                        radio = receiver.receiver.radio
                    else:
                        radio = receiver.radio
                    radio.set_rx_gain(gain, 0)
                    self.capture_gain = radio.get_rx_gain(0)
                    applied_request = gain
                    self.recent_peaks = RecentPeaks()
                focus = self.focus
                if focus is None and self.recent_peaks.region is not None:
                    self.recent_peaks = RecentPeaks()
                plan = None if self.args.demo else continuous_plan(focus, receiver.rate, receiver.bandwidth)
                image_region = ((focus, self.capture_gain) if plan is None and self.image_reject_available
                                and self.image_reject_requested else None)
                if image_region != self.image_region:
                    self.image_region = image_region
                    self.image_reference = None
                    self.recent_peaks = RecentPeaks()
                    self.shifted_peaks = RecentPeaks()
                if plan is None:
                    if not self.args.demo:
                        rf = receiver.receiver if isinstance(receiver, BandSweep) else receiver
                        rf.set_stream_rate(rf.rate)
                    leg = None
                    if image_region is not None:
                        leg = "A" if self.image_reference is None else "B"
                    layout = (shifted if leg == "B" else centers, offsets, usable)
                    self.scan(receiver, focus, paired, layout, leg)
                    self.cache_detail()
                else:
                    self.continuous(receiver, focus, plan, gain)
            except Exception as error:
                self.snapshot = dict(self.snapshot, error=str(error), progress=0)
                receiver = None
                self.recent_peaks = RecentPeaks()
                self.shifted_peaks = RecentPeaks()
                self.image_reference = None
                self.image_region = None
                time.sleep(3)

    def scan(self, receiver, focus=None, paired=False, layout=None, leg=None):
        centers, offsets, usable = layout if layout is not None else captures(receiver.rate, receiver.bandwidth, paired)
        low, high, first, count, centers = scan_range(centers, focus, paired)
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
            history = self.shifted_peaks if leg == "B" else self.recent_peaks
            peaks = history.update((low, high), bins, time.perf_counter())
        pair_detail = b""
        reference_has_peaks = False
        if leg == "A":
            self.image_reference = bins, peaks
        elif leg == "B":
            reference, reference_peaks = self.image_reference
            rejected = minimum_trace(reference, bins)
            pair_detail = reference.astype("<f4", copy=False).tobytes() + rejected.astype("<f4", copy=False).tobytes()
            if reference_peaks is not None:
                pair_detail += reference_peaks.astype("<f4", copy=False).tobytes()
                reference_has_peaks = True
            self.image_reference = None
        self.publish(bins, peaks, low, high, first * BINS_PER_OVERVIEW, BINS_PER_OVERVIEW,
                     seconds=round(time.perf_counter() - started, 2), hops=len(centers), focus=focus,
                     image_leg=leg, pair_detail=pair_detail, reference_has_peaks=reference_has_peaks)

    def continuous(self, receiver, focus, plan, gain):
        center, low, high, first, size = plan
        radio = receiver.receiver if isinstance(receiver, BandSweep) else receiver
        full_window = first is None
        span = focus[1] - focus[0]
        fft_size = 65536 if span <= 500e3 else 32768 if span <= 2e6 else FFT_SIZE
        stream_rate = radio.rate
        if fft_size != FFT_SIZE and radio.master_clock == MAX_RATE * 1e6:
            stream_rate = NARROW_RATE
            # Keep the narrow view off DC and within the decimator's passband.
            midpoint = (focus[0] + focus[1]) / 2
            offset = stream_rate / 4
            center = midpoint - offset if midpoint - offset >= LOW else midpoint + offset
        radio.set_stream_rate(stream_rate)
        if isinstance(receiver, BandSweep):
            actual = receiver.tune_continuous(center)
        else:
            actual = radio.tune(center)
        native_slice = None
        if fft_size != FFT_SIZE:
            # Keep each native FFT bin instead of combining it on the sweep grid.
            width = stream_rate / fft_size
            edge = actual - stream_rate / 2 - width / 2
            start = max(0, int(np.floor((focus[0] - edge) / width)))
            stop = min(fft_size, int(np.ceil((focus[1] - edge) / width)))
            native_slice = slice(start, stop)
            low, high = edge + start * width, edge + stop * width
            first, size, group = None, stop - start, 1
        elif full_window:
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
        radio.set_fft_size(fft_size)
        radio.start_continuous()
        try:
            while self.focus == focus and self.requested_gain == gain:
                if not radio.receive(actual, continuous=True):
                    gaps += 1
                    continue
                power = radio.spectrum()
                if native_slice is not None:
                    bins[:] = 10 * np.log10(np.maximum(1e-16, power[native_slice]))
                elif full_window:
                    bins[:] = 10 * np.log10(np.maximum(1e-16, power))
                else:
                    bins.fill(-np.inf)
                    np.maximum.at(bins, indices, 10 * np.log10(np.maximum(1e-16, power[mask])))
                now = time.perf_counter()
                peaks = self.recent_peaks.update((low, high), bins, now)
                windows += 1
                if now >= next_update:
                    self.publish(bins, peaks, low, high, first, group, seconds=CONTINUOUS_UPDATE_SECONDS,
                                 hops=1, continuous=True, windows=windows, gaps=gaps, focus=focus,
                                 fft_size=fft_size, full_window=full_window, stream_rate=stream_rate)
                    self.cache_detail()
                    next_update = now + CONTINUOUS_UPDATE_SECONDS
        finally:
            try:
                radio.stop_continuous()
            finally:
                radio.set_fft_size(FFT_SIZE)

    def publish(self, bins, peaks, low, high, first, group, seconds, hops,
                continuous=False, windows=0, gaps=0, focus=None, fft_size=FFT_SIZE, full_window=False,
                stream_rate=None, image_leg=None, pair_detail=b"", reference_has_peaks=False):
        overview = bins.reshape(-1, group).max(axis=1)
        trace = [round(float(value), 1) if np.isfinite(value) else None for value in overview]
        self.snapshot = dict(self.snapshot, trace=trace, number=self.snapshot["number"] + 1, progress=1, error=None,
                             gain=getattr(self, "capture_gain", None), capture_focus=focus,
                             rate_hz=stream_rate if stream_rate is not None else getattr(self, "capture_rate", None),
                             bandwidth_hz=getattr(self, "capture_bandwidth", None),
                             master_clock_hz=getattr(self, "master_clock", None),
                             low=low, high=high, detail_offset=first, detail_count=len(bins),
                             detail=bins.astype("<f4", copy=False).tobytes(),
                             peak_detail=peaks.astype("<f4", copy=False).tobytes() if peaks is not None else b"",
                             image_leg=image_leg, pair_detail=pair_detail, reference_has_peaks=reference_has_peaks,
                             seconds=seconds, hops=hops, continuous=continuous, windows=windows, gaps=gaps,
                             fft_size=fft_size, full_window=full_window)

    def cache_detail(self):
        frame = self.snapshot
        self.recent_details.append((frame["number"], frame["detail"], frame["peak_detail"], frame["pair_detail"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use simulated RF data")
    parser.add_argument("--device", default="type=b200,product=B210,num_recv_frames=128", help="UHD device arguments")
    parser.add_argument("--rate", type=float, default=MAX_RATE, help="receive rate in MS/s (default: %(default)s)")
    parser.add_argument("--gain", type=float, default=35, help="receive gain in dB")
    parser.add_argument("--antenna", choices=["RX2", "TX/RX"], default="RX2")
    parser.add_argument("--fpga-band-sweep", nargs="?", type=Path, const=BUNDLED_RETUNE_IMAGE,
                        help="enable Fast Lock with the bundled image or an explicit image path")
    parser.add_argument("--hop-plan", choices=["uniform", "paired"], default="paired",
                        help="sweep center spacing (default: %(default)s)")
    parser.add_argument("--gain-table", choices=["stock", "unified"], default="stock",
                        help="receive gain table (unified requires a compatible FPGA sweep image)")
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

    gain_stage_data = json.dumps(gain_stages).encode()
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                kind, body = "text/html; charset=utf-8", page
            elif self.path == "/api/sweep":
                snapshot = sweep.snapshot
                frame = {key: value for key, value in snapshot.items() if key not in ("detail", "peak_detail", "pair_detail")}
                frame["gain_table"] = args.gain_table
                frame["requested_gain"] = None if args.demo else sweep.requested_gain
                frame["focus"] = sweep.focus
                frame["peaks"] = bool(snapshot["peak_detail"])
                frame["image_reject_available"] = sweep.image_reject_available
                frame["image_reject_requested"] = sweep.image_reject_requested
                frame["image_pair"] = bool(snapshot["pair_detail"])
                kind, body = "application/json", json.dumps(frame, allow_nan=False).encode()
            elif self.path == "/api/gain-stages":
                kind, body = "application/json", gain_stage_data
            elif self.path.startswith("/api/detail/"):
                path, _, query = self.path.partition("?")
                frame = sweep.snapshot
                if path == f"/api/detail/{frame['number']}" and frame["detail"]:
                    body, peaks, pair = frame["detail"], frame["peak_detail"], frame["pair_detail"]
                else:
                    cached = next(((data, peaks, pair) for number, data, peaks, pair in sweep.recent_details.copy()
                                   if path == f"/api/detail/{number}"), None)
                    if cached is None:
                        self.send_error(404)
                        return
                    body, peaks, pair = cached
                options = query.split("&")
                if "peaks=1" in options and peaks:
                    body += peaks
                if "pair=1" in options and pair:
                    body += pair
                kind = "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.reply(kind, body)

        def do_POST(self):
            if self.path not in ("/api/focus", "/api/gain", "/api/image-reject"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                    raise ValueError
                value = json.loads(self.rfile.read(length))
                if self.path == "/api/image-reject":
                    if not sweep.image_reject_available or type(value) is not bool:
                        raise ValueError
                    sweep.image_reject_requested = value
                    self.reply("application/json", json.dumps(value).encode())
                    return
                if self.path == "/api/gain":
                    if args.demo or type(value) not in (int, float) or not 0 <= value <= 76:
                        raise ValueError
                    sweep.requested_gain = value
                    self.reply("application/json", json.dumps(value).encode())
                    return
                focus = value
                if focus is not None:
                    if (not isinstance(focus, list) or len(focus) != 2
                            or any(type(value) not in (int, float) for value in focus)
                            or not LOW <= focus[0] < focus[1] <= HIGH):
                        raise ValueError
                    focus = tuple(focus)
            except (ValueError, UnicodeError):
                messages = {
                    "/api/gain": "Expected live RX gain from 0 to 76 dB",
                    "/api/focus": "Expected a frequency range in Hz or null",
                    "/api/image-reject": "Image rejection requires a live paired sweep at 56 MS/s and a boolean value",
                }
                self.send_error(400, messages[self.path])
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
