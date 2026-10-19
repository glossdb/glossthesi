"""How often the forecast's bands held on held-out data: the table a
caller reads before handing out a range, served on every `/forecast`
answer as `coverage` (`docs/contract.md`).

Per grain, at every step glossql asks (12 months, 13 weeks, 31 days,
4 quarters) and at every span up to that horizon: the share of actual
values inside the 80% interval (levels 0.10 to 0.90) and inside the
90% interval (0.05 to 0.95), the points behind the share and its
standard error, clustered by series and by origin, with how many
datasets the share pools, so that a caller's rule can tell 0.75 ± 0.05
from 0.80 ± 0.01. Measured
by rolling-origin backtests through this route on datasets the set-up
was not chosen on (`docs/forecast.md`, GK-46): the evaluation's sealed
runs of 2026-10-13 (the steps of every grain, the spans of months and
weeks) and 2026-10-15 (the spans of days and quarters, at the
dependence measured for them, GK-48). A step's share is the model's
own. A span's share depends on the dependence the total's interval was
combined with (`forecast.DEPENDENCE`), so each grain's spans record the
value they were measured at, and they are served only while the service
combines with that value: a grain whose dependence differs serves its
steps and no spans until the table is measured again. The day's and the
quarter's dependence was fitted on the same datasets (GK-48), so their
spans' shares are fits, not held-out readings; the answer's
`assumptions` names the basis per grain.

A share pooled over many datasets is a default. A caller with its own
record of forecasts and what then happened reads that first."""

from __future__ import annotations

from . import forecast

