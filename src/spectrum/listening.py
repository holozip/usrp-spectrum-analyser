"""Channel settings and bounded IQ/PCM delivery from the current capture."""
from collections import deque
from dataclasses import asdict, dataclass, replace
from queue import Empty, Full, Queue
import struct
import threading

import numpy as np

from demodulation import AUDIO_RATE, BROADCAST_FM_MODES, CHANNEL_MODES, ChannelDemodulator, signal
from rds import RdsStation


@dataclass(frozen=True)
class Tune:
    frequency_hz: int
    mode: str
    filter_hz: int
    squelch_dbfs: int | None
    deemphasis_us: int

    @property
    def audio_channels(self):
        return 0 if self.mode == "iq" else 2 if self.mode == "wfm_stereo" else 1

    @property
    def sample_size(self):
        return 2 * self.audio_channels if self.audio_channels else 8

    @property
    def edges(self):
        if self.mode == "usb":
            return self.frequency_hz, self.frequency_hz + self.filter_hz
        if self.mode == "lsb":
            return self.frequency_hz - self.filter_hz, self.frequency_hz
        return self.frequency_hz - self.filter_hz / 2, self.frequency_hz + self.filter_hz / 2


@dataclass(frozen=True)
class IQCapture:
    center_hz: float
    rate_hz: float
    window: tuple
    focus: tuple
    antenna: str | None = None

    def contains(self, request):
        low, high = request.edges
        return self.window[0] <= low and high <= self.window[1]


class CaptureUnavailable(ValueError):
    pass


def bookmark_tune(bookmark):
    label = bookmark["modulation"].strip().lower()
    frequency = round(bookmark["frequency_hz"])
    if label in ("fm-n", "narrow fm"):
        label = "nfm"
    elif label in ("fm-w", "wide fm", "broadcast fm", "broadcast fm mono"):
        label = "wfm"
    elif label in ("fm stereo", "wfm stereo", "broadcast fm stereo", "wfm_st"):
        label = "wfm_stereo"
    mode = label if label in CHANNEL_MODES else "wfm" if 87.5e6 <= frequency <= 108e6 else "nfm"
    preset = CHANNEL_MODES[mode]
    width = round(bookmark["bandwidth_hz"] or 0)
    if not preset["min"] <= width <= preset["max"]:
        width = preset["default"]
    return Tune(frequency, mode, width, None, 50)


def validate_tune(value, low, high):
    if not isinstance(value, dict) or not {"frequency_hz", "mode"} <= value.keys() or value.keys() - {
        "frequency_hz", "mode", "filter_hz", "squelch_dbfs", "deemphasis_us",
    }:
        raise ValueError("Expected a channel frequency and mode, with optional filter, squelch and de-emphasis")
    mode = value["mode"]
    if not isinstance(mode, str) or mode not in CHANNEL_MODES:
        raise ValueError("Choose USB, LSB, NFM, WFM, WFM Stereo, AM or IQ")
    preset = CHANNEL_MODES[mode]
    width = value.get("filter_hz", preset["default"])
    if type(width) is not int or not preset["min"] <= width <= preset["max"]:
        raise ValueError(f"Choose a filter width from {preset['min']} to {preset['max']} Hz for {mode.upper()}")
    frequency = value["frequency_hz"]
    if type(frequency) is not int or not low <= frequency <= high:
        raise ValueError("Set a channel frequency within 70 MHz–6 GHz")
    squelch = value.get("squelch_dbfs")
    if squelch is not None and (type(squelch) is not int or not -140 <= squelch <= 0):
        raise ValueError("Squelch must be off (null) or an integer from −140 to 0 dBFS")
    deemphasis = value.get("deemphasis_us", 50)
    if type(deemphasis) is not int or deemphasis not in (50, 75):
        raise ValueError("FM de-emphasis must be 50 or 75 µs")
    request = Tune(frequency, mode, width, squelch, deemphasis)
    if request.edges[0] < low or request.edges[1] > high:
        raise ValueError("The entire channel must fit within 70 MHz–6 GHz")
    return request


