"""Background spectrum acquisition, trace publication and channel sample taps."""
import threading
import time
from collections import defaultdict, deque

import numpy as np

from demodulation import DemoStereo, demo_iq
from fpga_sweep import BandSweep
from listening import ChannelTap, IQCapture, Listening
from receiver import MAX_RATE, Demo, Receiver, spectrum_power
from sweep_plan import (
    BINS_PER_OVERVIEW, DETAIL_WIDTH, FFT_SIZE, HIGH, LOW,
    captures, continuous_plan, offset_hop_plan, scan_range,
)

NARROW_RATE = MAX_RATE * 1e6 / 4
DEMO_CAPTURE_RATE = 2.4e6
PEAK_BUCKET_SECONDS = 0.2
CONTINUOUS_UPDATE_SECONDS = 0.04


def minimum_trace(first, second):
    # A missing observation must not overwrite a measured bin.
    both = np.isfinite(first) & np.isfinite(second)
    return np.where(both, np.minimum(first, second), np.fmax(first, second))


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
