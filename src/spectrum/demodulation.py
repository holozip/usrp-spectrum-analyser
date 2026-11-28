"""Streaming channel filtering, analog demodulation and complex IQ output."""
from fractions import Fraction
import math

import numpy as np

from rds import OFFSET_C_PRIME, OFFSETS, RDS_RATE, SAMPLES_PER_BIT, RdsDecoder, checkword

try:
    from scipy import signal
except ImportError:
    signal = None


AUDIO_RATE = 48_000
BROADCAST_FM_MODES = ("wfm", "wfm_stereo")
CHANNEL_MODES = {
    "usb": {"default": 2_700, "min": 500, "max": 4_000},
    "lsb": {"default": 2_700, "min": 500, "max": 4_000},
    "am": {"default": 10_000, "min": 3_000, "max": 20_000},
    "iq": {"default": 25_000, "min": 1_000, "max": 40_000},
    "nfm": {"default": 12_500, "min": 8_000, "max": 30_000},
    "wfm": {"default": 200_000, "min": 140_000, "max": 240_000},
    "wfm_stereo": {"default": 220_000, "min": 160_000, "max": 300_000},
}


class Resampler:
    """Causal polyphase FIR with sample phase and history carried across reads."""

    def __init__(self, taps, up, down, dtype):
        self.taps = (taps * up).astype(np.float32)
        self.up = up
        self.down = down
        self.keep = ((len(taps) - 1 + up * down - 1) // (up * down)) * down
        self.history = np.zeros(self.keep, dtype=dtype)

    def process(self, samples):
        start = len(self.history)
        buffer = np.concatenate((self.history, samples))
        first = (start * self.up + self.down - 1) // self.down
        end = (len(buffer) * self.up + self.down - 1) // self.down
        keep = self.keep + len(buffer) % self.down
        self.history = buffer[-keep:].copy() if keep else buffer[:0].copy()
        return signal.upfirdn(self.taps, buffer, self.up, self.down)[first:end]


def lowpass(rate, cutoff, transition):
    size, beta = signal.kaiserord(60, transition / (rate / 2))
    return signal.firwin(size | 1, cutoff, fs=rate, window=("kaiser", beta))


class RdsClock:
    """Use the received pilot for both the 57 kHz carrier and RDS bit clock."""

    def __init__(self):
        self.filter = signal.butter(2, 12, fs=RDS_RATE, output="sos")
        self.filter_state = np.zeros((len(self.filter), 2), dtype=np.complex128)
        self.reference = 0j
        self.locked = False
        self.phase = 0.0
        self.next_sample = 1.0
        self.previous = 0j

    def process(self, samples, pilot):
        pilot, self.filter_state = signal.sosfilt(self.filter, pilot, zi=self.filter_state)
        count = len(pilot)
        threshold = 0.008 if self.locked else 0.012
        phase_change = 0.0
        locked = bool(abs(pilot[-1]) > threshold)
        if locked and self.locked:
            change = np.angle(pilot[-1] * self.reference.conjugate()) / (2 * np.pi)
            # Suppress spurious phase jumps during pilot fades.
            if abs(change) <= 6 * count / RDS_RATE:
                phase_change = change
        self.reference, self.locked = pilot[-1], locked
        grid = np.arange(1, count + 1)
        phases = self.phase + grid * phase_change / count
        self.phase = (self.phase + phase_change) % 1
        rotated = samples * np.exp(-6j * np.pi * phases)
        span = count + phase_change
        # Interpolate onto evenly spaced cycles of the received pilot. The
        # fractional sample and prior endpoint carry through block boundaries.
        positions = np.arange(self.next_sample, span + 1e-9)
        self.next_sample += len(positions) - span
        result = np.interp(positions * count / span, np.arange(count + 1), np.r_[self.previous, rotated])
        self.previous = rotated[-1]
        return result.astype(np.complex64)


class BroadcastFM:
    """Recover broadcast audio and RDS from the FM multiplex before de-emphasis."""

    def __init__(self, rate, deemphasis, stereo, rds_station=None):
        self.stereo = stereo
        self.stereo_locked = False
        self.stereo_blend_percent = 0.0
        self.rotation = 2 * np.pi * 19_000 / rate
        self.phase = 0.0
        self.oscillator = None
        if stereo:
            self.pilot_filter = signal.butter(2, 120, fs=rate, output="sos")
            self.pilot_state = np.zeros((len(self.pilot_filter), 2), dtype=np.complex128)
            blend_pole = np.exp(-1 / (0.045 * rate))
            self.blend_filter = ([1 - blend_pole], [1, -blend_pole])
            self.blend_state = np.zeros(1)
        audio_dtype = np.complex64 if stereo else np.float32
        ratio = Fraction(AUDIO_RATE / rate).limit_denominator(8192)
        self.audio = Resampler(lowpass(rate * ratio.numerator, 16_900, 3_600),
                               ratio.numerator, ratio.denominator, audio_dtype)
        pole = np.exp(-1 / (rate * deemphasis * 1e-6))
        self.deemphasis = ([1 - pole], [1, -pole])
        self.deemphasis_state = np.zeros(1, dtype=audio_dtype)
        self.highpass = signal.butter(1, 20, fs=AUDIO_RATE, btype="highpass")
        self.highpass_state = np.zeros(1, dtype=audio_dtype)
        rds_ratio = Fraction(RDS_RATE / rate).limit_denominator(8192)
        rds_taps = lowpass(rate * rds_ratio.numerator, 2_500, 2_000)
        self.rds_baseband = Resampler(rds_taps, rds_ratio.numerator, rds_ratio.denominator, np.complex64)
        self.rds_pilot = Resampler(rds_taps, rds_ratio.numerator, rds_ratio.denominator, np.complex64)
        self.rds_clock = RdsClock()
        self.rds = RdsDecoder(rds_station)

    def process(self, multiplex):
        count = len(multiplex)
        if self.oscillator is None or len(self.oscillator) != count:
            self.oscillator = np.exp(1j * self.rotation * np.arange(count)).astype(np.complex64)
        carrier = self.oscillator * np.complex64(np.exp(1j * self.phase))
        self.phase = (self.phase + self.rotation * count) % (2 * np.pi)
        down = carrier.conj()
        shifted = multiplex * down
        rds_baseband = self.rds_baseband.process(shifted * down * down)
        rds_pilot = self.rds_pilot.process(shifted)
        step = RDS_RATE // 50
        for start in range(0, len(rds_baseband), step):
            samples = self.rds_clock.process(rds_baseband[start:start + step], rds_pilot[start:start + step])
            self.rds.process(samples, self.rds_clock.locked)
        audio = multiplex
        if self.stereo:
            pilot, self.pilot_state = signal.sosfilt(self.pilot_filter, shifted, zi=self.pilot_state)
            envelope = np.abs(pilot)
            mean_level = float(envelope.mean())
            threshold = 0.011 if self.stereo_locked else 0.018
            self.stereo_locked = bool(mean_level >= threshold and abs(pilot.mean()) >= 0.85 * mean_level)
            blend, self.blend_state = signal.lfilter(*self.blend_filter, np.full(count, float(self.stereo_locked)),
                                                    zi=self.blend_state)
            self.stereo_blend_percent = round(float(blend[-1]) * 100, 1)
            phase_reference = carrier * pilot / np.maximum(envelope, 1e-9)
            # The 38 kHz carrier has positive slope at the pilot zero crossings.
            difference = -2 * (phase_reference * phase_reference).imag * multiplex * blend
            audio = multiplex + 1j * difference
        audio, self.deemphasis_state = signal.lfilter(*self.deemphasis, audio, zi=self.deemphasis_state)
        audio = self.audio.process(audio.astype(np.complex64 if self.stereo else np.float32))
        if len(audio):
            audio, self.highpass_state = signal.lfilter(*self.highpass, audio, zi=self.highpass_state)
        if self.stereo:
            return np.column_stack((audio.real + audio.imag, audio.real - audio.imag))
        return audio

    def snapshot(self):
        # The complex pilot mixer retains half the real signal's amplitude.
        pilot_level_percent = round(200 * float(abs(self.rds_clock.reference)), 1)
        return {"stereo_locked": self.stereo_locked, "pilot_detected": self.rds_clock.locked,
                "pilot_level_percent": pilot_level_percent, "stereo_blend_percent": self.stereo_blend_percent,
                "rds": self.rds.snapshot()}


class ChannelDemodulator:
    def __init__(self, rate, offset, mode, bandwidth, deemphasis, rds_station=None):
        self.mode = mode
        sideband_offset = {"usb": bandwidth / 2, "lsb": -bandwidth / 2}.get(mode, 0)
        self.rotation = -2 * np.pi * (offset + sideband_offset) / rate
        self.phase = 0.0
        self.oscillator = None
        self.coarse = []
        intermediate = rate
        # At high rates the first stages only need to reject aliases near the
        # final channel. Their wide transition bands keep the full-rate FIR short.
        while intermediate >= 8_000_000:
            output_rate = intermediate / 4
            taps = lowpass(intermediate, 0.5 * output_rate, 0.8 * output_rate)
            self.coarse.append(Resampler(taps, 1, 4, np.complex64))
            intermediate = output_rate
        coarse = int(intermediate / 800_000)
        if coarse > 1:
            output_rate = intermediate / coarse
            taps = lowpass(intermediate, 0.40 * output_rate, 0.18 * output_rate)
            self.coarse.append(Resampler(taps, 1, coarse, np.complex64))
            intermediate = output_rate
        wide = mode in BROADCAST_FM_MODES
        channel_down = max(1, int(intermediate / (360_000 if wide else 64_000)))
        channel_rate = intermediate / channel_down
        if sideband_offset:
            # Select the sideband at the lower rate, where a sharp cutoff costs less.
            taps = lowpass(intermediate, 0.35 * channel_rate, 0.21 * channel_rate)
            self.sideband_filter = Resampler(lowpass(channel_rate, bandwidth / 2, 450), 1, 1, np.complex64)
        else:
            taps = lowpass(intermediate, bandwidth / 2, 24_000 if wide else 6_000)
            self.sideband_filter = None
        self.channel = Resampler(taps, 1, channel_down, np.complex64)
        self.sideband_rotation = 2 * np.pi * sideband_offset / channel_rate
        self.sideband_phase = 0.0
        self.scale = channel_rate / (2 * np.pi * (75_000 if wide else 5_000))
        self.previous = np.zeros(1, dtype=np.complex64)
        noise_filter = self.sideband_filter or self.channel
        noise_filter_rate = channel_rate if sideband_offset else intermediate
        self.noise_bandwidth = float(noise_filter_rate * np.sum(noise_filter.taps ** 2))
        self.noise_rate = intermediate
        guard = bandwidth / 2 + max(3_000, bandwidth / 5)
        edge = min(guard + max(2_500, bandwidth / 2), 0.3 * intermediate)
        mixed_center = offset + sideband_offset
        self.noise_bands = ((max(-edge, -rate / 2 - mixed_center), -guard),
                            (guard, min(edge, rate / 2 - mixed_center)))
        self.noise_power = None
        self.snr_db = None
        self.noise_samples = 0
        self.broadcast = BroadcastFM(channel_rate, deemphasis, mode == "wfm_stereo", rds_station) if wide else None
        if wide:
            return
        ratio = Fraction(AUDIO_RATE / channel_rate).limit_denominator(8192)
        cutoff = {"am": 9_000, "iq": min(20_500, bandwidth / 2 + 1_000)}.get(mode, 4_000)
        self.output = Resampler(lowpass(channel_rate * ratio.numerator, cutoff, 2_000),
                                ratio.numerator, ratio.denominator, np.complex64 if mode == "iq" else np.float32)
        highpass_hz = 180 if mode in ("nfm", "usb", "lsb") else 35
        self.highpass = signal.butter(1, highpass_hz, fs=AUDIO_RATE, btype="highpass")
        self.highpass_state = np.zeros(1)
        pole = np.exp(-1 / (AUDIO_RATE * deemphasis * 1e-6))
        self.deemphasis = ([1 - pole], [1, -pole])
        self.deemphasis_state = np.zeros(1)

    def measure_snr(self, mixed, total_power):
        self.noise_samples -= len(mixed)
        if self.noise_samples > 0 or len(mixed) < 1024:
            return
        self.noise_samples = round(self.noise_rate / 10)
        frequency, density = signal.periodogram(mixed[-32768:], fs=self.noise_rate, window="hann",
                                               detrend=False, return_onesided=False)
        estimates = []
        for low, high in self.noise_bands:
            bins = density[(frequency >= low) & (frequency <= high)]
            if len(bins) >= 12:
                # The median limits narrow interferers. A noise-only periodogram's
                # exponential distribution has median equal to its mean times ln(2).
                estimates.append(float(np.median(bins)) / math.log(2))
        self.snr_db = None
        if not estimates:
            return
        noise = min(estimates) * self.noise_bandwidth
        if not math.isfinite(noise) or noise <= 0:
            return
        self.noise_power = noise if self.noise_power is None else 0.65 * self.noise_power + 0.35 * noise
        if math.isfinite(total_power) and total_power > self.noise_power:
            self.snr_db = 10 * math.log10((total_power - self.noise_power) / self.noise_power)

    def process(self, iq):
        count = len(iq)
        if self.oscillator is None or len(self.oscillator) != count:
            self.oscillator = np.exp(1j * self.rotation * np.arange(count)).astype(np.complex64)
        mixed = iq * self.oscillator
        mixed *= np.complex64(np.exp(1j * self.phase))
        self.phase = (self.phase + self.rotation * count) % (2 * np.pi)
        for stage in self.coarse:
            mixed = stage.process(mixed)
        channel = self.channel.process(mixed)
        if self.sideband_filter is not None:
            channel = self.sideband_filter.process(channel)
        if not len(channel):
            return np.empty(0, dtype=np.float32), -160.0
        total_power = float(np.mean(np.abs(channel) ** 2))
        power = 10 * math.log10(max(1e-16, total_power))
        self.measure_snr(mixed, total_power)
        if self.mode == "iq":
            return self.output.process(channel), power
        if self.mode == "nfm" or self.broadcast:
            previous = np.concatenate((self.previous, channel[:-1]))
            self.previous = channel[-1:].copy()
            baseband = np.angle(channel * previous.conj()) * np.float32(self.scale)
            if self.broadcast:
                return self.broadcast.process(baseband), power
        elif self.mode == "am":
            baseband = np.abs(channel) * np.float32(5)
        else:
            phase = self.sideband_phase + self.sideband_rotation * np.arange(len(channel))
            baseband = (channel * np.exp(1j * phase).astype(np.complex64)).real * np.float32(5)
            self.sideband_phase = (self.sideband_phase + self.sideband_rotation * len(channel)) % (2 * np.pi)
        audio = self.output.process(baseband)
        if len(audio):
            audio, self.highpass_state = signal.lfilter(*self.highpass, audio, zi=self.highpass_state)
            if self.mode == "nfm":
                audio, self.deemphasis_state = signal.lfilter(*self.deemphasis, audio, zi=self.deemphasis_state)
        return audio, power

    def snapshot(self):
        return self.broadcast.snapshot() if self.broadcast else None


# RF offset, audio tone and peak deviation for the continuous demo stations.
DEMO_FM = ((98_100_000, 1_000, 42_000), (433_920_000, 700, 2_500))
DEMO_STEREO_HZ = 98_600_000


def demo_rds_symbols():
    pi = 0xCAFE
    station = b"DEMO FM "
    text = b"Stereo and RDS test signal\r"
    text += b" " * (-len(text) % 4)
    groups = []
    flags = (1 << 10) | (10 << 5)  # TP and European PTY Pop music
    af_words = (0xE36F, 0x6A84, 0xCDCD, 0xCDCD)
    for segment, frequencies in enumerate(af_words):
        groups.append((pi, flags | segment, frequencies, int.from_bytes(station[2 * segment:2 * segment + 2], "big")))
    for segment in range(len(text) // 4):
        first, second = text[4 * segment:4 * segment + 2], text[4 * segment + 2:4 * segment + 4]
        groups.append((pi, 0x2000 | flags | segment, int.from_bytes(first, "big"), int.from_bytes(second, "big")))
    words = []
    for group in groups:
        for index, block in enumerate(group):
            offset = OFFSET_C_PRIME if index == 2 and group[1] & 0x800 else OFFSETS[index]
            shifted = block << 10
            words.append(shifted | (checkword(shifted) ^ offset))
    data = [(word >> bit) & 1 for word in words for bit in range(25, -1, -1)]
    symbols = (-1.0) ** (np.cumsum(data) % 2)
    return symbols if symbols[-1] == 1 else np.r_[symbols, -symbols]


class DemoStereo:
    def __init__(self, center, rate):
        self.offset = DEMO_STEREO_HZ - center
        self.rate = rate
        self.phase = 0.0
        self.symbols = demo_rds_symbols() if abs(self.offset) + 130_000 < rate / 2 else None

    def add(self, samples, time):
        if self.symbols is None:
            return
        left = 0.54 * np.sin(2 * np.pi * 1_000 * time)
        right = 0.48 * np.sin(2 * np.pi * 1_700 * time)
        pilot = 2 * np.pi * 19_000 * time + 0.4
        bit = time * (RDS_RATE / SAMPLES_PER_BIT) + 0.23
        symbols = self.symbols[bit.astype(np.int64) % len(self.symbols)]
        rds = 0.048 * symbols * np.sin(2 * np.pi * bit) * np.cos(3 * pilot + 0.7)
        multiplex = 0.5 * (left + right) + 0.5 * (left - right) * np.sin(2 * pilot) + 0.09 * np.sin(pilot) + rds
        phase = self.phase + 2 * np.pi / self.rate * np.cumsum(self.offset + 75_000 * multiplex)
        self.phase = phase[-1] % (2 * np.pi)
        samples += 0.04 * np.exp(1j * phase)


def demo_iq(center, rate, start, count, rng, stereo=None):
    time = np.arange(start, start + count) / rate
    samples = (rng.standard_normal(count) + 1j * rng.standard_normal(count)) * 0.0005
    for frequency, tone, deviation in DEMO_FM:
        offset = frequency - center
        if abs(offset) + deviation + tone < rate / 2:
            phase = 2 * np.pi * offset * time - deviation / tone * np.cos(2 * np.pi * tone * time)
            samples += 0.035 * np.exp(1j * phase)
    if stereo is not None:
        stereo.add(samples, time)
    return samples.astype(np.complex64)
