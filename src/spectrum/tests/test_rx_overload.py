import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fpga_sweep import RxOverload


class RxOverloadTests(unittest.TestCase):
    def setUp(self):
        self.control = Mock()
        clock = patch("fpga_sweep.time.monotonic", return_value=10.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def test_status_and_detector_reset_follow_the_chip_frontend(self):
        for frontend, address, reset in (("FE-RX2", 0x2B9, 0x80), ("FE-RX1", 0x2B8, 0x40)):
            with self.subTest(frontend=frontend):
                self.control.reset_mock()
                self.control.read.side_effect = [0x2B, 0xE5]
                monitor = RxOverload(self.control, frontend)
                monitor.rearm()
                monitor.observe()
                self.assertEqual(monitor.flags, 0x05)
                self.assertEqual(self.control.mock_calls, [
                    call.read(0x107), call.write(0x107, 0x2B | reset), call.write(0x107, 0x2B), call.read(address),
                ])

    def test_sweep_retains_recent_flags_across_a_retune_and_expires_them(self):
        monitor = RxOverload(self.control, "FE-RX2")
        self.control.read.side_effect = [0x2B, 0, 0x10, 0x2B, 0, 0]
        monitor.rearm()
        monitor.observe()
        monitor.observe()
        self.assertEqual(monitor.flags, 0)
        self.assertEqual(self.control.read.call_count, 2)

        monitor.observe(force=True)
        monitor.rearm()
        monitor.observe()
        self.assertEqual(monitor.flags, 0x10)
        self.clock.return_value = 11.1
        monitor.observe()
        self.assertEqual(monitor.flags, 0)

    def test_failed_status_read_reports_unknown_and_recovers(self):
        monitor = RxOverload(self.control, "FE-RX2")
        self.control.read.side_effect = [0x2B, 2, RuntimeError("SPI timeout"), 0]
        monitor.rearm()
        monitor.observe()
        self.assertEqual(monitor.flags, 2)
        self.clock.return_value = 10.2
        monitor.observe()
        self.assertIsNone(monitor.flags)
        self.clock.return_value = 10.4
        monitor.observe()
        self.assertEqual(monitor.flags, 0)

    def test_detector_reset_attempts_to_restore_threshold_when_assertion_fails(self):
        monitor = RxOverload(self.control, "FE-RX1")
        self.control.read.return_value = 0x2B
        self.control.write.side_effect = [RuntimeError("SPI timeout"), None]
        with self.assertRaisesRegex(RuntimeError, "SPI timeout"):
            monitor.rearm()
        self.control.write.assert_has_calls([call(0x107, 0x6B), call(0x107, 0x2B)])
