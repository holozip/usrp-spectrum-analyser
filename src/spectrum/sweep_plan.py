"""Frequency grids and capture plans for swept and continuous acquisition."""
import numpy as np

LOW, HIGH = 70e6, 6e9
DISPLAY_BINS = 4096
BINS_PER_OVERVIEW = 384
DETAIL_BINS = DISPLAY_BINS * BINS_PER_OVERVIEW
OVERVIEW_WIDTH = (HIGH - LOW) / DISPLAY_BINS
DETAIL_WIDTH = (HIGH - LOW) / DETAIL_BINS
FFT_SIZE = 16384


def captures(rate, bandwidth, paired=False):
    # Keep clear of both filter edges and discard the immediate LO/DC region.
    edge = 0.35 * min(rate, bandwidth)
    size = FFT_SIZE
    dc_gap = 0.01 * rate
    offsets = np.fft.fftshift(np.fft.fftfreq(size, 1 / rate))
    usable = (np.abs(offsets) <= edge) & (np.abs(offsets) >= dc_gap)
    # Put the first and last display bins inside the retained FFT band.
    margin = edge - rate / size
    # The neighboring hop must cover the entire DC gap, including its far end.
    step = margin - dc_gap
    span = HIGH - LOW - 2 * margin
    if paired:
        # The close neighbor covers each DC gap. Across pairs, keep the
        # shared passband wider than the FFT spacing and allow for filter roll-off.
        wide = 2 * (margin - dc_gap)
        pairs = int(np.ceil((span + wide) / (step + wide)))
        increments = np.resize([step, wide], 2 * pairs - 1)
        positions = np.r_[0, np.cumsum(increments)]
        centers = LOW + margin + positions * (span / positions[-1])
    else:
        count = int(np.ceil(span / step)) + 1
        centers = np.linspace(LOW + margin, HIGH - margin, count)
    return centers, offsets, usable


def offset_hop_plan(centers, shift=5e6):
    # Move both hops in each pair together and pin the pairs at the band edges.
    pair = np.arange(len(centers) // 2)
    taper = np.minimum(1, np.minimum(pair, pair[-1] - pair) / 6)
    return centers + np.repeat(shift * taper, 2)


def scan_range(centers, focus, paired=False):
    if focus is None:
        return LOW, HIGH, 0, DISPLAY_BINS, centers
    start, stop = focus
    # Align focused sweeps to the full-sweep display grid.
    first = max(0, min(DISPLAY_BINS - 1, int(np.floor((start - LOW) / OVERVIEW_WIDTH))))
    end = min(DISPLAY_BINS, max(first + 1, int(np.ceil((stop - LOW) / OVERVIEW_WIDTH))))
    low, high = LOW + first * OVERVIEW_WIDTH, LOW + end * OVERVIEW_WIDTH
    # Bracket the visible span with adjacent centers so they fill one another's DC gaps.
    left = max(0, min(len(centers) - 2, int(centers.searchsorted(low)) - 1))
    right = min(len(centers), max(left + 2, int(centers.searchsorted(high, side="right")) + 1))
    if paired:
        # Keep the partner of every retained center even at focused-span edges.
        left -= left % 2
        right += right % 2
    return low, high, first, end - first, centers[left:right]


def continuous_plan(focus, rate, bandwidth):
    if focus is None or rate != 56e6 or focus[1] - focus[0] > min(rate, bandwidth):
        return None
    # Keep the usual display grid and let pairs of bins form the overview.
    first = max(0, 2 * int(np.floor((focus[0] - LOW) / (2 * DETAIL_WIDTH))))
    end = min(DETAIL_BINS, 2 * int(np.ceil((focus[1] - LOW) / (2 * DETAIL_WIDTH))))
    low, high = LOW + first * DETAIL_WIDTH, LOW + end * DETAIL_WIDTH
    # Center the selection between the receiver's DC gap and its filter edge.
    margin = 3 * rate / FFT_SIZE
    inner = 0.01 * rate + margin
    outer = 0.35 * min(rate, bandwidth) - margin
    if high <= low or high - low > outer - inner:
        # A wide view exposes the full FFT, including DC and filter roll-off.
        center = (focus[0] + focus[1]) / 2 + rate / (2 * FFT_SIZE)
        return center, None, None, None, FFT_SIZE
    gap = (inner + outer - (high - low)) / 2
    center = low - gap if low - gap >= LOW else high + gap
    return center, low, high, first, end - first
