"""Decode the basic 57 kHz RDS stream after downconversion to 19 kS/s."""

from collections import Counter, defaultdict, deque
from itertools import combinations

import numpy as np


RDS_RATE = 19_000
SAMPLES_PER_BIT = 16
BIT_RATE = RDS_RATE / SAMPLES_PER_BIT
BLOCK_BITS = 26
GROUP_BITS = 4 * BLOCK_BITS
GROUP_SAMPLES = GROUP_BITS * SAMPLES_PER_BIT
QUALITY_SECONDS = 5
AF_BASE_HZ = 87_500_000
AF_STEP_HZ = 100_000
CHECK_MASK = (1 << BLOCK_BITS) - 1
GENERATOR = 0x5B9
OFFSETS = (0x0FC, 0x198, 0x168, 0x1B4)
OFFSET_C_PRIME = 0x350
BLOCK_INDEX = {**{offset: index for index, offset in enumerate(OFFSETS)}, OFFSET_C_PRIME: 2}


def checkword(bits):
    # Remainder modulo the RDS generator polynomial, IEC 62106 / NRSC annex A.
    while bits.bit_length() > 10:
        bits ^= GENERATOR << (bits.bit_length() - 11)
    return bits


SINGLE_BIT_ERRORS = {checkword(1 << index): 1 << index for index in range(BLOCK_BITS)}
OUTGOING_BIT = checkword(1 << BLOCK_BITS)
BIT_WEIGHTS = 1 << np.arange(BLOCK_BITS - 1, -1, -1, dtype=np.int32)


def burst_errors():
    # The shortened RDS code can correct a single burst spanning up to five
    # bits. Use a correction only when the demodulator marks those bits weak.
    errors = {}
    for span in range(1, 6):
        patterns = (1,) if span == 1 else (1 << (span - 1) | middle << 1 | 1 for middle in range(1 << (span - 2)))
        for pattern in patterns:
            for shift in range(BLOCK_BITS - span + 1):
                mask = pattern << shift
                indices = [index for index, weight in enumerate(BIT_WEIGHTS) if int(weight) & mask]
                errors[checkword(mask)] = (mask, indices)
    return errors


BURST_ERRORS = burst_errors()
SYMBOL_FLIPS = np.r_[0, BIT_WEIGHTS] ^ np.r_[BIT_WEIGHTS, 0]
SYMBOL_SYNDROMES = np.array([checkword(int(mask)) for mask in SYMBOL_FLIPS])
WEAK_SYMBOLS = 8
WEAK_COMBINATIONS = tuple(np.array(list(combinations(range(WEAK_SYMBOLS), count))) for count in (1, 2, 3))


def display_text(data, *, trim=True):
    # RDS Annex E's default Latin repertoire. Control bytes are not displayed.
    extended = (
        "áàéèíìóòúùÑÇŞß¡Ĳ"
        "âäêëîïôöûüñçşğıĳ"
        "ªα©‰Ğěňőπ€£$←↑→↓"
        "º¹²³±İńűµ¿÷°¼½¾§"
        "ÁÀÉÈÍÌÓÒÚÙŘČŠŽÐŁ"
        "ÂÄÊËÎÏÔÖÛÜřčšžðł"
        "ÃÅÆŒŷÝÕØÞŊŔĆŚŹŦð"
        "ãåæœŵýõøþŋŕćśźŧ "
    )
    special = {0x24: "¤", 0x5E: "―", 0x60: "‖", 0x7E: "¯"}
    text = "".join(special.get(byte, chr(byte)) if 32 <= byte < 127 else
                   extended[byte - 128] if byte >= 128 else " " for byte in data)
    return text.strip() if trim else text


