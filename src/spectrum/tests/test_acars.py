import pathlib
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from acars import AcarsDecoder, acars_crc
from demodulation import AUDIO_RATE, signal
from listening import ACARS_LOG_LIMIT, ChannelTap, IQCapture, Listening, bookmark_tune


def block_audio(text=b"", terminator=3, bad_crc=False):
    body = b"2.N123AB\x15Q01" + (b"\x02" + text if text else b"") + bytes([terminator])
    parity = lambda data: bytes(byte + (128 if byte.bit_count() % 2 == 0 else 0) for byte in data)
    data = parity(body)
    crc = (acars_crc(data) ^ int(bad_crc)).to_bytes(2, "little")
    wire = b"\xff" * 16 + parity(b"+*\x16\x16\x01") + data + crc + b"\x7f"
    bits = np.unpackbits(np.frombuffer(wire, dtype=np.uint8), bitorder="little")
    changes = np.r_[1, bits[:-1]] != bits
    cycles = 1.0 - 0.5 * changes
    phase = np.r_[0, np.cumsum(cycles[:-1])]
    width = AUDIO_RATE // 2400
    envelope = np.sin(2 * np.pi * (phase[:, None] + cycles[:, None] * np.arange(width) / width))
    return np.r_[np.zeros(313), envelope.ravel(), np.zeros(700)]


def decode(audio):
    decoder = AcarsDecoder(AUDIO_RATE)
    return [message for start in range(0, len(audio), 513)
            for message in decoder.process(audio[start:start + 513])]


class AcarsTests(unittest.TestCase):
    def test_crc_matches_arinc_example(self):
        self.assertEqual(acars_crc(b"\xcb\x37"), 0x6b3e)

    def test_streaming_blocks_validate_crc_and_preserve_text(self):
        audio = np.concatenate((block_audio(b"BAD CHECKSUM", bad_crc=True),
                                -block_audio(b"LINE ONE\r\nLINE TWO", terminator=0x17),
                                block_audio()))
        messages = decode(audio)
        self.assertEqual(messages, [
            {"mode": "2", "aircraft": "N123AB", "label": "Q0", "block_id": "1",
             "text": "LINE ONE\nLINE TWO", "more": True},
            {"mode": "2", "aircraft": "N123AB", "label": "Q0", "block_id": "1",
             "text": "", "more": False},
        ])
        self.assertEqual(decode(np.random.default_rng(13).normal(0, 1, 100_000)), [])

    @unittest.skipIf(signal is None, "Channel DSP requires SciPy")
    def test_channel_tap_decodes_am_iq_with_symbol_clock_offset(self):
        frequency = 131_825_000
        rate = 192_000
        center = frequency - 23_000
        focus = (center-50_000, center+50_000)
        capture = IQCapture(center, rate, focus, focus)
        request = bookmark_tune({"modulation": "ACARS", "frequency_hz": frequency, "bandwidth_hz": 10_000})
        text = b"ACARS CHANNEL TEST " * 11
        audio = block_audio(text)
        # Sample at a 200 ppm timing offset, with a shifted RF carrier and noise.
        positions = np.arange(len(audio) * 4) * (1.0002 * AUDIO_RATE / rate)
        envelope = 0.04 * (1 + 0.75 * np.interp(positions, np.arange(len(audio)), audio))
        carrier = np.exp(2j * np.pi * 23_460 * np.arange(len(positions)) / rate)
        rng = np.random.default_rng(287)
        iq = (envelope * carrier + 0.005 * (rng.normal(size=len(positions))
                                            + 1j * rng.normal(size=len(positions)))).astype(np.complex64)
        listening = Listening()
        with ChannelTap(listening, capture) as tap:
            state = listening.configure(request, focus)
            self.assertEqual(state["audio_channels"], 0)
            deadline = time.monotonic() + 3
            for start in range(0, len(iq), tap.count):
                while tap.queue.full():
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.001)
                tap.push(iq[start:start + tap.count])
            while not listening.acars_log()["messages"]:
                self.assertLess(time.monotonic(), deadline, listening.snapshot()["error"])
                time.sleep(0.001)

        message, = listening.acars_log()["messages"]
        self.assertEqual(message["frequency_hz"], frequency)
        self.assertEqual(message["text"], text.decode())
        self.assertEqual(listening.snapshot()["acars_revision"], 1)

    def test_log_keeps_latest_messages_and_rejects_stale_results(self):
        listening = Listening()
        frequency = 131_825_000
        focus = (frequency-50_000, frequency+50_000)
        listening.set_capture(IQCapture(frequency, 192_000, focus, focus))
        request = bookmark_tune({"modulation": "ACARS", "frequency_hz": frequency, "bandwidth_hz": 10_000})
        state = listening.configure(request, focus)
        self.assertEqual(state["settings"]["mode"], "acars")
        arguments = (state["revision"], state["audio_epoch"], b"", -60, False)
        listening.publish(*arguments, acars=[{"text": str(i)} for i in range(ACARS_LOG_LIMIT + 2)])
        listening.configure(None, focus)
        listening.publish(*arguments, acars=[{"text": "Stale"}])
        messages = listening.acars_log()["messages"]
        self.assertEqual(len(messages), ACARS_LOG_LIMIT)
        self.assertEqual(messages[0]["id"], ACARS_LOG_LIMIT + 2)
        self.assertEqual(messages[-1]["id"], 3)


if __name__ == "__main__":
    unittest.main()
