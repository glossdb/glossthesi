"""The what-if without the model (`docs/whatif.md`): how a series'
history is read."""

import numpy as np

from glossthesi import whatif


def _weekly(seed: int = 0, periods: int = 160):
    """A weekly series: y answers x with a slope of 2 and z not at all, over a yearly cycle and noise."""
    rng = np.random.default_rng(seed)
    stamps = (np.datetime64("2019-01-06") + np.arange(periods) * np.timedelta64(7, "D")).astype("datetime64[ms]")
    x, z = rng.uniform(0, 1, periods), rng.uniform(0, 1, periods)
    y = 50.0 + 2.0 * x + 3.0 * np.sin(2 * np.pi * np.arange(periods) / 52.18) + rng.normal(0, 0.3, periods)
    return stamps, y, x, z


def test_the_history_gives_the_slope_with_the_other_inputs_and_the_season_held():
    starts = whatif.places(np.random.default_rng(1))
    slopes, inside = [], []
    for seed in range(6):
        stamps, y, x, z = _weekly(seed)
        got = whatif.history(stamps, y, np.column_stack([x, z]), starts)
        slopes.append(got["slope"])
        lo, hi = np.quantile(got["draws"][:, 0], [0.05, 0.95])
        inside.append(lo < 2.0 < hi)
        assert got["free"][0] > 0.9 and got["explains"][0] > 0.5 and abs(got["slope_on_changes"][0] - 2.0) < 0.4
    # 2 per unit of x, nothing per unit of z, each inside its resampled interval on most series.
    assert np.allclose(np.array(slopes)[:, 0], 2.0, atol=0.25) and np.allclose(np.array(slopes)[:, 1], 0.0, atol=0.25) and np.mean(inside) >= 0.8
    # An input that only repeats another cannot be told apart from it.
    stamps, y, x, _ = _weekly()
    assert (whatif.history(stamps, y, np.column_stack([x, 3.0 * x + 1.0]), starts)["free"] < whatif.SEPARABLE).all()
    # A history too short to read leaves every input unread.
    assert np.isnan(whatif.history(stamps[:12], y[:12], np.column_stack([x[:12]]), starts)["slope"]).all()


def test_a_daily_series_holds_the_day_of_the_week_and_draws_whole_weeks():
    # Promotions only on weekdays, sales higher on weekdays anyway: the day of the week must be held.
    rng = np.random.default_rng(3)
    stamps = (np.datetime64("2021-01-04") + np.arange(400) * np.timedelta64(1, "D")).astype("datetime64[ms]")  # a Monday
    weekday = np.arange(400) % 7 < 5
    x = (weekday & (rng.uniform(size=400) < 0.5)).astype(np.float64)
    y = 10.0 + 4.0 * weekday + 1.5 * x + rng.normal(0, 0.3, 400)
    got = whatif.history(stamps, y, np.column_stack([x]), whatif.places(rng))
    assert abs(got["slope"][0] - 1.5) < 0.2 and 0.3 < got["free"][0] < 0.95 and got["explains"][0] > 0.5
    assert whatif.run_of_rows(whatif.minutes(stamps)) == 14


def test_a_drift_two_columns_merely_share_does_not_show_in_the_changes():
    # Found on real tables: promotions "moved" the world's oil price, because both drifted over the same years.
    rng = np.random.default_rng(5)
    stamps = _weekly(periods=240)[0]
    on_rows, on_changes, answered = [], [], []
    for _ in range(8):
        x = np.cumsum(rng.normal(0, 0.1, 240)) + 0.01 * np.arange(240)
        answers = 50.0 + 2.0 * x + rng.normal(0, 0.3, 240)  # this column answers x
        drifts = 20.0 + np.cumsum(rng.normal(0, 0.3, 240))  # this one drifts on its own
        starts = whatif.places(rng)
        answered.append(whatif.history(stamps, answers, np.column_stack([x]), starts)["slope_on_changes"][0])
        got = whatif.history(stamps, drifts, np.column_stack([x]), starts)
        on_rows.append(abs(got["slope"][0]))
        on_changes.append(abs(got["slope_on_changes"][0]))
    assert abs(np.median(answered) - 2.0) < 0.3
    # On the rows the drifting column shows a slope somewhere, in one direction or the other; on the changes it shows none.
    assert np.median(on_changes) < 0.5 < np.median(on_rows)


def _member(stamps, y, inputs, on, ahead=13):
    """The last `ahead` periods as the plan, and `on` raised there inside what its history holds."""
    cut = len(stamps) - ahead
    plan = {c: v[cut:] for c, v in inputs.items()}
    scenario = plan | {on: np.minimum(plan[on] + 0.2, np.nanmax(inputs[on][:cut]))}
    return whatif.Member("a", stamps[:cut], stamps[cut:], {"y": y[:cut]}, {c: v[:cut] for c, v in inputs.items()}, plan, scenario)


def test_an_input_that_is_mostly_empty_is_not_read_and_the_reason_says_so():
    stamps, y, x, z = _weekly()
    starts = whatif.places(np.random.default_rng(1))
    tenth = np.arange(len(y)) % 10 == 0
    mostly_empty, some_gaps = np.where(tenth, z, np.nan), np.where(tenth, np.nan, z)

    def read(inputs, on):
        m = _member(stamps, y, inputs, on)
        moved = np.nan_to_num(m.scenario[on] - m.plan[on])
        return whatif.reading(m, "y", on, np.full(13, 50.0), np.full(13, 50.0), 50.0 + 2.0 * moved, starts)

    # Nine rows in ten of one input are empty. Held with the rest it would leave fifteen rows; it is left out, and x is read.
    got = read({"x": x, "z": z, "markdown": mostly_empty}, "x")
    assert got["sign"] == "+" and got["basis"].endswith("; not read, being mostly empty: `markdown`")
    # One row in ten empty: the input is read, at the cost of those rows, and nothing is said.
    got = read({"x": x, "z": some_gaps}, "x")
    assert got["sign"] == "+" and "mostly empty" not in got["basis"]
    # The input asked about is itself mostly empty: no answer.
    got = read({"x": x, "markdown": mostly_empty}, "markdown")
    assert got["sign"] == "?" and got["basis"] == "the input is mostly empty in this member's history"