class RdsText:
    """Assemble verified two-character blocks from any repetition of a message."""

    def __init__(self):
        self.ps = {}
        self.rt = {}
        self.ps_pending = {}
        self.rt_pending = {}
        self.version = None
        self.next_version = None
        self.combined = {}

    @staticmethod
    def observe(confirmed, pending, slot, word, exact):
        data = word.to_bytes(2, "big")
        if confirmed.get(slot) == data:
            pending.pop(slot, None)
        elif pending.get(slot) == data or (exact and slot not in confirmed):
            confirmed[slot] = data
            pending.pop(slot, None)
        else:
            pending[slot] = data

    def add(self, control, third, fourth, control_exact=True, third_exact=True, fourth_exact=True, *,
            pi_exact=True, soft=None):
        group = control >> 12
        if group == 0:
            if fourth is not None:
                self.observe(self.ps, self.ps_pending, control & 3, fourth, control_exact and fourth_exact)
            return
        if group != 2:
            return

        version_b = bool(control & 0x800)
        version = version_b, bool(control & 0x10)
        if version != self.version:
            if not control_exact:
                return
            exact_payload = (fourth is not None and fourth_exact) or (not version_b and third is not None and third_exact)
            if self.version is not None and version != self.next_version and not (pi_exact and exact_payload):
                self.next_version = version
                return
            self.version = version
            self.rt.clear()
            self.rt_pending.clear()
            self.combined.clear()
        self.next_version = None
        address = control & 15
        if version_b:
            blocks = ((address, fourth, fourth_exact, OFFSETS[3], None if soft is None else soft[1]),)
        else:
            blocks = ((2 * address, third, third_exact, OFFSETS[2], None if soft is None else soft[0]),
                      (2 * address + 1, fourth, fourth_exact, OFFSETS[3], None if soft is None else soft[1]))
        for slot, word, exact, offset, evidence in blocks:
            if word is not None:
                self.observe(self.rt, self.rt_pending, slot, word, control_exact and exact)
            if control_exact and evidence is not None and slot not in self.rt:
                self.combine(slot, offset, evidence, word)

    def combine(self, slot, offset, evidence, word):
        # Average the soft bits from repeat addresses before checking the CRC.
        # A word recovered this way still needs corroboration before display.
        norm = float(np.mean(np.abs(evidence)))
        if norm <= 0:
            return
        current = evidence / norm
        previous, count = self.combined.get(slot, (current, 0))
        count = min(count + 1, 5)
        combined = previous + (current - previous) / count
        self.combined[slot] = combined, count
        value = int((combined < 0) @ BIT_WEIGHTS)
        if count >= 3 and checkword(value) == offset and (value >> 10) != word:
            self.observe(self.rt, self.rt_pending, slot, value >> 10, False)

    def station_name(self):
        if len(self.ps) == 4:
            return display_text(b"".join(self.ps[slot] for slot in range(4))) or None
        return None

    def radio_text_field(self):
        if self.version is None:
            return None
        block_count = 16 if self.version[0] else 32
        cells = [None] * (2 * block_count)
        end = None
        for slot in range(block_count):
            if slot not in self.rt:
                continue
            data, terminator, _ = self.rt[slot].partition(b"\r")
            start = 2 * slot
            cells[start:start + len(data)] = display_text(data, trim=False)
            if terminator:
                end = start + len(data)
                break
        return {"cells": cells, "end": end}

    def radio_text(self):
        if self.version is None:
            return None, False
        block_count = 16 if self.version[0] else 32
        spans = []
        start = 0
        chunk = bytearray()
        for slot in range(block_count):
            if slot in self.rt:
                if not chunk:
                    start = slot
                chunk.extend(self.rt[slot])
                if b"\r" in self.rt[slot]:
                    spans.append((start, bytes(chunk).split(b"\r", 1)[0], True))
                    break
            elif chunk:
                spans.append((start, bytes(chunk), False))
                chunk.clear()
        else:
            if chunk:
                spans.append((start, bytes(chunk), True))
        if not spans:
            return None, False
        if spans[0][0] == 0 and spans[0][2]:
            return display_text(spans[0][1]) or None, False
        readable = [(display_text(data), start, ended) for start, data, ended in spans]
        text, start, ended = max(readable, key=lambda span: len(span[0]))
        if len(text) < 8:
            return None, False
        return f"{'…' if start else ''}{text}{'' if ended else '…'}", True

    def snapshot(self):
        radiotext, partial = self.radio_text()
        return {"ps": self.station_name(), "radiotext": radiotext, "radiotext_partial": partial,
                "radiotext_field": self.radio_text_field()}


