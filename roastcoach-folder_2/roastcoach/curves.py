"""
Per-roast time series: temperatures, rate of rise, and control settings.

Refactor of the notebook's ``create_roast_samples`` with three bugs fixed:

1. Control columns were created with numeric names (0/1/2) and then looked up
   by name, and the control lookup used ``codes_by_control`` where it needed
   ``controls_by_code`` -- so power/fan/drum came out empty.
2. Control action indices are indices into the *full rate* sample array, so
   they must be divided by the sample drop factor before being applied to the
   downsampled frame.
3. Rate of rise was multiplied by the drop factor an extra time, which doubled
   every C/min value.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fields import codes_by_control, controls_by_code, roast_sample_fields

DEFAULT_DROP_FACTOR = 2
DEFAULT_TEMP_SPAN = 7
DEFAULT_RATE_FRACTION = 0.1

# Event columns that mark a moment in the roast, and how to label them.
EVENT_INDEX_FIELDS = [
    ("roastStartIndex", "Charge"),
    ("indexYellowingStart", "Yellowing"),
    ("indexFirstCrackStart", "1C start"),
    ("indexFirstCrackEnd", "1C end"),
    ("indexSecondCrackStart", "2C start"),
    ("indexSecondCrackEnd", "2C end"),
    ("roastEndIndex", "Drop"),
]


def _lowess(y: pd.Series, x: np.ndarray, frac: float) -> pd.Series:
    """LOWESS smoothing that keeps its original index alignment."""
    try:
        import statsmodels.api as sm
    except ImportError:  # statsmodels is optional
        return y.rolling(window=max(3, int(len(y) * frac)), center=True, min_periods=1).mean()

    mask = y.notna().values
    smoothed = pd.Series(np.nan, index=y.index, dtype="float64")
    if mask.sum() < 5:
        return y
    fitted = sm.nonparametric.lowess(y.values[mask], x[mask], frac=frac, return_sorted=False)
    smoothed.loc[y.index[mask]] = fitted
    return smoothed


def set_roast_samples_controls(roast_samples: pd.DataFrame, actions, drop_factor: int) -> None:
    """Expand RoasTime's sparse control-change events into per-sample columns."""
    for control_name in codes_by_control:
        roast_samples[control_name] = np.nan

    control_values: dict[str, tuple[int, float]] = {}
    for action in actions or []:
        control_name = controls_by_code.get(action.get("ctrlType"))
        if control_name is None:
            continue

        action_index = int(action.get("index", 0)) // drop_factor
        value = action.get("value")

        if control_name not in control_values:
            control_values[control_name] = (max(action_index, 0), value)
            continue

        prior_index, prior_value = control_values[control_name]
        control_loc = roast_samples.columns.get_loc(control_name)
        roast_samples.iloc[prior_index:action_index, control_loc] = prior_value
        control_values[control_name] = (action_index, value)

    for control_name, (index, value) in control_values.items():
        control_loc = roast_samples.columns.get_loc(control_name)
        roast_samples.iloc[index:, control_loc] = value


def create_roast_samples(
    roast_json: dict,
    drop_factor: int = DEFAULT_DROP_FACTOR,
    temp_span: int = DEFAULT_TEMP_SPAN,
    rate_fraction: float = DEFAULT_RATE_FRACTION,
    prefer_recorded_ror: bool = True,
) -> pd.DataFrame:
    """Build the smoothed time series for a single roast.

    ``prefer_recorded_ror`` uses the machine's own rate-of-rise series when the
    roast carries one for *both* sensors -- CSV exports do. Differencing a 1 Hz
    temperature column is much noisier than what RoasTime recorded live. When
    only one sensor has a recorded series, both are computed instead, so the two
    curves are always derived the same way.
    """
    drop_factor = max(1, int(drop_factor))

    series = {f: roast_json.get(f) or [] for f in roast_sample_fields}
    if roast_json.get("drumDerivative"):
        series["drumDerivative"] = roast_json["drumDerivative"]
    lengths = [len(v) for v in series.values() if v]
    if not lengths:
        return pd.DataFrame()
    n = min(lengths)
    if n < 2:
        return pd.DataFrame()

    frame = pd.DataFrame({f: pd.to_numeric(pd.Series(v[:n]), errors="coerce") for f, v in series.items() if v})
    samples = frame.iloc[::drop_factor, :].reset_index(drop=True).copy()
    if len(samples) < 3:
        return pd.DataFrame()

    sample_rate = roast_json.get("sampleRate") or 1
    sample_period = drop_factor / sample_rate  # seconds between retained samples
    samples["time_seconds"] = np.arange(len(samples)) * sample_period
    samples["time_minutes"] = samples["time_seconds"] / 60.0
    samples["uid"] = roast_json.get("uid")

    for source, target in (
        ("beanTemperature", "smoothBeanTemperature"),
        ("drumTemperature", "smoothDrumTemperature"),
    ):
        if source in samples:
            samples[target] = samples[source].ewm(span=temp_span).mean()

    x = samples["time_seconds"].values
    use_recorded = bool(
        prefer_recorded_ror
        and "beanDerivative" in samples
        and "drumDerivative" in samples
        and samples["beanDerivative"].notna().any()
        and samples["drumDerivative"].notna().any()
    )

    for temperature, recorded, raw, smooth in (
        ("beanTemperature", "beanDerivative", "rawBeanDerivative", "smoothBeanDerivative"),
        ("drumTemperature", "drumDerivative", "rawDrumDerivative", "smoothDrumDerivative"),
    ):
        if temperature not in samples:
            continue
        if use_recorded:
            samples[raw] = samples[recorded]
        else:
            samples[raw] = samples[temperature].diff() * 60.0 / sample_period
        samples[smooth] = _lowess(samples[raw], x, rate_fraction)

    # Second derivative of the IBTS rate of rise: where the roast is accelerating
    # or braking. The original project computed this as a plain diff of the RoR.
    if "smoothDrumDerivative" in samples:
        samples["secondDerivative"] = samples["smoothDrumDerivative"].diff() / (sample_period / 60.0)
        samples["secondDerivative"] = samples["secondDerivative"].ewm(span=max(3, temp_span)).mean()

    samples.attrs["ror_source"] = "recorded" if use_recorded else "computed"

    set_roast_samples_controls(samples, (roast_json.get("actions") or {}).get("actionTimeList"), drop_factor)

    return samples


def roast_events(roast_json: dict) -> list[tuple[str, float]]:
    """(label, seconds) for every event the roast actually recorded."""
    sample_rate = roast_json.get("sampleRate") or 1
    found = []
    for field, label in EVENT_INDEX_FIELDS:
        index = roast_json.get(field)
        if index is None or index <= 0:
            continue
        found.append((label, index / sample_rate))
    return found
