"""Local, receive-only B210 spectrum dashboard."""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

LOW, HIGH = 70e6, 6e9
MAX_RATE = 56
AVERAGES = 8
DISPLAY_BINS = 4096
PAGE = Path(__file__).with_name("index.html")


def fft_size(rate):
    # Keep FFT bins at least as fine as the original 40 MS/s sweep.
    return max(2048, 256 * int(np.ceil(rate / 5e6)))


def captures(rate, bandwidth):
    # Keep clear of both filter edges and discard the immediate LO/DC region.
    edge = 0.35 * min(rate, bandwidth)
    size = fft_size(rate)
    dc_gap = 0.01 * rate
    offsets = np.fft.fftshift(np.fft.fftfreq(size, 1 / rate))
    usable = (np.abs(offsets) <= edge) & (np.abs(offsets) >= dc_gap)
    # Put the first and last display bins inside the retained FFT band.
    margin = edge - rate / size
    # The neighboring hop must cover the entire DC gap, including its far end.
    step = margin - dc_gap
    count = int(np.ceil((HIGH - LOW - 2 * margin) / step)) + 1
    return np.linspace(LOW + margin, HIGH - margin, count), offsets, usable


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
        self.size = fft_size(self.rate)
        self.window = np.hanning(self.size)
        self.window_sum = self.window.sum()
        self.command.num_samps = self.size * AVERAGES
        self.command.stream_now = True
        self.samples = np.empty((1, self.command.num_samps), dtype=np.complex64)

    def measure(self, center, offsets):
        self.radio.set_rx_freq(self.uhd.types.TuneRequest(float(center)), 0)
        actual_center = self.radio.get_rx_freq(0)
        self.stream.issue_stream_cmd(self.command)
        received = 0
        while received < self.command.num_samps:
            count = self.stream.recv(self.samples[:, received:], self.metadata, 0.25)
            if not count or self.metadata.error_code != self.uhd.types.RXMetadataErrorCode.none:
                raise RuntimeError(f"RX at {center / 1e6:.2f} MHz: {self.metadata.strerror()}")
            received += count
        blocks = self.samples.reshape(AVERAGES, self.size)
        fft = np.fft.fftshift(np.fft.fft((blocks - blocks.mean(axis=1, keepdims=True)) * self.window), axes=1)
        power = np.mean(np.abs(fft / self.window_sum) ** 2, axis=0)
        return actual_center + offsets, power


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
                                     (2.44e9, 11e6, -48), (5.19e9, 14e6, -56)]:
            power += 10 ** (level / 10) * np.exp(-0.5 * ((frequency - signal) / width) ** 2)
        return frequency, power


class Sweep:
    def __init__(self, args):
        self.args = args
        self.snapshot = dict(demo=args.demo, trace=[], number=0, progress=0, error=None,
                             low=LOW, high=HIGH, seconds=None)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        receiver = None
        while True:
            try:
                if receiver is None:
                    receiver = Demo(self.args.rate * 1e6) if self.args.demo else Receiver(self.args)
                self.scan(receiver)
            except Exception as error:
                self.snapshot = dict(self.snapshot, error=str(error), progress=0)
                receiver = None
                time.sleep(3)

    def scan(self, receiver):
        centers, offsets, usable = captures(receiver.rate, receiver.bandwidth)
        self.snapshot = dict(self.snapshot, progress=0)
        started = time.perf_counter()
        bins = np.full(DISPLAY_BINS, -np.inf)
        for index, center in enumerate(centers):
            frequency, power = receiver.measure(center, offsets)
            mask = usable & (frequency >= LOW) & (frequency <= HIGH) & np.isfinite(power)
            indices = ((frequency[mask] - LOW) * DISPLAY_BINS / (HIGH - LOW)).astype(int)
            np.maximum.at(bins, np.minimum(indices, DISPLAY_BINS - 1), power[mask])
            self.snapshot = dict(self.snapshot, progress=(index + 1) / len(centers), error=None)
        measured = np.isfinite(bins)
        bins[measured] = 10 * np.log10(np.maximum(1e-16, bins[measured]))
        trace = [round(float(value), 1) if present else None for value, present in zip(bins, measured)]
        self.snapshot = dict(self.snapshot, trace=trace, number=self.snapshot["number"] + 1,
                             seconds=round(time.perf_counter() - started, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="use simulated RF data")
    parser.add_argument("--device", default="type=b200,product=B210,num_recv_frames=128", help="UHD device arguments")
    parser.add_argument("--rate", type=float, default=MAX_RATE, help="receive rate in MS/s (default: %(default)s)")
    parser.add_argument("--gain", type=float, default=35, help="receive gain in dB")
    parser.add_argument("--antenna", choices=["RX2", "TX/RX"], default="RX2")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 4 <= args.rate <= MAX_RATE:
        parser.error(f"--rate must be between 4 and {MAX_RATE} MS/s")
    if not 0 <= args.gain <= 76:
        parser.error("--gain must be between 0 and 76 dB")
    page = PAGE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                kind, body = "text/html; charset=utf-8", page
            elif self.path == "/api/sweep":
                kind, body = "application/json", json.dumps(sweep.snapshot, allow_nan=False).encode()
            else:
                self.send_error(404)
                return
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