class RdsAlternativeFrequencies:
    """Collect FM frequencies from CRC-verified type 0A groups."""

    def __init__(self, frequency_hz):
        self.tuned = round((frequency_hz - AF_BASE_HZ) / AF_STEP_HZ) if frequency_hz is not None else None
        self.reference = None
        self.lists = {}
        self.paired = defaultdict(set)
        self.method_b = False
        self.skip_am = False
        self.received = False

    def add(self, word):
        first, second = divmod(word, 256)
        if first == 224:
            self.lists.clear()
            self.paired.clear()
            self.reference = None
            self.method_b = self.skip_am = False
            self.received = True
            return
        header = 225 <= first <= 249
        if header:
            self.received = True
            self.reference = second if 1 <= second <= 204 else 0
            self.lists.setdefault(self.reference, set())
            self.skip_am = False
        if self.reference is None:
            return
        codes = []
        for code in (second,) if header else (first, second):
            if self.skip_am:
                self.skip_am = False
            elif code == 250:
                self.skip_am = True
            elif 1 <= code <= 204:
                codes.append(code)
        self.lists[self.reference].update(codes)
        # Method B pairs repeat the reference from their list header.
        if len(codes) == 2 and self.reference in codes and codes[0] != codes[1]:
            self.method_b = True
            self.paired[self.reference].update(codes)

    def snapshot(self):
        if not self.received:
            return None
        if self.method_b:
            reference = self.tuned if self.tuned is not None else self.reference
            codes = self.paired.get(reference, set())
        else:
            codes = self.lists.get(self.tuned, self.lists.get(self.reference, set()))
        return sorted(AF_BASE_HZ + code * AF_STEP_HZ for code in codes if code != self.tuned)


class RdsStation:
    """Decoded metadata for the current station, independent of signal lock."""

    def __init__(self, frequency_hz=None):
        self.frequency_hz = frequency_hz
        self.pi = None
        self.text = RdsText()
        self.alternatives = RdsAlternativeFrequencies(frequency_hz)

    def identify(self, pi):
        if pi != self.pi:
            self.pi = pi
            self.text = RdsText()
            self.alternatives = RdsAlternativeFrequencies(self.frequency_hz)


