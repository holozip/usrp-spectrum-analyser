import pathlib
import struct
import sys
import unittest

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from demodulation import AUDIO_RATE, DEMO_FM, DEMO_STEREO_HZ, BroadcastFM, ChannelDemodulator, DemoStereo, demo_iq, signal
from listening import CaptureUnavailable, IQCapture, Listening, bookmark_tune, validate_tune
from rds import (BIT_RATE, BIT_WEIGHTS, OFFSET_C_PRIME, OFFSETS, RDS_RATE, SAMPLES_PER_BIT,
                 RdsAlternativeFrequencies, RdsDecoder, RdsGroups, RdsText, checkword, recover_word)


@unittest.skipIf(signal is None, "FM requires SciPy")
class ChannelDspTests(unittest.TestCase):
    def test_snr_uses_the_channel_filter_and_nearby_noise(self):
        rate = 1_200_000
        offset = 240_000
        dsp = ChannelDemodulator(rate, offset, "nfm", 30_000, 50)
        time = np.arange(rate // 2) / rate
        rng = np.random.default_rng(75)
        noise_std = 0.01
        noise = noise_std * (rng.standard_normal(len(time)) + 1j * rng.standard_normal(len(time)))
        in_band_noise = 2 * noise_std ** 2 * dsp.noise_bandwidth / rate
        strength = np.sqrt(in_band_noise * 10 ** (12 / 10))
        carrier = strength * np.exp(2j * np.pi * offset * time)
        # Leave only one clean adjacent reference band.
        adjacent = 0.1 * np.exp(2j * np.pi * ((offset + 23_000) * time + 13_000 * time ** 2))
        for block in np.array_split(noise + carrier + adjacent, 25):
            dsp.process(block.astype(np.complex64))
        self.assertAlmostEqual(dsp.snr_db, 12, delta=1.5)

    def test_broadcast_stereo_separates_channels_and_decodes_rds(self):
        rate = 1_200_000
        center = DEMO_STEREO_HZ - 123_000
        source = DemoStereo(center, rate)
        rng = np.random.default_rng(135)
        stereo = ChannelDemodulator(rate, 123_000, "wfm_stereo", 220_000, 50)
        mono = ChannelDemodulator(rate, 123_000, "wfm", 200_000, 50)
        audio = []
        count = rate // 50
        for start in range(0, rate * 2, count):
            iq = demo_iq(center, rate, start, count, rng, source)
            audio.append(stereo.process(iq)[0])
            mono.process(iq)
        left, right = np.concatenate(audio)[AUDIO_RATE:].T
        left_fft = np.abs(np.fft.rfft(left * np.hanning(len(left))))
        right_fft = np.abs(np.fft.rfft(right * np.hanning(len(right))))
        self.assertGreater(left_fft[1000], 30 * left_fft[1700])
        self.assertGreater(right_fft[1700], 30 * right_fft[1000])
        self.assertIs(stereo.snapshot()["stereo_locked"], True)
        for dsp in (stereo, mono):
            broadcast = dsp.snapshot()
            self.assertTrue(broadcast["pilot_detected"])
            self.assertAlmostEqual(broadcast["pilot_level_percent"], 9, delta=0.6)
            self.assertEqual(broadcast["stereo_blend_percent"], 100.0 if dsp is stereo else 0.0)
            rds = broadcast["rds"]
            self.assertTrue(rds["locked"])
            self.assertEqual((rds["pi"], rds["ps"], rds["radiotext"]), ("CAFE", "DEMO FM", "Stereo and RDS test signal"))
            self.assertEqual((rds["pty"], rds["tp"], rds["ta"]), (10, True, False))
            self.assertEqual(rds["alternative_frequencies_hz"], [98_100_000, 98_600_000, 100_700_000])
            self.assertGreater(rds["block_quality"]["passed"], 0)

    def test_broadcast_audio_passes_15_khz_with_deemphasis_and_mono_fallback(self):
        rate = 384_000
        time = np.arange(rate // 2) / rate
        multiplex = (0.3 * (np.sin(2 * np.pi * 1_000 * time) + np.sin(2 * np.pi * 15_000 * time))).astype(np.float32)
        for deemphasis in (50, 75):
            with self.subTest(deemphasis=deemphasis):
                dsp = BroadcastFM(rate, deemphasis, stereo=True)
                left, right = dsp.process(multiplex)[AUDIO_RATE // 4:].T
                self.assertFalse(dsp.stereo_locked)
                np.testing.assert_allclose(left, right, atol=1e-7)
                fft = np.abs(np.fft.rfft(left * np.hanning(len(left))))
                measured = fft[3750] / fft[250]
                tau = deemphasis * 1e-6
                expected = np.sqrt((1 + (2 * np.pi * 1_000 * tau) ** 2) / (1 + (2 * np.pi * 15_000 * tau) ** 2))
                self.assertAlmostEqual(measured, expected, delta=0.02)

    def test_modes_recover_tones_across_uneven_iq_blocks(self):
        rate = 1_200_000
        rng = np.random.default_rng(12)
        for mode, width, (frequency, tone, _) in zip(("wfm", "nfm"), (200_000, 12_500), DEMO_FM):
            with self.subTest(mode=mode):
                offset = 240_000
                iq = demo_iq(frequency - offset, rate, 0, rate // 5, rng)
                whole, power = ChannelDemodulator(rate, offset, mode, width, 50).process(iq)
                self.assertIs(type(power), float)
                dsp = ChannelDemodulator(rate, offset, mode, width, 50)
                parts = np.array_split(iq, 13)
                audio = np.concatenate([dsp.process(part)[0] for part in parts])
                self.assertAlmostEqual(len(audio), AUDIO_RATE // 5, delta=1)
                np.testing.assert_allclose(audio, whole, atol=1e-6)
                audio = audio[960:]
                fft = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
                actual_tone = fft.argmax() * AUDIO_RATE / len(audio)
                self.assertAlmostEqual(actual_tone, tone, delta=4)

    def test_narrow_fm_rejects_stronger_adjacent_channel(self):
        rate = 1_200_000
        frequency, tone, _ = DEMO_FM[1]
        offset = 240_000
        rng = np.random.default_rng(19)
        iq = demo_iq(frequency - offset, rate, 0, rate // 5, rng)
        time = np.arange(len(iq)) / rate
        iq += 0.14 * np.exp(1j * (2 * np.pi * (offset + 25_000) * time - 2 * np.cos(2 * np.pi * 1200 * time)))
        audio, _ = ChannelDemodulator(rate, offset, "nfm", 12_500, 50).process(iq)
        audio = audio[960:]
        fft = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
        tone_bin = round(tone * len(audio) / AUDIO_RATE)
        adjacent_bin = round(1200 * len(audio) / AUDIO_RATE)
        self.assertGreater(fft[tone_bin], 100 * fft[adjacent_bin])

    def test_am_and_both_sidebands_recover_the_selected_audio(self):
        rate = 1_200_000
        offset = 200_000
        time = np.arange(rate // 5) / rate
        carrier = 0.02 * np.exp(2j * np.pi * offset * time)
        upper = np.exp(2j * np.pi * 1200 * time)
        lower = np.exp(-2j * np.pi * 1700 * time)
        cases = (
            ("am", 10_000, carrier * (1 + 0.6 * upper.real), 1200, None),
            ("usb", 2_700, carrier * (upper + lower), 1200, 1700),
            ("lsb", 2_700, carrier * (upper + lower), 1700, 1200),
        )
        for mode, width, iq, tone, rejected in cases:
            with self.subTest(mode=mode):
                dsp = ChannelDemodulator(rate, offset, mode, width, 50)
                output = np.concatenate([dsp.process(block)[0] for block in np.array_split(iq.astype(np.complex64), 11)])
                output = output[960:]
                fft = np.abs(np.fft.rfft(output * np.hanning(len(output))))
                self.assertAlmostEqual(fft.argmax() * AUDIO_RATE / len(output), tone, delta=4)
                if rejected:
                    self.assertGreater(fft.max(), 100 * fft[round(rejected * len(output) / AUDIO_RATE)])

    def test_iq_keeps_complex_phase_at_the_output_rate(self):
        rate = 1_200_000
        offset = 240_000
        iq = (0.05 * np.exp(2j * np.pi * (offset - 4_800) * np.arange(rate // 5) / rate)).astype(np.complex64)
        samples, _ = ChannelDemodulator(rate, offset, "iq", 25_000, 50).process(iq)
        self.assertEqual(samples.dtype, np.dtype("complex64"))
        self.assertAlmostEqual(len(samples), AUDIO_RATE // 5, delta=1)
        phase = np.angle(np.mean(samples[1001:] * samples[1000:-1].conj()))
        self.assertAlmostEqual(AUDIO_RATE * phase / (2 * np.pi), -4800, delta=1)


class RdsProtocolTests(unittest.TestCase):
    def group_bits(self, group, corrupt=()):
        for index, word in enumerate(group):
            offset = OFFSET_C_PRIME if index == 2 and group[1] & 0x800 else OFFSETS[index]
            data = (word << 10) | (checkword(word << 10) ^ offset)
            if index in corrupt:
                data ^= (1 << 20) | (1 << 17)
            for bit in range(25, -1, -1):
                yield (data >> bit) & 1

    def send_group(self, decoder, group, corrupt=()):
        for bit in self.group_bits(group, corrupt):
            decoder.push(bit)

    def feed_baseband(self, decoder, group, corrupt=()):
        bits = list(self.group_bits(group, corrupt)) * 10
        symbols = np.cumprod(1 - 2 * np.array(bits))
        pulse = np.sin(2 * np.pi * (np.arange(SAMPLES_PER_BIT) + 0.5) / SAMPLES_PER_BIT)
        decoder.process(np.outer(symbols, pulse).ravel().astype(np.complex64))

    def test_crc_rejects_corrupt_groups_and_new_text_replaces_previous_text(self):
        rds = RdsGroups()
        self.send_group(rds, (0x1234, 0x2800, 0x1234, 0x410D), corrupt=(3,))
        self.assertIsNone(rds.snapshot()["radiotext"])
        self.send_group(rds, (0x1234, 0x2800, 0x1234, 0x410D))
        self.assertEqual(rds.snapshot()["radiotext"], "A")
        self.send_group(rds, (0x1234, 0x2810, 0x1234, 0x420D))
        self.assertEqual(rds.snapshot()["radiotext"], "B")
        self.send_group(rds, (0x4321, 0, 0, 0x464D))
        self.assertEqual(rds.snapshot()["pi"], "4321")
        self.assertIsNone(rds.snapshot()["radiotext"])

    def test_rds_combines_verified_blocks_and_keeps_metadata_through_a_fade(self):
        rds = RdsGroups()
        pi = 0xC204
        group = (pi, 0x2800, pi, 0x410D)
        self.send_group(rds, group, corrupt=(1, 2))
        self.assertFalse(rds.snapshot()["locked"])
        self.send_group(rds, group, corrupt=(1, 2))
        self.assertTrue(rds.snapshot()["locked"])
        self.assertIsNone(rds.snapshot()["radiotext"])
        # An intact C' supplies the identity even when the A block is damaged.
        self.send_group(rds, group, corrupt=(0,))
        self.assertEqual(rds.snapshot()["radiotext"], "A")
        for _ in range(round(4 * BIT_RATE)):
            rds.push(0)
        self.assertFalse(rds.snapshot()["locked"])
        self.assertEqual(rds.snapshot()["radiotext"], "A")
        self.send_group(rds, group)
        self.assertTrue(rds.snapshot()["locked"])
        for _ in range(round(16 * BIT_RATE)):
            rds.push(0)
        self.assertIsNone(rds.snapshot()["pi"])

    def test_radiotext_combines_independent_halves_and_checks_message_flag(self):
        text = RdsText()
        text.add(0x2000, 0x5241, None)
        text.add(0x2000, None, 0x4449)
        text.add(0x2001, 0x4F20, 0x464D)
        self.assertEqual(text.radio_text(), ("RADIO FM…", True))
        text.add(0x2010, 0x5858, None, pi_exact=False)
        self.assertEqual(text.radio_text(), ("RADIO FM…", True))
        text.add(0x2002, 0x0D20, None)
        self.assertEqual(text.radio_text(), ("RADIO FM", False))
        text.add(0x2010, 0x4E45, 0x570D)
        self.assertEqual(text.radio_text(), ("NEW", False))

    def test_radiotext_field_keeps_unknown_positions_and_known_spaces(self):
        text = RdsText()
        self.assertIsNone(text.radio_text_field())
        text.add(0x2004, 0x2041, None)
        text.add(0x2000, None, 0x4243)
        field = text.radio_text_field()
        self.assertEqual(len(field["cells"]), 64)
        self.assertEqual(field["cells"][:5], [None, None, "B", "C", None])
        self.assertEqual(field["cells"][16:19], [" ", "A", None])
        self.assertIsNone(field["end"])
        text.add(0x2006, None, 0x595A)
        text.add(0x2005, 0x580D, None)
        field = text.radio_text_field()
        self.assertEqual(field["end"], 21)
        self.assertEqual(field["cells"][20:], ["X"] + [None] * 43)
        text.add(0x2812, None, 0x4E45)
        self.assertEqual(text.radio_text_field(), {"cells": [None] * 4 + ["N", "E"] + [None] * 26, "end": None})

    def test_radio_text_continues_after_lock_loss_and_decoder_restart(self):
        decoder = RdsDecoder()
        self.feed_baseband(decoder, (0xC204, 0x2000, 0x4142, 0x4344))
        initial = decoder.snapshot()
        self.assertTrue(initial["locked"])
        self.assertEqual(initial["radiotext_field"]["cells"][:4], list("ABCD"))

        decoder.process(np.zeros(16 * RDS_RATE, dtype=np.complex64))
        lost = decoder.snapshot()
        self.assertFalse(lost["locked"])
        self.assertEqual(lost["radiotext_field"], initial["radiotext_field"])

        recreated = RdsDecoder(decoder.station)
        self.assertEqual(recreated.snapshot()["radiotext_field"], initial["radiotext_field"])
        self.feed_baseband(recreated, (0xC204, 0x2001, 0x4546, 0x4748))
        recovered = recreated.snapshot()
        self.assertTrue(recovered["locked"])
        self.assertEqual(recovered["radiotext_field"]["cells"][:8], list("ABCDEFGH"))

        self.feed_baseband(recreated, (0xC204, 0x2011, 0x494A, 0x4B4C))
        self.assertEqual(recreated.snapshot()["radiotext_field"]["cells"][:8], [None] * 4 + list("IJKL"))
        self.feed_baseband(recreated, (0xC205, 0x2011, 0x4D4E, 0x4F50))
        changed = recreated.snapshot()
        self.assertEqual(changed["pi"], "C205")
        self.assertEqual(changed["radiotext_field"]["cells"][:8], [None] * 4 + list("MNOP"))

    def test_block_quality_counts_one_group_at_a_time_and_expires(self):
        decoder = RdsDecoder()
        group = (0xC204, 0x2000, 0x4142, 0x4344)
        self.feed_baseband(decoder, group)
        quality = decoder.snapshot()["block_quality"]
        self.assertEqual(quality["window_seconds"], 5)
        self.assertGreater(quality["passed"], 0)
        self.assertLessEqual(quality["passed"], 40)
        self.feed_baseband(decoder, group, corrupt=(3,))
        self.assertGreater(decoder.snapshot()["block_quality"]["rejected"], 0)
        decoder.process(np.zeros(8 * RDS_RATE, dtype=np.complex64))
        self.assertIsNone(decoder.snapshot()["block_quality"])

    def test_alternative_frequencies_follow_fm_lists_for_the_tuned_transmitter(self):
        alternatives = RdsAlternativeFrequencies(93_701_000)
        self.assertIsNone(alternatives.snapshot())
        alternatives.add(0xE43E)  # Four entries including the tuned 93.7 MHz.
        alternatives.add(0x426A)  # 94.1 and 98.1 MHz.
        alternatives.add(0xFA10)  # An AM code consumes the following byte.
        alternatives.add(0xCDCD)
        self.assertEqual(alternatives.snapshot(), [94_100_000, 98_100_000])
        alternatives.add(0xE0CD)
        self.assertEqual(alternatives.snapshot(), [])

        alternatives = RdsAlternativeFrequencies(93_700_000)
        alternatives.add(0xE53E)
        alternatives.add(0x3E6A)  # 93.7 paired with 98.1 MHz.
        alternatives.add(0x423E)  # Descending pair for a regional variant.
        alternatives.add(0xE56A)  # A sublist for a different transmitter.
        alternatives.add(0x6A84)
        self.assertEqual(alternatives.snapshot(), [94_100_000, 98_100_000])

    def test_rds_corrects_weak_symbol_errors_and_corroborates_recovered_name(self):
        word = 0x4242 << 10
        code = word | (checkword(word) ^ OFFSETS[3])
        errors = (1 << 21) | (1 << 20)
        damaged = code ^ errors
        scores = np.where(damaged & BIT_WEIGHTS, -1.0, 1.0)
        scores[(errors & BIT_WEIGHTS) != 0] *= 0.05
        value, exact = recover_word(np.array([damaged]), np.array([checkword(damaged)]), scores[None], OFFSETS[3])
        self.assertEqual((value, exact), (0x4242, False))
        text = RdsText()
        for address, pair in enumerate((0x4320, 0x5234, 0x2020), 1):
            text.add(address, None, pair)
        text.add(0, None, value, fourth_exact=exact)
        self.assertIsNone(text.station_name())
        text.add(0, None, value, fourth_exact=exact)
        self.assertEqual(text.station_name(), "BBC R4")


class ListenStateTests(unittest.TestCase):
    def test_broadcast_metadata_survives_capture_interruptions_and_clears_on_retune(self):
        listening = Listening()
        focus = (97_500_000, 99_500_000)
        capture = IQCapture(98_500_000, 2_000_000, focus, focus)
        listening.set_capture(capture)
        bounds = 70_000_000, 6_000_000_000
        wfm = validate_tune({"mode": "wfm_stereo", "frequency_hz": 98_600_000}, *bounds)
        state = listening.configure(wfm, focus)
        station = listening.read_rds_station(state["revision"], state["audio_epoch"])
        station.identify(0xC204)
        station.text.add(0x2000, 0x4142, None)
        decoder = RdsDecoder(station)
        rds = {**decoder.snapshot(), "locked": True}
        broadcast = {"stereo_locked": True, "rds": rds}
        listening.publish(state["revision"], state["audio_epoch"], b"", -30, False, broadcast)
        listening.mark_gap(state["revision"], state["audio_epoch"])
        expected = {"stereo_locked": False, "rds": {**rds, "locked": False}}
        self.assertEqual(listening.snapshot()["broadcast"], expected)

        listening.set_capture(None)
        listening.set_capture(capture)
        mono = validate_tune({"mode": "wfm", "frequency_hz": 98_600_000}, *bounds)
        restored = listening.configure(mono, focus)
        self.assertEqual(restored["broadcast"], expected)
        retained = listening.read_rds_station(restored["revision"], restored["audio_epoch"])
        self.assertIs(retained, station)
        self.assertEqual(RdsDecoder(retained).snapshot()["radiotext_field"], rds["radiotext_field"])

        other = validate_tune({"mode": "wfm", "frequency_hz": 98_100_000}, *bounds)
        changed = listening.configure(other, focus)
        self.assertIsNone(changed["broadcast"])
        self.assertIsNone(listening.read_rds_station(restored["revision"], restored["audio_epoch"]))
        new_station = listening.read_rds_station(changed["revision"], changed["audio_epoch"])
        self.assertIsNone(new_station.pi)
        self.assertIsNone(new_station.text.radio_text_field())

    def test_stereo_frame_counts_are_in_samples_per_channel_and_reset_on_mode_change(self):
        listening = Listening()
        focus = (97_500_000, 99_500_000)
        listening.set_capture(IQCapture(98_500_000, 2_000_000, focus, focus))
        bounds = 70_000_000, 6_000_000_000
        stereo = validate_tune({"mode": "wfm_stereo", "frequency_hz": 98_600_000}, *bounds)
        state = listening.configure(stereo, focus)
        self.assertEqual(state["audio_channels"], 2)
        listening.publish(state["revision"], state["audio_epoch"], b"\0" * 8, -30, False, {"stereo_locked": True})
        self.assertEqual(struct.unpack("<III", listening.frames[-1][1][:12])[2], 2)
        self.assertTrue(listening.snapshot()["broadcast"]["stereo_locked"])
        mono = validate_tune({"mode": "wfm", "frequency_hz": 98_600_000}, *bounds)
        changed = listening.configure(mono, focus)
        self.assertEqual(changed["audio_channels"], 1)
        self.assertIsNone(changed["broadcast"])
        writes = []
        listening.stream(writes.append, state["audio_epoch"], stereo.sample_size)
        self.assertEqual(writes, [])

    def test_bookmark_presets_and_recall_follow_active_channels(self):
        bookmark = {"frequency_hz": 433_920_000, "modulation": " Narrow FM ", "bandwidth_hz": 25_000}
        preset = bookmark_tune(bookmark)
        self.assertEqual((preset.frequency_hz, preset.mode, preset.filter_hz), (433_920_000, "nfm", 25_000))
        unknown = bookmark_tune({**bookmark, "frequency_hz": 98_100_000,
                                 "modulation": "FM?", "bandwidth_hz": 3_000})
        self.assertEqual((unknown.mode, unknown.filter_hz), ("wfm", 200_000))

        listening = Listening()
        bounds = 70_000_000, 6_000_000_000
        self.assertIsNone(listening.recall(preset, *bounds)["settings"])
        focus = (97_500_000, 99_500_000)
        capture = IQCapture(98_500_000, 2_000_000, focus, focus)
        listening.set_capture(capture)
        active = validate_tune({"mode": "wfm", "frequency_hz": 98_100_000,
                                "squelch_dbfs": -60, "deemphasis_us": 75}, *bounds)
        listening.configure(active, focus)
        recalled = listening.recall(preset, *bounds)
        self.assertEqual(recalled["settings"]["frequency_hz"], preset.frequency_hz)
        self.assertEqual(recalled["settings"]["squelch_dbfs"], -60)
        self.assertEqual(recalled["settings"]["deemphasis_us"], 75)
        self.assertIs(listening.capture, capture)
        self.assertIsNone(recalled["applied_revision"])

    def test_in_band_tune_keeps_window_and_invalidates_old_audio(self):
        listening = Listening()
        bounds = 70_000_000, 6_000_000_000
        focus = 97_500_000, 99_500_000
        capture = IQCapture(98_500_000, 2_000_000, focus, focus)
        first = validate_tune({"mode": "wfm", "frequency_hz": 98_100_000}, *bounds)
        with self.assertRaises(CaptureUnavailable):
            listening.configure(first, focus)
        listening.set_capture(capture)
        old = listening.configure(first, focus)
        listening.publish(old["revision"], old["audio_epoch"], b"\0\1", -29.8, False, snr=12.3)
        self.assertEqual(listening.snapshot()["applied_revision"], old["revision"])
        self.assertEqual(listening.snapshot()["snr_db"], 12.3)
        second = validate_tune({"mode": "wfm", "frequency_hz": 98_300_000}, *bounds)
        new = listening.configure(second, focus)
        self.assertEqual(old["capture"], new["capture"])
        self.assertGreater(new["audio_epoch"], old["audio_epoch"])
        listening.publish(old["revision"], old["audio_epoch"], b"\0\1", -29.8, False, snr=12.3)
        self.assertIsNone(listening.snapshot()["applied_revision"])
        self.assertIsNone(listening.snapshot()["snr_db"])
        self.assertEqual(struct.unpack("<III", listening.frames[-1][1])[2], 0)
        outside = validate_tune({"mode": "usb", "frequency_hz": 99_499_000}, *bounds)
        with self.assertRaises(CaptureUnavailable):
            listening.configure(outside, focus)
        self.assertEqual(listening.snapshot()["settings"]["frequency_hz"], second.frequency_hz)
        self.assertIsNone(listening.configure(None, focus)["settings"])
        self.assertIs(listening.capture, capture)

    def test_rejects_filters_and_modes_outside_supported_ranges(self):
        bounds = 70_000_000, 6_000_000_000
        for invalid in ({"filter_hz": 400_000}, {"mode": "digital"}, {"mode": []},
                        {"frequency_hz": 70_000_000}, {"squelch_dbfs": True}, {"deemphasis_us": 10}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_tune({"mode": "wfm", "frequency_hz": 98_100_000} | invalid, *bounds)