class Listening:
    def __init__(self):
        self.condition = threading.Condition()
        self.request = None
        self.capture = None
        self.revision = 0
        self.epoch = 0
        self.stopped_epoch = 0
        self.sequence = 0
        self.frames = deque(maxlen=6)
        self.applied_revision = None
        self.power = None
        self.snr = None
        self.squelched = False
        self.broadcast = None
        self.rds_station = None
        self.error = None

    def snapshot(self):
        with self.condition:
            capture = self.capture
            return {"available": signal is not None, "settings": asdict(self.request) if self.request else None,
                    "capture": asdict(capture) if capture else None, "window": capture.window if capture else None,
                    "revision": self.revision, "applied_revision": self.applied_revision,
                    "power_dbfs": self.power, "snr_db": self.snr,
                    "squelched": self.squelched, "audio_rate_hz": AUDIO_RATE,
                    "audio_channels": self.request.audio_channels if self.request else 0,
                    "audio_epoch": self.epoch, "broadcast": self.broadcast, "error": self.error}

    def configure(self, request, focus):
        with self.condition:
            if request is not None:
                if self.capture is None or self.capture.focus != focus:
                    raise CaptureUnavailable("Zoom the spectrum to a continuous capture before listening.")
                if not self.capture.contains(request):
                    raise CaptureUnavailable("The entire channel must fit in the current IQ capture.")
            self._set_request(request)
            return self.snapshot()

    def recall(self, preset, low, high):
        with self.condition:
            start, stop = preset.edges
            if self.request is not None and low <= start and stop <= high:
                # The bookmark has already requested its survey focus. The tap
                # will wait for samples that cover the newly selected channel.
                self._set_request(replace(preset, squelch_dbfs=self.request.squelch_dbfs,
                                          deemphasis_us=self.request.deemphasis_us))
            return self.snapshot()

    def _set_request(self, request):
        if request == self.request and (request is None or not self.error):
            return
        changed_format = (request is not None and self.request is not None
                          and request.sample_size != self.request.sample_size)
        broadcast = request is not None and request.mode in BROADCAST_FM_MODES
        same_station = (broadcast and self.request is not None and self.request.mode in BROADCAST_FM_MODES
                        and request.frequency_hz == self.request.frequency_hz)
        if not same_station:
            self.rds_station = RdsStation(request.frequency_hz) if broadcast else None
        self.request = request
        self.revision += 1
        self.interrupt(keep_rds=same_station)
        if request is None or changed_format:
            self.stopped_epoch = self.epoch

    def set_capture(self, capture):
        with self.condition:
            self.capture = capture
            if self.request is not None:
                self.interrupt()

    def interrupt(self, keep_rds=True):
        with self.condition:
            self.epoch += 1
            self.applied_revision = self.power = self.snr = None
            self.squelched = False
            if keep_rds:
                self.mark_rds_unlocked()
            else:
                self.broadcast = None
            self.error = None
            self.frames.clear()
            self.append(b"")

    def mark_rds_unlocked(self):
        rds = self.broadcast.get("rds") if self.broadcast else None
        self.broadcast = {"stereo_locked": False, "rds": {**rds, "locked": False, "block_quality": None}} \
            if rds and rds.get("pi") else None

    def append(self, payload):
        self.sequence += 1
        sample_size = self.request.sample_size if self.request else 2
        packet = struct.pack("<III", self.epoch, self.sequence, len(payload) // sample_size) + payload
        self.frames.append((self.sequence, packet))
        self.condition.notify_all()

    def read_settings(self, for_audio=False):
        with self.condition:
            request = None if for_audio and self.error else self.request
            return request, self.capture, self.revision, self.epoch

    def read_rds_station(self, revision, epoch):
        with self.condition:
            if revision == self.revision and epoch == self.epoch:
                return self.rds_station
            return None

    def publish(self, revision, epoch, pcm, power, squelched, broadcast=None, snr=None):
        with self.condition:
            if revision == self.revision and epoch == self.epoch:
                self.applied_revision = revision
                self.power = round(power, 1)
                self.snr = round(snr, 1) if snr is not None else None
                self.squelched = squelched
                self.broadcast = broadcast
                self.append(pcm)

    def mark_gap(self, revision, epoch):
        with self.condition:
            if revision == self.revision and epoch == self.epoch:
                self.frames.clear()
                self.power = self.snr = None
                self.mark_rds_unlocked()
                self.append(b"")

    def fail(self, revision, epoch, error):
        with self.condition:
            if revision == self.revision and epoch == self.epoch:
                self.interrupt()
                self.error = str(error)

    def stream(self, write, session, sample_size):
        cursor = None
        while True:
            with self.condition:
                if not self.request or session < self.stopped_epoch or self.request.sample_size != sample_size:
                    return
                if not self.frames or (cursor is not None and self.frames[-1][0] <= cursor):
                    self.condition.wait(timeout=1)
                    if not self.request or session < self.stopped_epoch or self.request.sample_size != sample_size:
                        return
                if self.frames and (cursor is None or self.frames[-1][0] > cursor):
                    first, _ = self.frames[0]
                    cursor, packet = (self.frames[-1] if cursor is None or cursor < first - 1
                                      else self.frames[cursor + 1 - first])
                else:
                    # Heartbeat keeps a silent or unavailable source observable and checks for disconnects.
                    cursor = self.sequence
                    packet = struct.pack("<III", self.epoch, cursor, 0)
            write(packet)


class ChannelTap:
    """Copy IQ for the audio worker without blocking the spectrum capture loop."""

    def __init__(self, listening, capture):
        self.listening = listening
        self.capture = capture
        self.count = round(capture.rate_hz * 0.02)
        self.queue = Queue(maxsize=4)
        self.stopping = threading.Event()
        self.thread = None
        self.serial = self.filled = 0
        self.version = None
        self.buffer = None

    def __enter__(self):
        self.listening.set_capture(self.capture)
        if signal is not None:
            self.thread = threading.Thread(target=self.run, daemon=True, name="channel-dsp")
            self.thread.start()
        return self

    def __exit__(self, *_args):
        self.listening.set_capture(None)
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()

    def push(self, samples):
        request, capture, revision, epoch = self.listening.read_settings(for_audio=True)
        if request is None or capture is not self.capture or not capture.contains(request):
            return
        version = (revision, epoch)
        if version != self.version:
            self.version = version
            self.filled = 0
        while len(samples):
            if self.filled == 0:
                self.buffer = np.empty(self.count, dtype=np.complex64)
            count = min(len(samples), self.count - self.filled)
            self.buffer[self.filled:self.filled + count] = samples[:count]
            self.filled += count
            samples = samples[count:]
            if self.filled == self.count:
                self.serial += 1
                block = (self.serial, version, self.buffer)
                try:
                    self.queue.put_nowait(block)
                except Full:
                    # Drop audio work under load. RX and FFT keep running.
                    pass
                self.filled = 0

    def gap(self):
        if self.listening.read_settings()[0] is not None:
            self.listening.interrupt()

    def run(self):
        previous = 0
        version = None
        dsp = None
        while not self.stopping.is_set():
            try:
                serial, block_version, samples = self.queue.get(timeout=0.04)
            except Empty:
                continue
            request, capture, revision, epoch = self.listening.read_settings(for_audio=True)
            if request is None or capture is not self.capture or block_version != (revision, epoch):
                continue
            missed = version == block_version and serial != previous + 1
            try:
                if missed:
                    self.listening.mark_gap(revision, epoch)
                if missed or version != block_version:
                    dsp = ChannelDemodulator(capture.rate_hz, request.frequency_hz - capture.center_hz,
                                             request.mode, request.filter_hz, request.deemphasis_us,
                                             rds_station=self.listening.read_rds_station(revision, epoch))
                    version = block_version
                output, power = dsp.process(samples)
            except Exception as error:
                self.listening.fail(revision, epoch, error)
                continue
            previous = serial
            if request.mode == "iq":
                self.listening.publish(revision, epoch, output.astype("<c8", copy=False).tobytes(),
                                       power, False, snr=dsp.snr_db)
            else:
                closed = request.squelch_dbfs is not None and power < request.squelch_dbfs
                pcm = (np.zeros(output.shape, dtype="<i2") if closed
                       else (np.clip(output, -1, 1) * 32767).astype("<i2"))
                self.listening.publish(revision, epoch, pcm.tobytes(), power, closed, dsp.snapshot(), snr=dsp.snr_db)
