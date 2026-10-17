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


def test_a_short_or_fine_history_is_drawn_in_runs_shorter_than_itself():
    # A 5-minute table of 200 rows: the run (a day, 288 rows) is longer than the history; drawn as it was, every
    # resample was the whole history and a noise slope was "off zero" with an interval of no width.
    rng = np.random.default_rng(0)
    stamps = (np.datetime64("2024-01-01T00:00") + np.arange(200) * np.timedelta64(5, "m")).astype("datetime64[ms]")
    got = whatif.history(stamps, rng.normal(size=200), np.column_stack([rng.normal(size=200)]), whatif.places(rng))
    lo, hi = np.quantile(got["draws"][:, 0], [0.05, 0.95])
    assert len(np.unique(np.round(got["draws"][:, 0], 12))) > 100 and lo < 0 < hi
    # Under no effect, with autocorrelated noise, the 90% interval covers zero about nine times in ten where the cap
    # bites (30 weekly rows: 35 of 40, where it was 31) and where it does not (400 daily rows: 34 of 40, as before).
    # On 1,000 hourly rows with autocorrelation 0.97 the rows reading covers about 28 of 40: a run of a day is shorter
    # than that dependence, which the cap does not touch (docs/whatif.md, where it is weak).
    def ar(n, rho):
        e, out = rng.normal(size=n), np.zeros(n)
        for t in range(1, n):
            out[t] = rho * out[t - 1] + e[t]
        return out
    for first, step, n, rho in (("2019-01-06", np.timedelta64(7, "D"), 30, 0.5), ("2021-01-04", np.timedelta64(1, "D"), 400, 0.9)):
        stamps = (np.datetime64(first) + np.arange(n) * step).astype("datetime64[ms]")
        covers = 0
        for _ in range(40):
            got = whatif.history(stamps, 50 + 2 * ar(n, rho), np.column_stack([ar(n, rho)]), whatif.places(rng))
            lo, hi = np.quantile(got["draws"][:, 0], [0.05, 0.95])
            covers += lo <= 0 <= hi
        assert covers >= 30, (n, covers)  # 75% at the least over 40 draws: the interval is honest to within its own noise


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


def test_a_reading_that_holds_on_the_rows_alone_is_said_as_such_and_an_unchanged_input_as_unchanged():
    # A column that drifts on its own beside a drifting input: the rows show a slope, the changes do not; the basis says so
    # and keeps "no effect" (the sign rule is the conjunction), where before it said the history showed nothing at all.
    rng = np.random.default_rng(5)
    stamps = _weekly(periods=240)[0]
    x = np.cumsum(rng.normal(0, 0.1, 240)) + 0.01 * np.arange(240)
    drifts = 20.0 + np.cumsum(rng.normal(0, 0.3, 240))
    starts = whatif.places(rng)
    m = whatif.Member("a", stamps[:-13], stamps[-13:], {"y": drifts[:-13]}, {"x": x[:-13]}, {"x": x[-13:]}, {"x": np.minimum(x[-13:] + 0.2, x[:-13].max())})
    plan = np.full(13, 20.0)
    got = whatif.reading(m, "y", "x", plan, plan, plan, starts)
    assert got["sign"] in "0?" and "no effect" in got["basis"] and "both readings" in got["basis"] and "disagree" not in got["basis"]
    # An input the same as planned and under the scenario for this member is `=`, unchanged: not "no effect visible".
    same = whatif.reading(m, "y", "x", plan, plan, plan, starts) if False else whatif.reading(whatif.Member("a", m.stamps, m.ahead, m.targets, m.inputs, m.plan, dict(m.plan)), "y", "x", plan, plan, plan, starts)
    assert same["sign"] == "=" and whatif.CONFIDENCE["="] == "unchanged" and "same as planned" in same["basis"]
    # Every threshold the reading uses is declared on the answer.
    declared = {a["value"] for a in whatif.assumptions() if not isinstance(a["value"], str)}
    assert {whatif.INTERVAL, whatif.STILL, whatif.SEPARABLE, whatif.MOSTLY_EMPTY, whatif.TIED, whatif.EXTRA_ROWS} <= declared


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


def test_several_inputs_changed_at_once_are_read_as_the_sum_of_what_each_does():
    stamps, y, x, z = _weekly()
    starts = whatif.places(np.random.default_rng(1))
    m = _member(stamps, y, {"x": x, "z": z}, "x")
    m.scenario["z"] = np.minimum(m.plan["z"] + 0.1, np.nanmax(z[: len(m.stamps)]))
    plan = np.full(13, 50.0)
    alone = {c: whatif.reading(m, "y", c, plan, plan, plan + 1.0, starts) for c in ("x", "z")}
    both = whatif.reading(m, "y", ["x", "z"], plan, plan, plan + 1.0, starts)
    # The history's estimate of the joint change is the sum of the two single estimates, to the digit.
    assert np.allclose(both["history_value"] - plan, (alone["x"]["history_value"] - plan) + (alone["z"]["history_value"] - plan))
    assert both["sign"] in "+-0?" and "the inputs together" in both["basis"] or both["basis"].startswith("the model and the history disagree")
    # One input changed answers as before, to the digit, through the same code.
    one = whatif.reading(m, "y", ["x"], plan, plan, plan + 1.0, starts)
    for key in ("sign", "basis", "explains"):
        assert one[key] == alone["x"][key]
    assert np.allclose(one["history_low"], alone["x"]["history_low"]) and np.allclose(one["history_high"], alone["x"]["history_high"])


def test_a_combination_the_history_never_held_is_beyond_it_and_the_parts_add_up():
    rng = np.random.default_rng(3)
    promo, holiday = (rng.uniform(size=200) < 0.4).astype(float), (rng.uniform(size=200) < 0.3).astype(float)
    never_together = np.column_stack([promo, np.where(promo == 1, 0.0, holiday)])
    # Each input inside its own range, the pair never on together: beyond. On together before: inside.
    assert whatif.beyond_together(never_together, np.array([[1.0, 1.0]]))
    assert not whatif.beyond_together(never_together, np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert not whatif.beyond_together(np.column_stack([promo, holiday]), np.array([[1.0, 1.0]]))
    # A continuous pair: a corner the history never visited is beyond, a point among the rows is not.
    cloud = rng.normal(size=(300, 2))
    assert whatif.beyond_together(cloud, np.array([[6.0, 6.0]])) and not whatif.beyond_together(cloud, np.array([[0.3, -0.2]]))
    # The parts of a joint reading add up to its estimate and equal the single readings.
    stamps, y, x, z = _weekly()
    starts = whatif.places(np.random.default_rng(1))
    m = _member(stamps, y, {"x": x, "z": z}, "x")
    m.scenario["z"] = np.minimum(m.plan["z"] + 0.1, np.nanmax(z[: len(m.stamps)]))
    plan = np.full(13, 50.0)
    both = whatif.reading(m, "y", ["x", "z"], plan, plan, plan + 1.0, starts)
    assert [part["input"] for part in both["parts"]] == ["x", "z"]
    assert np.isclose(sum(part["history_shift"] for part in both["parts"]), np.mean(both["history_value"] - plan))
    for c in ("x", "z"):
        alone = whatif.reading(m, "y", c, plan, plan, plan + 1.0, starts)
        assert np.isclose(alone["parts"][0]["history_shift"], next(part["history_shift"] for part in both["parts"] if part["input"] == c))

