"""How often the forecast's bands held on held-out data: the table a
caller reads before handing out a range, served on every `/forecast`
answer as `coverage` (`docs/contract.md`).

Per grain, at every step glossql asks (12 months, 13 weeks, 31 days,
4 quarters) and at every span up to that horizon: the share of actual
values inside the 80% interval (levels 0.10 to 0.90) and inside the
90% interval (0.05 to 0.95), and the points behind the share. Measured
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
# Per grain: at each step, then at each span, (share inside the 80% interval, share inside the 90% interval, points).
HELD = {
    "month": {
        "steps": [
            (0.751, 0.874, 603), (0.719, 0.805, 604), (0.76, 0.87, 601), (0.749, 0.869, 605), (0.683, 0.807, 605),
            (0.729, 0.831, 602), (0.721, 0.85, 606), (0.684, 0.792, 605), (0.715, 0.811, 604), (0.701, 0.804, 608),
            (0.675, 0.786, 607), (0.67, 0.776, 606),
        ],
        "spans": [
            (0.7, 0.849, 477), (0.684, 0.792, 475), (0.722, 0.79, 472), (0.742, 0.799, 472), (0.711, 0.79, 471),
            (0.708, 0.785, 469), (0.719, 0.783, 469), (0.699, 0.765, 468), (0.709, 0.771, 467), (0.696, 0.782, 467),
            (0.682, 0.792, 466), (0.67, 0.794, 466),
        ],
    },
    "week": {
        "steps": [
            (0.807, 0.896, 720), (0.817, 0.921, 720), (0.81, 0.921, 720), (0.787, 0.901, 720), (0.806, 0.904, 720),
            (0.818, 0.922, 720), (0.812, 0.907, 720), (0.8, 0.912, 720), (0.803, 0.91, 720), (0.839, 0.929, 720),
            (0.819, 0.928, 720), (0.8, 0.894, 720), (0.803, 0.912, 720),
        ],
        "spans": [
            (0.807, 0.896, 720), (0.787, 0.904, 720), (0.808, 0.922, 720), (0.789, 0.922, 720), (0.801, 0.91, 720),
            (0.808, 0.914, 720), (0.829, 0.929, 720), (0.836, 0.935, 720), (0.832, 0.925, 720), (0.832, 0.928, 720),
            (0.836, 0.932, 720), (0.85, 0.938, 720), (0.849, 0.932, 720),
        ],
    },
    "day": {
        "steps": [
            (0.793, 0.907, 653), (0.773, 0.862, 739), (0.771, 0.87, 772), (0.744, 0.852, 776), (0.708, 0.823, 780),
            (0.759, 0.851, 777), (0.744, 0.833, 777), (0.735, 0.844, 736), (0.741, 0.847, 780), (0.767, 0.856, 778),
            (0.751, 0.864, 778), (0.762, 0.832, 781), (0.754, 0.857, 777), (0.756, 0.845, 737), (0.8, 0.873, 735),
            (0.755, 0.853, 781), (0.735, 0.823, 774), (0.756, 0.845, 775), (0.754, 0.86, 781), (0.78, 0.877, 779),
            (0.777, 0.865, 773), (0.761, 0.862, 777), (0.74, 0.832, 776), (0.693, 0.789, 769), (0.705, 0.81, 684),
            (0.757, 0.845, 734), (0.773, 0.864, 774), (0.75, 0.833, 780), (0.742, 0.833, 778), (0.698, 0.807, 778),
            (0.741, 0.847, 737),
        ],
        "spans": [
            (0.793, 0.907, 653), (0.798, 0.893, 609), (0.795, 0.903, 601), (0.803, 0.897, 595), (0.781, 0.879, 593),
            (0.772, 0.891, 589), (0.777, 0.872, 587), (0.76, 0.866, 546), (0.767, 0.877, 545), (0.785, 0.89, 544),
            (0.781, 0.897, 544), (0.785, 0.895, 544), (0.802, 0.902, 540), (0.789, 0.903, 503), (0.792, 0.887, 461),
            (0.781, 0.898, 461), (0.772, 0.886, 457), (0.786, 0.885, 454), (0.784, 0.896, 454), (0.792, 0.905, 453),
            (0.787, 0.904, 450), (0.784, 0.909, 449), (0.77, 0.894, 444), (0.774, 0.907, 439), (0.776, 0.893, 429),
            (0.788, 0.897, 429), (0.791, 0.904, 425), (0.791, 0.901, 425), (0.795, 0.906, 424), (0.791, 0.903, 421),
            (0.793, 0.905, 420),
        ],
    },
    "quarter": {
        "steps": [
            (0.809, 0.905, 864), (0.817, 0.909, 864), (0.793, 0.899, 864), (0.797, 0.894, 864),
        ],
        "spans": [
            (0.809, 0.905, 864), (0.789, 0.902, 864), (0.789, 0.897, 864), (0.8, 0.888, 864),
        ],
    },
}


def held(grain: str, horizon: int, spans: list[int], levels: list[float]) -> list[dict]:
    """The `coverage` entries of an answer: per step up to `horizon`
    and per span in `spans`, for each band in `BANDS` whose two levels
    the request asked, the share that held and the points behind it.
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
        out += [{"step": step, "span": None, "low": BANDS[i][0], "high": BANDS[i][1], "share": row[i], "points": row[2]} for i in asked]
    if abs(forecast.DEPENDENCE[grain] - MEASURED_AT[grain]) < 1e-9:
        for span in sorted(set(spans)):
            if 1 <= span <= len(table["spans"]):
                row = table["spans"][span - 1]
                out += [{"step": span, "span": span, "low": BANDS[i][0], "high": BANDS[i][1], "share": row[i], "points": row[2]} for i in asked]
    return out