class RdsGroups:
    def __init__(self):
        self.word = 0
        self.syndrome = 0
        self.position = 0
        self.alignment = None
        self.candidates = {}
        self.last_exact = 0
        self.last_verified = None
        self.valid_groups = 0
        self.blocks = [None] * 4
        self.exact = [False] * 4
        self.third_is_pi = False
        self.clear_station()

    def clear_station(self):
        self.pi = self.pty = self.tp = self.ta = None
        self.text = RdsText()
        self.alternate_pi = None
        self.last_verified = None

    def push(self, bit):
        bit = int(bit)
        self.position += 1
        # Roll the remainder with the word so searching every bit stays cheap.
        self.syndrome = self.syndrome << 1 | bit
        if self.word >> (BLOCK_BITS - 1):
            self.syndrome ^= OUTGOING_BIT
        if self.syndrome & 0x400:
            self.syndrome ^= GENERATOR
        self.word = ((self.word << 1) | bit) & CHECK_MASK
        if self.position < BLOCK_BITS:
            return
        if self.alignment is not None and self.position - self.last_exact > 2 * BIT_RATE:
            self.alignment = None
            self.candidates.clear()
        if self.alignment is None:
            self.acquire()
            return
        index, extra = divmod((self.position - self.alignment) % GROUP_BITS, BLOCK_BITS)
        if not extra:
            self.read_block(index)

    def acquire(self):
        index = BLOCK_INDEX.get(self.syndrome)
        if index is None:
            return
        alignment = (self.position - index * BLOCK_BITS) % GROUP_BITS
        word = self.word >> 10
        is_pi = index == 0 or self.syndrome == OFFSET_C_PRIME
        hits = [hit for hit in self.candidates.get(alignment, ()) if self.position - hit[0] <= 4 * BIT_RATE]
        repeats_pi = is_pi and any(previous_pi and previous_word == word for _, _, previous_word, previous_pi in hits)
        hits.append((self.position, index, word, is_pi))
        self.candidates[alignment] = hits
        start = self.position - index * BLOCK_BITS
        current = [hit for hit in hits if hit[0] >= start]
        identities = {value for _, _, value, pi in current if pi}
        # Require either repeated PI or a group with three exact blocks and a
        # consistent PI. A coincidental CRC match alone cannot acquire sync.
        if not repeats_pi and (len(current) < 3 or len(identities) != 1):
            return
        pi = word if repeats_pi else identities.pop()
        if self.pi != pi:
            self.clear_station()
            self.pi = pi
        self.last_verified = self.last_exact = self.position
        self.alignment = alignment
        self.candidates.clear()
        self.reset_group()
        for _, block, value, has_pi in current:
            self.blocks[block] = value
            self.exact[block] = True
            if block == 2:
                self.third_is_pi = has_pi
        if index == 3:
            self.finish_group()

    def reset_group(self):
        self.blocks = [None] * 4
        self.exact = [False] * 4
        self.third_is_pi = False

    def read_block(self, index):
        target = OFFSETS[index]
        if index == 2:
            control = self.blocks[1] if self.exact[1] else None
            self.third_is_pi = bool(control & 0x800) if control is not None else self.syndrome == OFFSET_C_PRIME
            if self.third_is_pi:
                target = OFFSET_C_PRIME
        exact = self.syndrome == target
        error = 0 if exact else SINGLE_BIT_ERRORS.get(self.syndrome ^ target)
        self.blocks[index] = (self.word ^ error) >> 10 if error is not None else None
        self.exact[index] = exact
        if exact:
            self.last_exact = self.position
        if index == 3:
            self.finish_group()

    def finish_group(self):
        first, control, third, fourth = self.blocks
        exact_a, exact_b, exact_c, exact_d = self.exact
        exact_count = sum(self.exact)
        third_is_pi = self.third_is_pi
        self.reset_group()

        pi = first if exact_a else None
        # In a version B group C' repeats the PI. It can stand in for a
        # damaged A block, or corroborate a station change.
        repeated = third if exact_c and third_is_pi else None
        if pi is not None and repeated is not None and pi != repeated:
            return
        identity = pi if pi is not None else repeated
        if identity is not None and identity != self.pi:
            recent_match = self.alternate_pi is not None and self.alternate_pi[0] == identity \
                and self.position - self.alternate_pi[1] <= 4 * BIT_RATE
            if exact_count < 3 and pi != repeated and not recent_match:
                self.alternate_pi = (identity, self.position)
                return
            self.clear_station()
            self.pi = identity
        if identity is not None:
            self.alternate_pi = None
            self.last_verified = self.position
        if not self.has_station or control is None or (not exact_b and exact_count < 3):
            return
        if identity is None and not (exact_b and (exact_c or exact_d)):
            return
        # Only use a one-bit correction when every other block checks out.
        if not exact_c and exact_count < 3:
            third = None
        if not exact_d and exact_count < 3:
            fourth = None
        self.decode(control, third, fourth, exact_b, exact_c, exact_d, identity is not None)
        self.valid_groups += 1
        if exact_count >= 3:
            self.last_verified = self.position

    def decode(self, control, third, fourth, exact_b, exact_c, exact_d, pi_exact):
        self.pty = (control >> 5) & 31
        self.tp = bool(control & 0x400)
        if control >> 12 == 0 or control >> 11 == 31:
            self.ta = bool(control & 0x10)
        self.text.add(control, third, fourth, exact_b, exact_c, exact_d, pi_exact=pi_exact)

    @property
    def locked(self):
        return self.alignment is not None and self.last_verified is not None \
            and self.position - self.last_verified < 3 * BIT_RATE

    @property
    def has_station(self):
        return self.last_verified is not None and self.position - self.last_verified < 15 * BIT_RATE

    def snapshot(self):
        if not self.has_station:
            return {"locked": False, "pi": None, "ps": None, "radiotext": None, "radiotext_partial": False,
                    "radiotext_field": None, "pty": None, "tp": None, "ta": None}
        return {"locked": self.locked, "pi": f"{self.pi:04X}",
                "pty": self.pty, "tp": self.tp, "ta": self.ta, **self.text.snapshot()}


