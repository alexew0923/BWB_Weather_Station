"""Small, robust statistical helpers shared by every analysis stage.

Robust rather than Gaussian throughout: this dataset contains stuck sensors,
corrupt frames and hard ADC rails, all of which wreck a mean and a standard
deviation while leaving a median and a MAD intact.
"""

import numpy as np
import pandas as pd

from .models import SignalStatistics

#: Scale factor that makes the median absolute deviation a consistent estimator
#: of the standard deviation for normally distributed data.
MAD_TO_SIGMA = 1.4826


def clean(values):
    """Return finite values as a 1-D float array."""
    array = np.asarray(pd.Series(values, dtype="float64").to_numpy(), dtype="float64")
    return array[np.isfinite(array)]


def robust_sigma(values, floor=0.0):
    """A standard-deviation-equivalent scale from the median absolute deviation.

    Returns ``floor`` when the sample is too small or perfectly flat. A flat
    sample is common here -- the wetness channel sits pinned at its dry rail for
    82% of the record -- and a zero scale would make every deviation infinitely
    significant, so callers must always supply a meaningful floor.
    """
    array = clean(values)
    if array.size < 2:
        return float(floor)
    mad = float(np.median(np.abs(array - np.median(array))))
    return max(float(mad * MAD_TO_SIGMA), float(floor))


def robust_z(value, values, floor=0.0):
    """Robust standardised deviation of ``value`` against a reference sample."""
    array = clean(values)
    if array.size < 2 or value is None or not np.isfinite(value):
        return None
    sigma = robust_sigma(array, floor=floor)
    if sigma <= 0:
        return None
    return float((value - np.median(array)) / sigma)


def percentile_of(value, values):
    """The percentile ``value`` occupies within ``values`` (0-100)."""
    array = clean(values)
    if array.size == 0 or value is None or not np.isfinite(value):
        return None
    return float(100.0 * np.mean(array <= value))


def describe(values, sigma_floor=0.0):
    """Summarise a signal with robust and conventional statistics."""
    series = pd.Series(values, dtype="float64")
    array = clean(series)
    if array.size == 0:
        return SignalStatistics(count=int(len(series)), valid_count=0)
    return SignalStatistics(
        count=int(len(series)),
        valid_count=int(array.size),
        minimum=float(np.min(array)),
        maximum=float(np.max(array)),
        mean=float(np.mean(array)),
        median=float(np.median(array)),
        p10=float(np.percentile(array, 10)),
        p25=float(np.percentile(array, 25)),
        p75=float(np.percentile(array, 75)),
        p90=float(np.percentile(array, 90)),
        robust_sigma=robust_sigma(array, floor=sigma_floor),
        standard_deviation=float(np.std(array, ddof=1)) if array.size > 1 else 0.0,
    )


def longest_run(mask):
    """Length and start index of the longest run of True in a boolean array."""
    array = np.asarray(mask, dtype=bool)
    if array.size == 0 or not array.any():
        return 0, None
    best_length = 0
    best_start = None
    length = 0
    start = 0
    for index, flag in enumerate(array):
        if flag:
            if length == 0:
                start = index
            length += 1
            if length > best_length:
                best_length, best_start = length, start
        else:
            length = 0
    return best_length, best_start


def elapsed_minutes(start, end):
    """Real elapsed minutes between two timezone-aware instants."""
    return (pd.Timestamp(end) - pd.Timestamp(start)).total_seconds() / 60.0