# The bands measured, by the levels that bound them, in the order of the tuples below.
BANDS = ((0.10, 0.90), (0.05, 0.95))
# What the spans of each grain were combined with when the table was measured.
MEASURED_AT = {"month": 0.43, "week": 0.22, "day": 0.12, "quarter": 0.25}
# Per grain, how many held-out datasets the shares pool.
DATASETS = {"month": 4, "week": 2, "day": 3, "quarter": 4}
# Per grain: at each step, then at each span, (share inside the 80% interval, its standard error, share inside the 90%
# interval, its standard error, points). The error is clustered by series and by origin, the larger (GK-46).
HELD = {
    "month": {
        "steps": [
            (0.751, 0.049, 0.874, 0.042, 603),
            (0.719, 0.074, 0.805, 0.069, 604),
            (0.76, 0.063, 0.87, 0.047, 601),
            (0.749, 0.059, 0.869, 0.044, 605),
            (0.683, 0.067, 0.807, 0.05, 605),
            (0.729, 0.058, 0.831, 0.043, 602),
            (0.721, 0.051, 0.85, 0.038, 606),
            (0.684, 0.062, 0.792, 0.055, 605),
            (0.715, 0.06, 0.811, 0.051, 604),
            (0.701, 0.066, 0.804, 0.05, 608),
            (0.675, 0.062, 0.786, 0.055, 607),
            (0.67, 0.064, 0.776, 0.055, 606),
        ],
        "spans": [
            (0.7, 0.057, 0.849, 0.05, 477),
            (0.684, 0.077, 0.792, 0.078, 475),
            (0.722, 0.083, 0.79, 0.079, 472),
            (0.742, 0.084, 0.799, 0.077, 472),
            (0.711, 0.088, 0.79, 0.077, 471),
            (0.708, 0.089, 0.785, 0.076, 469),
            (0.719, 0.089, 0.783, 0.078, 469),
            (0.699, 0.088, 0.765, 0.08, 468),
            (0.709, 0.09, 0.771, 0.078, 467),
            (0.696, 0.092, 0.782, 0.075, 467),
            (0.682, 0.09, 0.792, 0.074, 466),
            (0.67, 0.089, 0.794, 0.073, 466),
        ],
    },
    "week": {
        "steps": [
            (0.807, 0.014, 0.896, 0.011, 720),
            (0.817, 0.021, 0.921, 0.015, 720),
            (0.81, 0.014, 0.921, 0.01, 720),
            (0.787, 0.018, 0.901, 0.012, 720),
            (0.806, 0.02, 0.904, 0.013, 720),
            (0.818, 0.015, 0.922, 0.01, 720),
            (0.812, 0.02, 0.907, 0.016, 720),
            (0.8, 0.017, 0.912, 0.015, 720),
            (0.803, 0.022, 0.91, 0.013, 720),
            (0.839, 0.018, 0.929, 0.01, 720),
            (0.819, 0.014, 0.928, 0.009, 720),
            (0.8, 0.018, 0.894, 0.014, 720),
            (0.803, 0.019, 0.912, 0.011, 720),
        ],
        "spans": [
            (0.807, 0.014, 0.896, 0.011, 720),
            (0.787, 0.018, 0.904, 0.014, 720),
            (0.808, 0.016, 0.922, 0.011, 720),
            (0.789, 0.018, 0.922, 0.009, 720),
            (0.801, 0.021, 0.91, 0.013, 720),
            (0.808, 0.015, 0.914, 0.012, 720),
            (0.829, 0.017, 0.929, 0.012, 720),
            (0.836, 0.017, 0.935, 0.012, 720),
            (0.832, 0.017, 0.925, 0.011, 720),
            (0.832, 0.016, 0.928, 0.012, 720),
            (0.836, 0.015, 0.932, 0.011, 720),
            (0.85, 0.015, 0.938, 0.009, 720),
            (0.849, 0.016, 0.932, 0.01, 720),
        ],
    },
    "day": {
        "steps": [
            (0.793, 0.019, 0.907, 0.014, 653),
            (0.773, 0.026, 0.862, 0.024, 739),
            (0.771, 0.029, 0.87, 0.022, 772),
            (0.744, 0.035, 0.852, 0.026, 776),
            (0.708, 0.043, 0.823, 0.027, 780),
            (0.759, 0.026, 0.851, 0.022, 777),
            (0.744, 0.033, 0.833, 0.023, 777),
            (0.735, 0.04, 0.844, 0.036, 736),
            (0.741, 0.037, 0.847, 0.028, 780),
            (0.767, 0.035, 0.856, 0.031, 778),
            (0.751, 0.03, 0.864, 0.026, 778),
            (0.762, 0.037, 0.832, 0.034, 781),
            (0.754, 0.035, 0.857, 0.028, 777),
            (0.756, 0.03, 0.845, 0.023, 737),
            (0.8, 0.025, 0.873, 0.019, 735),
            (0.755, 0.028, 0.853, 0.021, 781),
            (0.735, 0.042, 0.823, 0.039, 774),
            (0.756, 0.026, 0.845, 0.022, 775),
            (0.754, 0.029, 0.86, 0.02, 781),
            (0.78, 0.031, 0.877, 0.02, 779),
            (0.777, 0.029, 0.865, 0.025, 773),
            (0.761, 0.03, 0.862, 0.021, 777),
            (0.74, 0.042, 0.832, 0.038, 776),
            (0.693, 0.052, 0.789, 0.045, 769),
            (0.705, 0.052, 0.81, 0.046, 684),
            (0.757, 0.033, 0.845, 0.028, 734),
            (0.773, 0.03, 0.864, 0.026, 774),
            (0.75, 0.032, 0.833, 0.028, 780),
            (0.742, 0.034, 0.833, 0.028, 778),
            (0.698, 0.044, 0.807, 0.035, 778),
            (0.741, 0.042, 0.847, 0.027, 737),
        ],
        "spans": [
            (0.793, 0.019, 0.907, 0.014, 653),
            (0.798, 0.021, 0.893, 0.017, 609),
            (0.795, 0.021, 0.903, 0.013, 601),
            (0.803, 0.02, 0.897, 0.016, 595),
            (0.781, 0.023, 0.879, 0.02, 593),
            (0.772, 0.023, 0.891, 0.017, 589),
            (0.777, 0.025, 0.872, 0.015, 587),
            (0.76, 0.033, 0.866, 0.019, 546),
            (0.767, 0.026, 0.877, 0.02, 545),
            (0.785, 0.027, 0.89, 0.02, 544),
            (0.781, 0.028, 0.897, 0.019, 544),
            (0.785, 0.03, 0.895, 0.019, 544),
            (0.802, 0.03, 0.902, 0.021, 540),
            (0.789, 0.031, 0.903, 0.021, 503),
            (0.792, 0.033, 0.887, 0.023, 461),
            (0.781, 0.033, 0.898, 0.021, 461),
            (0.772, 0.039, 0.886, 0.027, 457),
            (0.786, 0.036, 0.885, 0.025, 454),
            (0.784, 0.038, 0.896, 0.027, 454),
            (0.792, 0.035, 0.905, 0.026, 453),
            (0.787, 0.034, 0.904, 0.024, 450),
            (0.784, 0.03, 0.909, 0.025, 449),
            (0.77, 0.03, 0.894, 0.024, 444),
            (0.774, 0.03, 0.907, 0.024, 439),
            (0.776, 0.03, 0.893, 0.025, 429),
            (0.788, 0.03, 0.897, 0.024, 429),
            (0.791, 0.03, 0.904, 0.026, 425),
            (0.791, 0.03, 0.901, 0.027, 425),
            (0.795, 0.029, 0.906, 0.023, 424),
            (0.791, 0.028, 0.903, 0.022, 421),
            (0.793, 0.03, 0.905, 0.022, 420),
        ],
    },
    "quarter": {
        "steps": [
            (0.809, 0.014, 0.905, 0.01, 864),
            (0.817, 0.016, 0.909, 0.011, 864),
            (0.793, 0.017, 0.899, 0.011, 864),
            (0.797, 0.02, 0.894, 0.015, 864),
        ],
        "spans": [
            (0.809, 0.014, 0.905, 0.01, 864),
            (0.789, 0.018, 0.902, 0.012, 864),
            (0.789, 0.02, 0.897, 0.013, 864),
            (0.8, 0.02, 0.888, 0.017, 864),
        ],
    },
}