def biphase_template():
    # RDS uses two opposite impulses half a bit apart, shaped by a
    # cosine low-pass (NRSC-4, section 1.7). Keep four bits of its impulse response.
    fft_size = 1024
    frequency = np.fft.fftfreq(fft_size) * SAMPLES_PER_BIT
    response = np.where(abs(frequency) < 2, np.cos(np.pi * frequency / 4), 0)
    pulse = np.fft.fftshift(np.fft.ifft(response * 2j * np.sin(np.pi * frequency / 2))).real
    middle = fft_size // 2
    half = 2 * SAMPLES_PER_BIT
    return pulse[middle - half:middle + half + 1].astype(np.float32)


BIPHASE_TEMPLATE = biphase_template()


def recover_word(words, syndromes, soft, offset):
    exact = Counter(int(word) >> 10 for word in words[syndromes == offset])
    if exact:
        options = exact.most_common(2)
        if len(options) == 1 or options[0][1] > options[1][1]:
            return options[0][0], True
        return None, False

    reliability = np.abs(soft)
    baseline = np.maximum(np.median(reliability, axis=1), 1e-12)
    candidates = weak_symbols(words, syndromes ^ offset, reliability / baseline[:, None])
    if candidates:
        ranked = sorted(candidates, key=lambda word: (-len(candidates[word]), min(candidates[word].values())))
        return ranked[0], False
    candidates = defaultdict(list)
    for word, syndrome, values, typical in zip(words, syndromes, reliability, baseline):
        burst = BURST_ERRORS.get(int(syndrome) ^ offset)
        if burst is None:
            continue
        mask, indices = burst
        cost = float(np.sum(values[indices]) / typical)
        if cost <= 0.65:
            candidates[(int(word) ^ mask) >> 10].append(cost)
    if not candidates:
        return None, False
    ranked = sorted(candidates, key=lambda word: (-len(candidates[word]), min(candidates[word])))
    first = candidates[ranked[0]]
    if len(ranked) > 1:
        second = candidates[ranked[1]]
        if len(first) == len(second) and min(second) - min(first) < 0.15:
            return None, False
    return ranked[0], False


def weak_symbols(words, errors, reliability):
    # An uncertain differential BPSK symbol can corrupt two adjacent bits.
    # Try the weakest symbols against the block syndrome before display-level
    # repetition checks. Each timing phase contributes at most one vote.
    left = np.pad(reliability, ((0, 0), (1, 0)))
    right = np.pad(reliability, ((0, 0), (0, 1)))
    confidence = np.maximum(left, right)
    weak = np.argpartition(confidence, WEAK_SYMBOLS - 1, axis=1)[:, :WEAK_SYMBOLS]
    candidates = defaultdict(dict)
    for combination in WEAK_COMBINATIONS:
        symbols = weak[:, combination]
        costs = np.sum(np.take_along_axis(confidence[:, None, :], symbols, axis=2), axis=2)
        checks = np.bitwise_xor.reduce(SYMBOL_SYNDROMES[symbols], axis=2)
        for phase, choice in np.argwhere((checks == errors[:, None]) & (costs <= 1.15)):
            mask = int(np.bitwise_xor.reduce(SYMBOL_FLIPS[symbols[phase, choice]]))
            word = (int(words[phase]) ^ mask) >> 10
            cost = float(costs[phase, choice])
            candidates[word][phase] = min(cost, candidates[word].get(phase, cost))
    return candidates


