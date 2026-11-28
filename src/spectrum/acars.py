"""Streaming decoder for the 2,400 bit/s VHF ACARS waveform (ARINC 618)."""

import numpy as np


BIT_RATE = 2400
HEADER_LENGTH = 12
MAX_BODY_LENGTH = HEADER_LENGTH + 1 + 220 + 1  # STX, text, ETX/ETB
END_OF_TEXT = 0x03
END_OF_BLOCK = 0x17
START_OF_TEXT = 0x02
SYNC_BYTES = b"+*\x16\x16\x01"
TIMING_MARGIN = 2


def acars_crc(data):
    """CRC-16/CCITT in wire order, including the odd parity bits."""
    remainder = 0
    for octet in data:
        remainder ^= octet
        for _ in range(8):
            carry = remainder & 1
            remainder >>= 1
            if carry:
                remainder ^= 0x8408
    return remainder


def odd_parity(octet):
    return octet | ((1 - octet.bit_count() % 2) << 7)


def display_text(data):
    text = data.decode("ascii").replace("\r\n", "\n").replace("\r", "\n")
    controls = {value: chr(0x2400 + value) for value in range(32) if value not in (9, 10)}
    controls[127] = "␡"
    return text.translate(controls)


def read_block(body, checksum):
    if acars_crc(body) != int.from_bytes(checksum, "little"):
        return None
    data = bytes(octet & 0x7f for octet in body)
    text = display_text(data[HEADER_LENGTH + 1:-1])
    return {"mode": chr(data[0]), "aircraft": display_text(data[1:8]).strip(". "),
            "label": display_text(data[9:11]), "block_id": display_text(data[11:12]),
            "text": text, "more": data[-1] == END_OF_BLOCK}


class AcarsDecoder:
    """Search AM audio for sync, then track MSK symbol timing through each block."""

    def __init__(self, rate):
        self.width = rate // BIT_RATE
        phase = np.arange(self.width) * (np.pi / self.width)
        self.tones = np.array((np.sin(2 * phase), np.sin(phase)), dtype=np.float32)
        # Include eight pre-key bits before the fixed synchronization characters.
        previous = 1
        symbols = [self.tones[0]] * 8
        for octet in SYNC_BYTES:
            for index in range(8):
                bit = (odd_parity(octet) >> index) & 1
                symbols.append((2 * previous - 1) * self.tones[int(bit != previous)])
                previous = bit
        self.sync = np.concatenate(symbols)
        self.sync -= self.sync.mean()
        self.sync_energy = float(self.sync @ self.sync)
        self.sync_last_bit = previous
        self.audio = np.empty(0, dtype=np.float32)
        self.position = None

    def find_sync(self):
        search_ahead = self.width // 2
        length = len(self.sync)
        count = len(self.audio) - length + 1 - search_ahead
        if count <= 0:
            return False
        cross = np.correlate(self.audio, self.sync)
        energy = np.correlate(self.audio ** 2, np.ones(length, dtype=np.float32))
        quality = np.abs(cross) / np.sqrt(np.maximum(energy * self.sync_energy, 1e-20))
        candidates = np.flatnonzero(quality[:count] >= 0.62)
        if not len(candidates):
            self.audio = self.audio[count:].copy()
            return False
        first = candidates[0]
        peak = first + int(np.argmax(quality[first:first + search_ahead + 1]))
        end = peak + length
        self.audio = self.audio[end - TIMING_MARGIN:]
        self.position = float(TIMING_MARGIN)
        self.polarity = 1 if cross[peak] > 0 else -1
        self.previous = self.sync_last_bit
        self.octet = self.bits = 0
        self.body = bytearray()
        self.checksum = bytearray()
        return True

    def read_bit(self, windows, center):
        first = max(0, center - TIMING_MARGIN)
        nearby = windows[first:center + TIMING_MARGIN + 1]
        scores = (nearby @ self.tones.T) * (self.polarity * (2 * self.previous - 1))
        index, tone = np.unravel_index(scores.argmax(), scores.shape)
        self.position += self.width + 0.15 * (first + index - self.position)
        self.previous ^= int(tone)
        return self.previous

    def read_octet(self, octet):
        """Return whether the block ended and, when valid, the decoded fields."""
        control = octet & 0x7f
        if self.body and (self.body[-1] & 0x7f) in (END_OF_TEXT, END_OF_BLOCK):
            self.checksum.append(octet)
            if len(self.checksum) == 2:
                return True, read_block(self.body, self.checksum)
            return False, None
        if octet.bit_count() % 2 != 1:
            return True, None
        if len(self.body) == HEADER_LENGTH and control not in (START_OF_TEXT, END_OF_TEXT):
            return True, None
        if len(self.body) < HEADER_LENGTH and control in (END_OF_TEXT, END_OF_BLOCK):
            return True, None
        if len(self.body) >= MAX_BODY_LENGTH:
            return True, None
        self.body.append(octet)
        return False, None

    def process(self, audio):
        self.audio = np.concatenate((self.audio, audio.astype(np.float32, copy=False)))
        messages = []
        while self.position is not None or self.find_sync():
            if len(self.audio) < self.width:
                break
            windows = np.lib.stride_tricks.sliding_window_view(self.audio, self.width)
            while round(self.position) + TIMING_MARGIN < len(windows):
                self.octet |= self.read_bit(windows, round(self.position)) << self.bits
                self.bits += 1
                if self.bits < 8:
                    continue
                finished, message = self.read_octet(self.octet)
                self.octet = self.bits = 0
                if finished:
                    if message is not None:
                        messages.append(message)
                        self.audio = self.audio[round(self.position):]
                    self.position = None
                    break
            if self.position is not None:
                break
        return messages
