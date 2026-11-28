import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from receiver import Receiver


class AntennaTests(unittest.TestCase):
    def setUp(self):
        self.receiver = Receiver.__new__(Receiver)
        self.receiver.radio = Mock()
        self.receiver.uhd = SimpleNamespace(types=SimpleNamespace(TuneRequest=float))
        self.receiver.channel = 0
        self.receiver.stream = Mock()
        self.receiver.stream_rate = 14e6
        self.receiver.bandwidth = self.receiver.master_clock = 56e6
        self.receiver.antennas = {"A:TX/RX": (0, "TX/RX"), "A:RX2": (0, "RX2"), "B:RX2": (1, "RX2")}
        radio = self.receiver.radio
        radio.get_rx_bandwidth.return_value = 56e6
        radio.get_master_clock_rate.return_value = 56e6
        radio.get_rx_rate.return_value = 14e6
        radio.get_rx_antenna.return_value = "RX2"

    def test_switch_within_one_rf_chain_keeps_the_streamer(self):
        stream = self.receiver.stream
        self.receiver.select_antenna("A:RX2", 40)
        self.receiver.radio.set_rx_antenna.assert_called_once_with("RX2", 0)
        self.assertIs(self.receiver.stream, stream)

    def test_switch_between_rf_chains_reopens_single_channel_and_preserves_settings(self):
        stream = Mock()

        def open_stream(channel):
            self.assertEqual(channel, 1)
            self.assertIsNone(self.receiver.stream)
            return stream

        self.receiver.open_stream = Mock(side_effect=open_stream)
        self.receiver.select_antenna("B:RX2", 40)
        radio = self.receiver.radio
        radio.set_rx_rate.assert_called_once_with(14e6, 1)
        radio.set_rx_antenna.assert_called_once_with("RX2", 1)
        radio.set_rx_gain.assert_called_once_with(40, 1)
        self.assertIs(self.receiver.stream, stream)
        self.receiver.tune(93.7e6)
        radio.set_rx_freq.assert_called_once_with(93.7e6, 1)
        self.receiver.get_gain()
        radio.get_rx_gain.assert_called_once_with(1)
