"""Local, receive-only B210 spectrum dashboard."""
import argparse
import json
import mimetypes
import sqlite3
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

from fpga_sweep import BandSweep
from bookmarks import BookmarkAPI, BookmarkStore, default_bookmark_path
from demodulation import DEMO_FM, DEMO_STEREO_HZ, DemoStereo, demo_iq
from listening import ChannelTap, CaptureUnavailable, IQCapture, Listening, bookmark_tune, validate_tune

LOW, HIGH = 70e6, 6e9
MAX_RATE = 56
NARROW_RATE = MAX_RATE * 1e6 / 4
DEMO_CAPTURE_RATE = 2.4e6
DEFAULT_AVERAGES = 8
MAX_AVERAGES = 32
DISPLAY_BINS = 4096
BINS_PER_OVERVIEW = 384
DETAIL_BINS = DISPLAY_BINS * BINS_PER_OVERVIEW
OVERVIEW_WIDTH = (HIGH - LOW) / DISPLAY_BINS
FFT_SIZE = 16384
REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = REPO_ROOT / "build/spectrum"
BUNDLED_RETUNE_IMAGE = REPO_ROOT / "resources/firmware/usrp_b210_retune.bin"
UNIFIED_GAIN_ROM = REPO_ROOT / "src/firmware/rtl/control/unified_rx_gain.mem"
PEAK_BUCKET_SECONDS = 0.2
CONTINUOUS_UPDATE_SECONDS = 0.04
DETAIL_WIDTH = (HIGH - LOW) / DETAIL_BINS
RX_PORTS = ("TX/RX", "RX2")
RX_ANTENNAS = tuple(f"{side}:{port}" for side in "AB" for port in RX_PORTS)


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