# Per grain, what the shares rest on: the datasets pooled, the histories behind the origins, and what the table does
# not carry (docs/forecast.md, GK-46, GK-48; the evaluation's results page holds the error bars per step).
MEASURED_ON = {
    "month": "measured: pooled over 4 held-out datasets, 69 monthly series (a headcount, product reviews, clinical trials), two years of history behind every origin; a share carries a clustered standard error of 0.05 to 0.09, served beside it, and no step reaches 0.80 (GK-46)",
    "week": "measured: pooled over 2 held-out datasets, 80 weekly series (a fashion retailer, a grocery), two years of history behind every origin; a share carries a clustered standard error of 0.01 to 0.02 (GK-46)",
    "day": "measured: pooled over 3 held-out datasets, 87 daily series (cash machines, a grocery delivery's warehouses, a grocery), a year of history behind every origin; standard error 0.02 to 0.04; the month's total at a dependence fitted on the same data (GK-46, GK-48)",
    "quarter": "measured: pooled over 4 held-out datasets, 96 quarterly series (two tourism sets with 80 of the series, clinical trials), two years of history behind every origin; standard error 0.01 to 0.02; the year's total at a dependence fitted on the same data (GK-46, GK-48)",
}


def basis(grain: str) -> dict:
    """What the served shares rest on, for the answer's `assumptions`: the datasets they pool, the histories behind
    them, and the error the table does not carry. A caller whose series are shorter, mostly zero or unlike these
    datasets reads the shares as a default, not as its own."""
    return {"field": "coverage", "grain": grain, "basis": MEASURED_ON[grain]}


def _entry(step: int, span: int | None, band: int, row: tuple, grain: str) -> dict:
    return {
        "step": step,
        "span": span,
        "low": BANDS[band][0],
        "high": BANDS[band][1],
        "share": row[2 * band],
        "se": row[2 * band + 1],
        "points": row[4],
        "datasets": DATASETS[grain],
    }


def held(grain: str, horizon: int, spans: list[int], levels: list[float]) -> list[dict]:
    """The `coverage` entries of an answer: per step up to `horizon`
    and per span in `spans`, for each band in `BANDS` whose two levels
    the request asked, the share that held, its standard error, the points behind it and the datasets pooled.
    A step beyond the table, a band the request's levels do not form,
    or a span at a dependence other than the one the spans were
    measured at has no entry."""
    table = HELD.get(grain)
    if table is None:
        return []
    asked = [i for i, (low, high) in enumerate(BANDS) if any(abs(level - low) < 1e-9 for level in levels) and any(abs(level - high) < 1e-9 for level in levels)]
    out = []
    for step in range(1, min(horizon, len(table["steps"])) + 1):
        row = table["steps"][step - 1]
        out += [_entry(step, None, i, row, grain) for i in asked]
    if abs(forecast.DEPENDENCE[grain] - MEASURED_AT[grain]) < 1e-9:
        for span in sorted(set(spans)):
            if 1 <= span <= len(table["spans"]):
                row = table["spans"][span - 1]
                out += [_entry(span, span, i, row, grain) for i in asked]
    return out
