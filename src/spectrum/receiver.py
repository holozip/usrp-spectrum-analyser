"""UHD and simulated receivers for spectrum acquisition."""
import time

import numpy as np

from demodulation import DEMO_FM, DEMO_STEREO_HZ
from sweep_plan import FFT_SIZE

MAX_RATE = 56
DEFAULT_AVERAGES = 8
MAX_AVERAGES = 32
RX_PORTS = ("TX/RX", "RX2")
RX_ANTENNAS = tuple(f"{side}:{port}" for side in "AB" for port in RX_PORTS)


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