def offset_hop_plan(centers, shift=5e6):
    # Move both hops in each pair together and pin the pairs at the band edges.
    pair = np.arange(len(centers) // 2)
    taper = np.minimum(1, np.minimum(pair, pair[-1] - pair) / 6)
    return centers + np.repeat(shift * taper, 2)


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


def spectrum_power(samples, window, window_sum):
    blocks = samples.reshape(-1, len(window))
    fft = np.fft.fftshift(np.fft.fft((blocks - blocks.mean(axis=1, keepdims=True)) * window), axes=1)
    return np.mean(np.abs(fft / window_sum) ** 2, axis=0)


class Receiver:
    overload = None

    def __init__(self, args, antenna):
        import uhd

        self.uhd = uhd
        self.radio = uhd.usrp.MultiUSRP(args.device)
        if self.radio.get_mboard_name() == "B210":
            self.radio.set_rx_subdev_spec(uhd.usrp.SubdevSpec("A:A A:B"))
        self.antennas = {}
        for channel, side in enumerate("AB"[:self.radio.get_rx_num_channels()]):
            ports = self.radio.get_rx_antennas(channel)
            self.antennas.update({f"{side}:{port}": (channel, port) for port in RX_PORTS if port in ports})
        if antenna not in self.antennas:
            raise RuntimeError(f"RX antenna {antenna} is unavailable on this receiver")
        self.channel, port = self.antennas[antenna]
        if args.rate == MAX_RATE:
            self.radio.set_master_clock_rate(MAX_RATE * 1e6)
        self.radio.set_rx_rate(args.rate * 1e6, self.channel)
        self.rate = self.radio.get_rx_rate(self.channel)
        self.stream_rate = self.rate
        self.master_clock = self.radio.get_master_clock_rate()
        self.radio.set_rx_bandwidth(min(self.rate, MAX_RATE * 1e6), self.channel)
        self.bandwidth = self.radio.get_rx_bandwidth(self.channel)
        self.set_antenna_port(self.channel, port)
        self.set_gain(args.gain)
        self.stream = self.open_stream(self.channel)
        self.metadata = uhd.types.RXMetadata()
        self.command = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        self.command.stream_now = True
        self.size = 0
        self.averages = args.averages
        self.set_fft_size(FFT_SIZE)

    def open_stream(self, channel):
        stream_args = self.uhd.usrp.StreamArgs("fc32", "sc16")
        stream_args.channels = [channel]
        return self.radio.get_rx_stream(stream_args)

    def set_antenna_port(self, channel, port):
        self.radio.set_rx_antenna(port, channel)
        if self.radio.get_rx_antenna(channel) != port:
            raise RuntimeError(f"Receiver did not select {port} on UHD channel {channel}")

    def select_antenna(self, antenna, gain):
        channel, port = self.antennas[antenna]
        if channel != self.channel:
            # Release the old streamer before opening the other chain at the
            # single-channel clock rate. The sweep has already stopped RX.
            self.stream = None
            self.radio.set_rx_rate(self.stream_rate, channel)
            if self.radio.get_rx_bandwidth(channel) != self.bandwidth:
                self.radio.set_rx_bandwidth(self.bandwidth, channel)
            self.set_antenna_port(channel, port)
            self.radio.set_rx_gain(gain, channel)
            self.stream = self.open_stream(channel)
            self.check_stream_settings(self.stream_rate, channel)
            self.channel = channel
        else:
            self.set_antenna_port(channel, port)
        if self.overload is not None:
            self.overload.select(self.radio.get_rx_subdev_name(self.channel))
            self.overload.rearm()

    def set_gain(self, gain):
        self.radio.set_rx_gain(gain, self.channel)
        if self.overload is not None:
            self.overload.clear()
            self.overload.rearm()

    def get_gain(self):
        return self.radio.get_rx_gain(self.channel)

    def set_fft_size(self, size, averages=None):
        averages = self.averages if averages is None else averages
        if self.size == size and self.averages == averages:
            return
        if self.size != size:
            self.window = np.hanning(size)
            self.window_sum = self.window.sum()
        self.size = size
        self.averages = averages
        self.command.num_samps = size * averages
        self.samples = np.empty((1, self.command.num_samps), dtype=np.complex64)

    def check_stream_settings(self, rate, channel):
        if (self.radio.get_rx_rate(channel) != rate or self.radio.get_master_clock_rate() != self.master_clock
                or self.radio.get_rx_bandwidth(channel) != self.bandwidth):
            raise RuntimeError(f"Could not set {rate / 1e6:g} MS/s at the current master clock and analog bandwidth")

    def set_stream_rate(self, rate):
        if self.stream_rate == rate:
            return
        self.radio.set_rx_rate(rate, self.channel)
        self.check_stream_settings(rate, self.channel)
        self.stream_rate = rate
        if self.overload is not None:
            self.overload.rearm()

    def tune(self, center):
        if self.overload is not None:
            self.overload.observe(force=True)
        self.radio.set_rx_freq(self.uhd.types.TuneRequest(float(center)), self.channel)
        actual = self.radio.get_rx_freq(self.channel)
        if self.overload is not None:
            self.overload.rearm()
        return actual

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

    def receive(self, center, continuous=False, tap=None):
        received = 0
        while received < self.samples.shape[1]:
            end = received + tap.count if tap else self.samples.shape[1]
            count = self.stream.recv(self.samples[:, received:end], self.metadata, 0.25)
            if continuous and self.metadata.error_code == self.uhd.types.RXMetadataErrorCode.overflow:
                return False  # Discard the partial FFT window across the missing samples.
            if not count or self.metadata.error_code != self.uhd.types.RXMetadataErrorCode.none:
                raise RuntimeError(f"RX at {center / 1e6:.2f} MHz: {self.metadata.strerror()}")
            if tap:
                tap.push(self.samples[0, received:received + count])
            received += count
        if self.overload is not None:
            self.overload.observe()
        return True

    def spectrum(self):
        return spectrum_power(self.samples, self.window, self.window_sum)


class Demo:
    def __init__(self, rate):
        self.rate = rate
        self.bandwidth = min(rate, MAX_RATE * 1e6)
        self.random = np.random.default_rng()

    def measure(self, center, offsets):
        frequency = center + offsets
        power = 10 ** (self.random.normal(-93, 2, frequency.size) / 10)
        for signal, width, level in [(DEMO_FM[0][0], 85e3, -48), (DEMO_STEREO_HZ, 90e3, -44), (450e6, 0.6e6, -53),
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
        self.listening = Listening()
        self.focus = None
        self.navigation_lock = threading.Lock()
        self.navigation_revision = 0
        self.bookmark_channel = None
        self.requested_gain = args.gain
        self.requested_antenna = None if args.demo else args.antenna
        self.antenna_options = ()
        self.antenna_error = None
        self.capture_antenna = None
        self.requested_averages = args.averages
        self.capture_gain = None
        self.capture_averages = None
        self.image_reject_available = False
        self.image_reject_requested = False
        self.image_references = []
        self.image_region = None
        self.shifted_peaks = defaultdict(RecentPeaks)
        self.recent_details = deque(maxlen=4)
        self.recent_peaks = RecentPeaks()
        self.snapshot = dict(demo=args.demo, fpga=bool(args.fpga_band_sweep), trace=[], number=0, progress=0, error=None,
                             low=LOW, high=HIGH, seconds=None, detail=b"", peak_detail=b"", image_detail=b"",
                             rx_overload=None)
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
                        antenna = self.capture_antenna or self.args.antenna
                        receiver = Receiver(self.args, antenna)
                        self.capture_antenna = antenna
                        self.antenna_options = tuple(receiver.antennas)
                        applied_request = self.args.gain
                        self.capture_gain = receiver.get_gain()
                        self.capture_rate = receiver.rate
                        self.capture_bandwidth = receiver.bandwidth
                        self.master_clock = receiver.master_clock
                    centers, offsets, usable = captures(receiver.rate, receiver.bandwidth, paired)
                    self.image_reject_available = (
                        not self.args.demo and paired and receiver.rate == receiver.bandwidth == 56e6
                    )
                    plans = (centers,)
                    if self.image_reject_available:
                        plans = centers, offset_hop_plan(centers), offset_hop_plan(centers, -5e6)
                    if self.args.fpga_band_sweep:
                        receiver = BandSweep(receiver, np.unique(np.concatenate(plans)),
                                             unified_gain=self.args.gain_table == "unified")
                rf = receiver.receiver if isinstance(receiver, BandSweep) else receiver
                gain = self.requested_gain
                antenna = self.requested_antenna
                if not self.args.demo and antenna != self.capture_antenna:
                    try:
                        receiver.select_antenna(antenna, gain)
                    except Exception as error:
                        with self.navigation_lock:
                            if self.requested_antenna == antenna:
                                self.requested_antenna = self.capture_antenna
                                self.antenna_error = f"Could not switch to {antenna}: {error}"
                        raise
                    self.capture_antenna = antenna
                    self.capture_gain = rf.get_gain()
                    self.snapshot = dict(self.snapshot, rx_overload=None)
                    self.recent_peaks = RecentPeaks()
                    self.shifted_peaks.clear()
                if not self.args.demo and gain != applied_request:
                    if isinstance(receiver, BandSweep):
                        receiver.control.leave()
                    rf.set_gain(gain)
                    self.capture_gain = rf.get_gain()
                    applied_request = gain
                    self.snapshot = dict(self.snapshot, rx_overload=None)
                    self.recent_peaks = RecentPeaks()
                averages = self.requested_averages
                if not self.args.demo:
                    if rf.averages != averages:
                        self.recent_peaks = RecentPeaks()
                        self.shifted_peaks.clear()
                    self.capture_averages = averages
                focus = self.focus
                if focus is None and self.recent_peaks.region is not None:
                    self.recent_peaks = RecentPeaks()
                plan = None if self.args.demo else continuous_plan(focus, receiver.rate, receiver.bandwidth)
                demo_continuous = self.args.demo and focus and focus[1] - focus[0] <= 2e6
                image_region = ((focus, self.capture_antenna, self.capture_gain, averages)
                                if plan is None and self.image_reject_available
                                and self.image_reject_requested else None)
                if image_region != self.image_region:
                    self.image_region = image_region
                    self.image_references.clear()
                    self.recent_peaks = RecentPeaks()
                    self.shifted_peaks.clear()
                if demo_continuous:
                    self.demo_continuous(receiver, focus, averages)
                elif plan is None:
                    if not self.args.demo:
                        rf.set_stream_rate(rf.rate)
                        rf.set_fft_size(FFT_SIZE, averages)
                    leg = None
                    slot = 0
                    if image_region is not None:
                        slot = len(self.image_references)
                        leg = "ABC"[slot]
                    layout = plans[slot], offsets, usable
                    if self.scan(receiver, focus, paired, layout, leg):
                        self.cache_detail()
                else:
                    self.continuous(receiver, focus, plan, gain, averages, antenna)
            except Exception as error:
                self.listening.interrupt()
                self.snapshot = dict(self.snapshot, error=str(error), progress=0, rx_overload=None)
                receiver = None
                rf = None
                self.recent_peaks = RecentPeaks()
                self.shifted_peaks.clear()
                self.image_references.clear()
                self.image_region = None
                time.sleep(3)

    def scan(self, receiver, focus=None, paired=False, layout=None, leg=None):
        centers, offsets, usable = layout if layout is not None else captures(receiver.rate, receiver.bandwidth, paired)
        low, high, first, count, centers = scan_range(centers, focus, paired)
        detail_count = count * BINS_PER_OVERVIEW
        overload = receiver.receiver.overload if isinstance(receiver, BandSweep) else None
        self.snapshot = dict(self.snapshot, progress=0)
        started = time.perf_counter()
        bins = np.full(detail_count, -np.inf, dtype=np.float32)
        for index, center in enumerate(centers):
            if self.requested_antenna != self.capture_antenna:
                return False
            frequency, power = receiver.measure(center, offsets)
            mask = usable & (frequency >= low) & (frequency <= high) & np.isfinite(power)
            indices = ((frequency[mask] - low) * detail_count / (high - low)).astype(int)
            np.maximum.at(bins, np.minimum(indices, detail_count - 1), power[mask])
            self.snapshot = dict(self.snapshot, progress=(index + 1) / len(centers), error=None,
                                 rx_overload=overload.flags if overload is not None else None)
        if self.requested_antenna != self.capture_antenna:
            return False
        measured = np.isfinite(bins)
        bins[measured] = 10 * np.log10(np.maximum(1e-16, bins[measured]))
        peaks = None
        if focus is not None:
            history = self.shifted_peaks[leg] if leg in ("B", "C") else self.recent_peaks
            peaks = history.update((low, high), bins, time.perf_counter())
        image_detail = b""
        if leg in ("A", "B"):
            self.image_references.append((bins, peaks))
        elif leg == "C":
            a, b = self.image_references
            rejected = minimum_trace(minimum_trace(a[0], b[0]), bins)
            # The raw C sweep is served first, followed by the minimum and the two raw references.
            image_detail = b"".join(trace.astype("<f4", copy=False).tobytes()
                                    for trace in (rejected, *a, *b) if trace is not None)
            self.image_references.clear()
        self.publish(bins, peaks, low, high, first * BINS_PER_OVERVIEW, BINS_PER_OVERVIEW,
                     seconds=round(time.perf_counter() - started, 2), hops=len(centers), focus=focus,
                     image_leg=leg, image_detail=image_detail, rx_overload=self.snapshot["rx_overload"])
        return True

    def demo_continuous(self, receiver, focus, averages):
        rate = DEMO_CAPTURE_RATE
        center = sum(focus) / 2
        fft_size = 8192
        fft_window = np.hanning(fft_size)
        fft_sum = fft_window.sum()
        count = fft_size * averages
        width = rate / fft_size
        low = center - rate / 2 - width / 2
        high = low + rate
        capture = IQCapture(center, rate, (max(LOW, center - rate / 2), min(HIGH, center + rate / 2)), focus)
        peaks = RecentPeaks()
        deadline = time.monotonic()
        sample_index = windows = 0
        next_update = 0
        stereo = DemoStereo(center, rate)
        with ChannelTap(self.listening, capture) as tap:
            while self.focus == focus:
                deadline += count / rate
                time.sleep(max(0, deadline - time.monotonic()))
                samples = demo_iq(center, rate, sample_index, count, receiver.random, stereo)
                sample_index += count
                tap.push(samples)
                power = spectrum_power(samples, fft_window, fft_sum)
                bins = (10 * np.log10(np.maximum(1e-16, power))).astype(np.float32)
                now = time.monotonic()
                held = peaks.update((low, high), bins, now)
                windows += 1
                if now >= next_update:
                    self.publish(bins, held, low, high, None, 4, seconds=CONTINUOUS_UPDATE_SECONDS,
                                 hops=1, continuous=True, windows=windows, focus=focus, fft_size=fft_size,
                                 stream_rate=rate)
                    self.cache_detail()
                    next_update = now + CONTINUOUS_UPDATE_SECONDS

    def continuous(self, receiver, focus, plan, gain, averages, antenna):
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
        radio.set_fft_size(fft_size, averages)
        half = min(stream_rate, radio.bandwidth) / 2
        capture = IQCapture(actual, stream_rate, (max(LOW, actual - half), min(HIGH, actual + half)), focus, antenna)
        with ChannelTap(self.listening, capture) as tap:
            radio.start_continuous()
            try:
                while (self.focus == focus and self.requested_antenna == antenna
                       and self.requested_gain == gain and self.requested_averages == averages):
                    if not radio.receive(actual, continuous=True, tap=tap):
                        gaps += 1
                        tap.gap()
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
                                     fft_size=fft_size, full_window=full_window, stream_rate=stream_rate,
                                     rx_overload=radio.overload.flags if radio.overload is not None else None)
                        self.cache_detail()
                        next_update = now + CONTINUOUS_UPDATE_SECONDS
            finally:
                radio.stop_continuous()

    def publish(self, bins, peaks, low, high, first, group, seconds, hops,
                continuous=False, windows=0, gaps=0, focus=None, fft_size=FFT_SIZE, full_window=False,
                stream_rate=None, image_leg=None, image_detail=b"", rx_overload=None):
        overview = bins.reshape(-1, group).max(axis=1)
        trace = [round(float(value), 1) if np.isfinite(value) else None for value in overview]
        self.snapshot = dict(self.snapshot, trace=trace, number=self.snapshot["number"] + 1, progress=1, error=None,
                             antenna=self.capture_antenna, gain=self.capture_gain, capture_focus=focus,
                             rate_hz=stream_rate if stream_rate is not None else getattr(self, "capture_rate", None),
                             bandwidth_hz=getattr(self, "capture_bandwidth", None),
                             master_clock_hz=getattr(self, "master_clock", None),
                             fft_averages=getattr(self, "capture_averages", None),
                             low=low, high=high, detail_offset=first, detail_count=len(bins),
                             detail=bins.astype("<f4", copy=False).tobytes(),
                             peak_detail=peaks.astype("<f4", copy=False).tobytes() if peaks is not None else b"",
                             image_leg=image_leg, image_detail=image_detail,
                             seconds=seconds, hops=hops, continuous=continuous, windows=windows, gaps=gaps,
                             fft_size=fft_size, full_window=full_window, rx_overload=rx_overload)

    def cache_detail(self):
        frame = self.snapshot
        self.recent_details.append((frame["number"], frame["detail"], frame["peak_detail"], frame["image_detail"]))


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

    gain_stage_data = json.dumps(gain_stages).encode()

    class Handler(BookmarkAPI, BaseHTTPRequestHandler):
        def bookmark_recalled(self, bookmark):
            preset = bookmark_tune(bookmark)
            sweep.bookmark_channel = asdict(preset)
            listen = sweep.listening.recall(preset, LOW, HIGH)
            return {"channel": sweep.bookmark_channel, "listen": listen}

        def do_GET(self):
            if self.handle_bookmarks():
                return
            path = self.path.partition("?")[0]
            if path == "/":
                try:
                    body = (DASHBOARD / "index.html").read_bytes()
                except FileNotFoundError:
                    self.send_error(503, "Dashboard not built. Run npm ci and npm run build.")
                    return
                kind = "text/html; charset=utf-8"
            elif self.path in ("/api/sweep", "/api/sweep?survey=1"):
                snapshot = sweep.snapshot
                frame = {key: value for key, value in snapshot.items() if key not in ("detail", "peak_detail", "image_detail")}
                frame["gain_table"] = args.gain_table
                frame["requested_gain"] = None if args.demo else sweep.requested_gain
                frame["requested_averages"] = None if args.demo else sweep.requested_averages
                with sweep.navigation_lock:
                    frame["focus"] = sweep.focus
                    frame["requested_antenna"] = sweep.requested_antenna
                    frame["antenna_options"] = sweep.antenna_options
                    frame["antenna_error"] = sweep.antenna_error
                    frame["navigation_revision"] = sweep.navigation_revision
                    frame["bookmark_channel"] = sweep.bookmark_channel
                    frame["listen"] = sweep.listening.snapshot()
                frame["peaks"] = bool(snapshot["peak_detail"])
                frame["image_reject_available"] = sweep.image_reject_available
                frame["image_reject_requested"] = sweep.image_reject_requested
                frame["image_set"] = bool(snapshot["image_detail"])
                frame["survey_number"] = snapshot["number"]
                kind, body = "application/json", json.dumps(frame, allow_nan=False).encode()
            elif self.path == "/api/listen":
                return self.listen_json(200, sweep.listening.snapshot())
            elif self.path == "/api/listen/acars":
                return self.listen_json(200, sweep.listening.acars_log())
            elif self.path in ("/api/listen/audio", "/api/listen/iq"):
                iq = self.path.endswith("/iq")
                request, _, _, epoch = sweep.listening.read_settings()
                if request is None or (request.mode != "iq" if iq else not request.audio_channels):
                    return self.listen_json(409, {"error": "Start a channel in IQ mode" if iq else "Start a channel in an audio mode"})
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                if not iq:
                    self.send_header("X-Audio-Channels", str(request.audio_channels))
                self.end_headers()
                self.connection.settimeout(2)
                try:
                    sweep.listening.stream(self.wfile.write, epoch, request.sample_size)
                except OSError:
                    pass
                return
            elif self.path == "/api/gain-stages":
                kind, body = "application/json", gain_stage_data
            elif self.path.startswith("/api/detail/"):
                path, _, query = self.path.partition("?")
                frame = sweep.snapshot
                if path == f"/api/detail/{frame['number']}" and frame["detail"]:
                    body, peaks, images = frame["detail"], frame["peak_detail"], frame["image_detail"]
                else:
                    cached = next(((data, peaks, images) for number, data, peaks, images in sweep.recent_details.copy()
                                   if path == f"/api/detail/{number}"), None)
                    if cached is None:
                        self.send_error(404)
                        return
                    body, peaks, images = cached
                options = query.split("&")
                if "peaks=1" in options and peaks:
                    body += peaks
                if "image=1" in options and images:
                    body += images
                kind = "application/octet-stream"
            elif path.startswith("/assets/"):
                name = path.removeprefix("/assets/")
                if not name or name != Path(name).name or name in (".", ".."):
                    self.send_error(404)
                    return
                try:
                    body = (DASHBOARD / "assets" / name).read_bytes()
                except (FileNotFoundError, IsADirectoryError):
                    self.send_error(404)
                    return
                kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.reply(kind, body)

        def do_POST(self):
            if self.handle_bookmarks():
                return
            if self.path == "/api/listen":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get_content_type() != "application/json" or not 0 < length <= 512:
                        raise ValueError("Expected a JSON channel selection or null")
                    value = json.loads(self.rfile.read(length))
                    full = value == {"stop": "full"}
                    request = validate_tune(value, LOW, HIGH) if value is not None and not full else None
                except (ValueError, UnicodeError) as error:
                    return self.listen_json(400, {"error": str(error)})
                if request and not sweep.listening.snapshot()["available"]:
                    return self.listen_json(503, {"error": "Install SciPy on the receiver to enable channel demodulation"})
                with sweep.navigation_lock:
                    if full:
                        sweep.focus = None
                    try:
                        state = sweep.listening.configure(request, sweep.focus)
                    except CaptureUnavailable as error:
                        return self.listen_json(409, {"error": str(error)})
                    state["survey_focus"] = sweep.focus
                return self.listen_json(200, state)
            if self.path not in ("/api/focus", "/api/antenna", "/api/gain", "/api/averages", "/api/image-reject"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get_content_type() != "application/json" or not 0 < length <= 128:
                    raise ValueError
                value = json.loads(self.rfile.read(length))
                if self.path == "/api/antenna":
                    with sweep.navigation_lock:
                        if not isinstance(value, str) or value not in sweep.antenna_options:
                            raise ValueError
                        sweep.requested_antenna = value
                        sweep.antenna_error = None
                    self.reply("application/json", json.dumps(value).encode())
                    return
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
                if self.path == "/api/averages":
                    if args.demo or type(value) is not int or not 1 <= value <= MAX_AVERAGES:
                        raise ValueError
                    sweep.requested_averages = value
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
                    "/api/antenna": "Expected one of the live receiver's antenna options",
                    "/api/gain": "Expected live RX gain from 0 to 76 dB",
                    "/api/averages": f"Expected an integer from 1 to {MAX_AVERAGES} on a live receiver",
                    "/api/focus": "Expected a frequency range in Hz or null",
                    "/api/image-reject": "Image rejection requires a live paired sweep at 56 MS/s and a boolean value",
                }
                self.send_error(400, messages[self.path])
                return
            with sweep.navigation_lock:
                sweep.focus = focus
            self.reply("application/json", json.dumps(focus).encode())

        def listen_json(self, status, value):
            self.reply("application/json", json.dumps(value, allow_nan=False).encode(), status)

        def reply(self, kind, body, status=200):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

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