class RdsDecoder:
    """Recover biphase symbol timing and differential bits from complex baseband."""

    def __init__(self, station=None):
        self.history = np.zeros(len(BIPHASE_TEMPLATE) - 1, dtype=np.complex64)
        self.last_matched = np.zeros(SAMPLES_PER_BIT, dtype=np.complex64)
        self.carrier_axis = 0j
        self.carrier_energy = 0.0
        self.sample_index = 0
        self.groups = [RdsGroups() for _ in range(SAMPLES_PER_BIT)]
        self.soft = np.empty(0, dtype=np.float32)
        self.last_group_sample = -GROUP_SAMPLES
        self.station = station if station is not None else RdsStation()
        self.block_quality = deque()

    def process(self, baseband, pilot_locked=False):
        block_size = RDS_RATE // 50
        for start in range(0, len(baseband), block_size):
            self.process_block(baseband[start:start + block_size], pilot_locked)

    def process_block(self, baseband, pilot_locked):
        samples = np.concatenate((self.history, baseband))
        self.history = samples[-len(self.history):].copy()
        matched = np.correlate(samples, BIPHASE_TEMPLATE, mode="valid")
        # Squaring removes BPSK polarity. Smooth the carrier axis across
        # reads so a weak subcarrier isn't estimated from one noisy block.
        time_constant = 0.2 if pilot_locked else 0.035
        weight = -np.expm1(-len(matched) / (time_constant * RDS_RATE))
        self.carrier_axis += weight * (np.mean(matched ** 2) - self.carrier_axis)
        self.carrier_energy += weight * (np.mean(np.abs(matched) ** 2) - self.carrier_energy)
        adjacent = np.concatenate((self.last_matched, matched))
        self.last_matched = adjacent[-SAMPLES_PER_BIT:].copy()
        threshold = 0.06 if pilot_locked else 0.1
        if abs(self.carrier_axis) > threshold * self.carrier_energy:
            base = (adjacent * np.exp(-0.5j * np.angle(self.carrier_axis))).real
            first, second = base[SAMPLES_PER_BIT:], base[:-SAMPLES_PER_BIT]
            scores = np.copysign(np.minimum(np.abs(first), np.abs(second)), first * second)
        else:
            product = (matched * adjacent[:-SAMPLES_PER_BIT].conj()).real
            scores = np.copysign(np.sqrt(np.abs(product)), product)
        changes = scores < 0
        # Let CRC-verified groups choose between all sampled timing phases.
        for phase, groups in enumerate(self.groups):
            first = (phase - self.sample_index) % SAMPLES_PER_BIT
            for bit in changes[first::SAMPLES_PER_BIT]:
                groups.push(bit)
        self.sample_index += len(matched)
        while self.block_quality and self.block_quality[0][0] <= self.sample_index - QUALITY_SECONDS * RDS_RATE:
            self.block_quality.popleft()
        self.soft = np.concatenate((self.soft, scores))[-2 * GROUP_SAMPLES:]
        phase, lane = self.best_station()
        if lane.locked:
            if self.station.pi != lane.pi:
                self.block_quality.clear()
            self.station.identify(lane.pi)
            self.recover_group(phase, lane.alignment)
            # A locked timing phase can provide the first verified fragments
            # while the shared decoder is still acquiring the message.
            text = self.station.text
            if text.version is None and lane.text.version is not None:
                text.version = lane.text.version
                text.rt.update(lane.text.rt)

    def best_station(self):
        return max(enumerate(self.groups), key=lambda item: (
            item[1].locked, item[1].has_station, item[1].valid_groups, item[1].last_verified or 0,
        ))

    def recover_group(self, phase, alignment):
        # Every timing phase has a different bit counter origin. Convert the
        # reference phase's D boundary to a common sample index before merging.
        boundary = (alignment - 1 + 3 * BLOCK_BITS) * SAMPLES_PER_BIT + phase
        latest = self.sample_index - SAMPLES_PER_BIT
        end = latest - (latest - boundary) % GROUP_SAMPLES
        start = self.sample_index - len(self.soft)
        first = end - (GROUP_BITS - 1) * SAMPLES_PER_BIT - SAMPLES_PER_BIT // 2
        if first < start or end - self.last_group_sample < GROUP_SAMPLES // 2:
            return
        self.last_group_sample = end
        indices = first - start + np.arange(SAMPLES_PER_BIT)[:, None] + SAMPLES_PER_BIT * np.arange(GROUP_BITS)
        soft = self.soft[indices].reshape(SAMPLES_PER_BIT, 4, BLOCK_BITS)
        words = (soft < 0) @ BIT_WEIGHTS
        syndromes = np.fromiter((checkword(int(word)) for word in words.flat), dtype=np.int32).reshape(words.shape)
        first_word, first_exact = recover_word(words[:, 0], syndromes[:, 0], soft[:, 0], OFFSETS[0])
        control, control_exact = recover_word(words[:, 1], syndromes[:, 1], soft[:, 1], OFFSETS[1])
        third_offset = OFFSET_C_PRIME if control is not None and control & 0x800 else OFFSETS[2]
        if not control_exact and not np.any(syndromes[:, 2] == third_offset):
            # With an uncertain block B, prefer any exact C or C' candidate.
            other_offset = OFFSETS[2] if third_offset == OFFSET_C_PRIME else OFFSET_C_PRIME
            if np.any(syndromes[:, 2] == other_offset):
                third_offset = other_offset
        third, third_exact = recover_word(words[:, 2], syndromes[:, 2], soft[:, 2], third_offset)
        fourth, fourth_exact = recover_word(words[:, 3], syndromes[:, 3], soft[:, 3], OFFSETS[3])
        blocks = ((first_word, first_exact), (control, control_exact), (third, third_exact), (fourth, fourth_exact))
        passed = sum(exact for _, exact in blocks)
        rejected = sum(word is None for word, _ in blocks)
        self.block_quality.append((end, passed, len(blocks) - passed - rejected, rejected))
        identities = words[syndromes[:, 0] == OFFSETS[0], 0] >> 10
        if len(identities) and self.station.pi not in identities:
            return
        if control is None or control >> 12 not in (0, 2):
            return
        if control >> 12 == 0 and not control & 0x800 and control_exact and third_exact and third_offset == OFFSETS[2]:
            self.station.alternatives.add(third)
        if third_offset != OFFSETS[2] or (control >> 12 == 2 and control & 0x800):
            third, third_exact = None, False
        center = SAMPLES_PER_BIT // 2
        data_soft = np.mean(soft[center - 2:center + 3, 2:], axis=0)
        self.station.text.add(control, third, fourth, control_exact, third_exact, fourth_exact,
                              pi_exact=self.station.pi in identities, soft=data_soft)

    def snapshot(self):
        _, best = self.best_station()
        state = best.snapshot()
        if self.station.pi is not None:
            pi = f"{self.station.pi:04X}"
            same_station = state["pi"] == pi
            text = self.station.text.snapshot()
            if same_station and text["ps"] is None:
                text["ps"] = state["ps"]
            state.update(text, pi=pi, locked=same_station and state["locked"])
            if not same_station:
                state.update(pty=None, tp=None, ta=None)
        state["alternative_frequencies_hz"] = self.station.alternatives.snapshot()
        state["block_quality"] = None
        if self.block_quality:
            _, passed, corrected, rejected = map(sum, zip(*self.block_quality))
            state["block_quality"] = {"window_seconds": QUALITY_SECONDS,
                                      "passed": passed, "corrected": corrected, "rejected": rejected}
        return state
