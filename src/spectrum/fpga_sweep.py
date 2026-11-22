"""B210 Fast Lock controls and rolling band-only receive sweeps."""
from bisect import bisect_right
import time


B210_RX_BAND_EDGES = (1.3e9, 2.2e9, 4e9)


class FastLock:
    def __init__(self, radio):
        self.regs = radio.get_user_settings_iface(0)
        status = self.regs.peek64(8) if self.regs is not None else 0
        if status >> 32 != 0x52584C33:
            raise RuntimeError("The FPGA image does not provide Fast Lock and RF DC polling")
        self.normal_settings = None
        self.tag = (status >> 25) & 0x7F
        if self.read(0x037) & 0xF8 != 0x08:
            raise RuntimeError("FPGA SPI did not identify the AD9361")

    def transact(self, command):
        self.tag = self.tag % 127 + 1
        self.regs.poke32(8, (self.tag << 25) | command)
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            status = self.regs.peek64(8)
            if (status >> 25) & 0x7F == self.tag:
                if status & (1 << 24):
                    operation = "profile refill or verification" if command >> 20 == 0x12 else "RX PLL lock"
                    raise RuntimeError(f"FPGA {operation} failed")
                return (status >> 16) & 0xFF
        raise RuntimeError("FPGA retune mailbox did not acknowledge the command")

    def read(self, address):
        return self.transact(address << 8)

    def write(self, address, value):
        self.transact(0x800000 | (address << 8) | value)

    def profile(self):
        # AD9361 UG-570 RX profile words 0–15, captured after an ordinary UHD tune.
        registers = {addr: self.read(addr) for addr in (
            0x005, *range(0x231, 0x23A), 0x23B, 0x23E, 0x23F, 0x240, 0x242, 0x250, 0x251
        )}
        cp = registers[0x23B] & 63
        r3, c3, r1 = registers[0x240] & 15, registers[0x23F] & 15, registers[0x23F] >> 4
        filters = ((registers[0x23E] & 15) << 4) | (registers[0x23E] >> 4)
        return [
            *[registers[addr] for addr in range(0x231, 0x236)],
            ((registers[0x242] & 7) << 4) | (registers[0x239] & 15),
            (((registers[0x242] >> 3) & 3) << 6) | cp, cp,
            r3 * 17, c3 * 17, filters, r1 * 17,
            (registers[0x250] & 0x70) | (registers[0x005] & 15),
            (((registers[0x238] >> 3) & 15) << 4) | (registers[0x251] & 15),
            registers[0x237], ((registers[0x236] & 127) << 1) | (registers[0x238] & 1),
        ]

    def refill(self, slot, profile, previous_alc):
        words = list(profile)
        # Distinct consecutive ALC values avoid the AD9361 Fast Lock issue.
        if words[15] >> 1 == previous_alc:
            words[15] ^= 2
        for pair in range(8):
            value = words[2 * pair] | (words[2 * pair + 1] << 8)
            self.regs.poke32(8, 0x1100000 | (pair << 16) | value)
        self.transact(0x1200000 | slot)
        return words[15] >> 1

    def prepare(self, profile):
        alc = self.refill(0, profile, self.read(0x236) & 127)
        self.normal_settings = (self.read(0x015), self.read(0x230))
        self.write(0x25B, 0)
        self.write(0x25A, 1)
        self.write(0x25F, 0)
        self.write(0x015, self.normal_settings[0] | 2)
        self.write(0x230, self.normal_settings[1] | 1)
        return alc

    def leave(self):
        if self.normal_settings is None:
            return
        # AD9361 Fast Lock exit: force and release ALC and VCO tune, then
        # restore the normal lock detector and ENSM synthesizer-ready mask.
        self.write(0x25A, 0)
        alc, vco = self.read(0x236), self.read(0x238)
        self.write(0x236, alc | 0x80)
        self.write(0x238, vco | 2)
        self.write(0x236, alc & 0x7F)
        self.write(0x238, vco & 0xFD)
        self.write(0x230, self.normal_settings[1])
        self.write(0x015, self.normal_settings[0])
        self.normal_settings = None

    def calibration_polling(self, enabled):
        self.transact(0x1300000 | int(enabled))

    def calibration_status(self):
        status = self.regs.peek64(16)
        if status >> 40 != 0x524443:
            raise RuntimeError("RF DC timing mailbox was not found")
        return (status >> 32) & 255, status & 0xFFFFFF, bool(status & (1 << 24))

    def recall(self, slot):
        self.transact(0x1000000 | slot)


class BandSweep:
    def __init__(self, receiver, centers):
        self.receiver = receiver
        self.rate, self.bandwidth = receiver.rate, receiver.bandwidth
        if (self.rate, self.bandwidth) != (56e6, 56e6):
            raise RuntimeError("FPGA sweep requires 56 MS/s and 56 MHz analog bandwidth")
        self.control = FastLock(receiver.radio)
        self.centers = centers
        self.anchors = [0, *centers.searchsorted(B210_RX_BAND_EDGES)]
        self.profiles = []
        for center in centers:
            actual = self.tune(center)
            self.profiles.append((actual, self.control.profile()))
        self.control.calibration_polling(True)
        self.calibration_count = self.control.calibration_status()[0]
        self.band = None
        self.after_continuous = False
        self.previous_alc = None
        self.slot = 0

    def tune(self, center):
        return self.receiver.tune(center)

    def read_calibration(self, actual, required):
        count, _, timed_out = self.control.calibration_status()
        change = (count - self.calibration_count) % 256
        expected = (1,) if required else (0, 1)
        if change not in expected or (change and timed_out):
            raise RuntimeError(f"RF DC calibration failed at {actual / 1e6:.3f} MHz")
        self.calibration_count = count

    def tune_continuous(self, center):
        self.control.leave()
        actual = self.tune(center)
        self.read_calibration(actual, required=False)
        self.band = bisect_right(B210_RX_BAND_EDGES, actual)
        self.previous_alc = None
        self.after_continuous = True
        return actual

    def measure(self, center, offsets):
        index = self.centers.searchsorted(center)
        band = bisect_right(self.anchors, index) - 1
        anchor = self.anchors[band]
        actual = None
        if band != self.band:
            self.control.leave()
            actual = self.tune(self.centers[anchor])
            # The fixed-tune stream may already have calibrated near this anchor.
            self.read_calibration(actual, required=not self.after_continuous)
            self.after_continuous = False
            self.previous_alc = None
            self.band = band
        if actual is None or index != anchor:
            actual, profile = self.profiles[index]
            if self.previous_alc is None:
                self.slot = 0
                self.previous_alc = self.control.prepare(profile)
            else:
                # Refill the inactive slot while its neighbor drives the LO.
                self.slot ^= 1
                self.previous_alc = self.control.refill(self.slot, profile, self.previous_alc)
            self.control.recall(self.slot)
        self.receiver.capture(actual)
        return actual + offsets, self.receiver.spectrum()
