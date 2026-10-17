"""The what-if: which way a column moves under a changed input
(`docs/contract.md`, `/whatif`; what it rests on: `docs/whatif.md`).

Every member is read on its own, and every target on its own.

- TabICL's forecaster predicts the target three times: the last
  periods of the history from the rows before them (held back, so
  the error is measured on the caller's own table), the periods
  ahead with the inputs as planned, and the same under the change.
- The member's own history is read for how the target moved with
  the changed input, the other inputs, the trend and the calendar's
  cycles held: a slope with an interval from resampling, read twice,
  on the rows and on their changes from one row to the next. Several
  inputs changed at once are read together: the history's estimate
  is the sum of what each does, its interval from the same draws
  summed the same way, so an interaction between two inputs is not
  something the history reads.
- The sign is stated where the two agree: the history shows the
  target higher where the input, or the inputs together, was higher,
  and the model moves it the same way. Both read the same history
  with the same features (the calendar and the inputs), so their
  agreement says the association holds under a linear and under a
  flexible fit; it does not say the input causes the change. That the
  nothing which set the input also moved the target is the caller's
  assumption, declared on every answer (`assumptions`).

The route promises that direction and no size. The model's shift
and the history's estimate are both returned; the size lies
somewhere between them, and the model errs low more often than
high."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RESAMPLES = 300
INTERVAL = 0.90  # of the history's slope, by resampling
MIN_RUNS = 10  # a run of neighbouring rows is at most this share of the history: a resample is never the history itself
REACH = 0.99  # the share of history rows whose nearest-neighbour distance sets how far a combination of inputs may lie
BEYOND_SHARE = 0.10  # a combination is beyond where more than this share of the periods ahead lie past the reach
SEPARABLE = 0.10  # the share of an input's variation the other inputs and the calendar must leave for it to be told apart
STILL = 0.10  # a shift under this share of what the history's regression leaves of the column (its residual spread) counts as none
MOSTLY_EMPTY = 0.50  # an input empty in more than this share of the history's rows is not read
CONTEXT = 4096  # the past periods TabICL's forecaster reads at most
MIN_HISTORY = 26  # the rows a member needs behind a prediction
MEDIAN = 0.5


class WhatifError(Exception):
    """A read the what-if refuses for one member or target, with the reason the door reports."""


# -- reading the history ---------------------------------------------------------


def minutes(stamps: np.ndarray) -> float:
    """The grain: the usual distance between two rows, in minutes."""
    return float(np.median(np.diff(stamps).astype("timedelta64[m]").astype(np.float64))) if len(stamps) > 1 else np.inf


def run_of_rows(grain: float) -> int:
    """How many neighbouring rows are drawn together when the history
    is drawn again: a day on a table finer than daily, two weeks on a
    daily one, eight weeks on a weekly one, three months on a monthly.
    On a short history the run is cut to a tenth of it (`MIN_RUNS`), so
    that there are runs to draw: a run as long as the history gives the
    history back 300 times, an interval of no width, and any slope off
    zero (found 2026-10-17 on a 5-minute table of 200 rows)."""
    return int(round(24 * 60 / grain)) if grain < 24 * 60 else 14 if grain == 24 * 60 else 8 if grain <= 7 * 24 * 60 else 3


def design(stamps: np.ndarray, inputs: np.ndarray) -> np.ndarray:
    """What is held when the history's slope is read: a level, a
    trend, the year's cycle, on a daily or finer table the day of the
    week, on a finer one the hour of the day, and every input (last)."""
    day = (stamps - stamps.astype("datetime64[Y]")).astype("timedelta64[D]").astype(np.float64)
    t = np.arange(len(stamps), dtype=np.float64)
    held = [np.ones(len(stamps)), t / max(len(stamps), 1), *[f(2 * np.pi * k * day / 365.25) for k in (1, 2) for f in (np.sin, np.cos)]]
    grain = minutes(stamps)
    if grain <= 24 * 60:
        weekday = (stamps.astype("datetime64[D]").astype(np.int64) + 3) % 7  # Monday is 0
        held += [(weekday == d).astype(np.float64) for d in range(1, 7)]
    if grain < 24 * 60:
        hour = stamps.astype("datetime64[h]").astype(np.int64) % 24
        held += [(hour == h).astype(np.float64) for h in range(1, 24)]
    return np.column_stack([*held, inputs])


def rows_needed(stamps: np.ndarray, inputs: int) -> int:
    """The actual periods a member needs behind a prediction: `MIN_HISTORY`, or the columns the history's reading
    holds plus `EXTRA_ROWS` where that is more — 16 with two inputs on a monthly or weekly table, 22 on a daily, 45 on
    an hourly — so that the door says so instead of the reading answering "too short"."""
    return max(MIN_HISTORY, design(stamps, np.zeros((len(stamps), inputs))).shape[1] + EXTRA_ROWS)


def places(rng: np.random.Generator) -> np.ndarray:
    """Where each run of rows starts when the history is drawn again, as a share of the history: the same for every series read with it."""
    return rng.uniform(size=(RESAMPLES, CONTEXT))


def history(stamps: np.ndarray, y: np.ndarray, inputs: np.ndarray, starts: np.ndarray, run: int | None = None, together: list[int] | None = None) -> dict[str, np.ndarray]:
    """Per input (the last axis), how `y` moved with it over one
    series' history, the other inputs, the trend and the cycles held,
    in units of `y` per unit of the input. Read twice: on the rows as
    they are (`slope`), and on their changes from one row to the next
    (`slope_on_changes`); a drift that two columns merely share shows
    only in the first. With each reading its resamples (`draws`,
    `draws_on_changes`): the history drawn again in runs of
    neighbouring rows, since neighbouring rows resemble each other.
    And how much of the input's own variation the rest leaves
    unexplained (`free`), and how much of what the rest leaves of `y`
    the input explains (`explains`); with `together`, the indices of
    inputs changed at once, how much they explain as a set
    (`explains_together`). An input that cannot be read is NaN, with
    `free` at zero."""
    k = inputs.shape[1]
    slopes, draws, free, explains = np.full(k, np.nan), np.full((RESAMPLES, k), np.nan), np.zeros(k), np.full(k, np.nan)
    slopes_c, draws_c = np.full(k, np.nan), np.full((RESAMPLES, k), np.nan)
    out = {"slope": slopes, "draws": draws, "slope_on_changes": slopes_c, "draws_on_changes": draws_c, "free": free, "explains": explains, "explains_together": np.nan, "residual_sd": np.nan}
    full = design(stamps, inputs)
    held = full.shape[1] - k
    keep = ~np.isnan(y) & ~np.isnan(full).any(axis=1)
    run = run_of_rows(minutes(stamps)) if run is None else run
    x, seen = full[keep], y[keep]
    varies = np.std(x[:, held:], axis=0) > 0 if len(seen) else np.zeros(k, dtype=bool)
    if len(seen) < x.shape[1] + EXTRA_ROWS or not varies.any():
        return out

    def rows(b: int, length: int) -> np.ndarray:
        size = max(1, min(run, length // MIN_RUNS))
        first = (starts[b, : -(-length // size)] * (length - size + 1)).astype(int)
        return (first[:, None] + np.arange(size)).ravel()[:length]

    coef = np.linalg.lstsq(x, seen, rcond=None)[0]
    left = np.sum((seen - x @ coef) ** 2)
    out["residual_sd"] = float(np.sqrt(left / max(len(seen) - x.shape[1], 1)))  # what the level, trend, cycles and inputs leave of y
    for j in np.flatnonzero(varies):
        at = held + j
        others = np.delete(x, at, axis=1)
        fitted = others @ np.linalg.lstsq(others, x[:, at], rcond=None)[0]
        free[j] = float(np.var(x[:, at] - fitted) / np.var(x[:, at]))
        slopes[j] = coef[at]
        without = np.sum((seen - others @ np.linalg.lstsq(others, seen, rcond=None)[0]) ** 2)
        explains[j] = float(1 - left / without) if without > 0 else 0.0
    if together and all(varies[j] for j in together):
        others = np.delete(x, [held + j for j in together], axis=1)
        without = np.sum((seen - others @ np.linalg.lstsq(others, seen, rcond=None)[0]) ** 2)
        out["explains_together"] = float(1 - left / without) if without > 0 else 0.0
    # The changes from one row to the next: of the column and of every input; the level, trend and cycles stay.
    dx, dy = np.column_stack([full[1:, :held], np.diff(full[:, held:], axis=0)]), np.diff(y)
    keep = ~np.isnan(dy) & ~np.isnan(dx).any(axis=1)
    dx, dy = dx[keep], dy[keep]
    moving = varies & (np.std(dx[:, held:], axis=0) > 0) if len(dy) >= dx.shape[1] + EXTRA_ROWS else np.zeros(k, dtype=bool)
    if moving.any():
        slopes_c[moving] = np.linalg.lstsq(dx, dy, rcond=None)[0][held:][moving]
    for b in range(RESAMPLES):
        r = rows(b, len(seen))
        draws[b, varies] = np.linalg.lstsq(x[r], seen[r], rcond=None)[0][held:][varies]
        if moving.any():
            r = rows(b, len(dy))
            draws_c[b, moving] = np.linalg.lstsq(dx[r], dy[r], rcond=None)[0][held:][moving]
    return out


def beyond_together(past: np.ndarray, ahead: np.ndarray) -> bool:
    """Whether the rows of `ahead` (periods, inputs), a combination of
    several inputs, lie farther from every row of `past` (rows,
    inputs) than past rows lie from their own nearest neighbour, each
    input scaled by its spread in the past: the range check per input
    does not see a combination the history never held (a promotion and
    a holiday that never fell together, a price never cut while the
    promotion was on). The reach is the distance within which 99 of
    100 history rows find a neighbour (`REACH`): the loneliest row
    alone set it before, so one mis-keyed row disarmed the check. A
    combination is beyond where more than a tenth of the periods ahead
    lie past the reach (`BEYOND_SHARE`): one odd period among thirteen
    does not fire it, two do; a plan drawn from the history itself
    fires it on well under one in a hundred. Where every combination
    of switches has a duplicate the reach is zero and an unseen one is
    beyond; where the history is sparse everywhere, near is as near as
    it gets. Rows with a gap are left out."""
    past = past[~np.isnan(past).any(axis=1)]
    ahead = ahead[~np.isnan(ahead).any(axis=1)]
    if len(past) < 2 or not len(ahead):
        return False
    scale = np.std(past, axis=0, ddof=1)
    scale[~(scale > 0)] = 1.0
    p, a = past / scale, ahead / scale

    def nearest(rows: np.ndarray, own: bool) -> np.ndarray:
        out = np.full(len(rows), np.inf)
        for lo in range(0, len(rows), 256):  # in blocks: the history holds up to 4,096 rows
            d2 = ((rows[lo : lo + 256, None, :] - p[None, :, :]) ** 2).sum(axis=-1)
            if own:
                d2[np.arange(d2.shape[0]), np.arange(lo, lo + d2.shape[0])] = np.inf
            out[lo : lo + 256] = np.sqrt(d2.min(axis=1))
        return out

    reach = float(np.quantile(nearest(p, True), REACH))
    return bool(np.mean(nearest(a, False) > reach) > BEYOND_SHARE)


def spread(y: np.ndarray) -> float:
    """A column's own spread: its standard deviation, 1 where it has none."""
    out = np.nanstd(y, ddof=1) if (~np.isnan(y)).sum() > 1 else np.nan
    return float(out) if np.isfinite(out) and out > 0 else 1.0


# -- one member, one target -------------------------------------------------------


@dataclass
class Member:
    """One member's rows: the history, and the periods ahead as planned and under the change."""

    name: str
    stamps: np.ndarray  # (n,) the history's periods, datetime64
    ahead: np.ndarray  # (h,) the periods ahead
    targets: dict[str, np.ndarray]  # per target, (n,)
    inputs: dict[str, np.ndarray]  # per input, (n,)
    plan: dict[str, np.ndarray]  # per input, (h,) as planned
    scenario: dict[str, np.ndarray]  # per input, (h,) under the change


@dataclass
class Job:
    """One prediction asked of the forecaster: a history, and the periods to predict with their inputs."""

    stamps: np.ndarray
    y: np.ndarray
    inputs: dict[str, np.ndarray]
    ahead: np.ndarray
    future: dict[str, np.ndarray]


def jobs(member: Member, target: str) -> list[Job]:
    """The three predictions of one target: held back, as planned, under the change."""
    h, y = len(member.ahead), member.targets[target]
    before = {c: v[:-h] for c, v in member.inputs.items()}
    recorded = {c: v[-h:] for c, v in member.inputs.items()}
    return [
        Job(member.stamps[:-h], y[:-h], before, member.stamps[-h:], recorded),
        Job(member.stamps, y, member.inputs, member.ahead, member.plan),
        Job(member.stamps, y, member.inputs, member.ahead, member.scenario),
    ]


def reading(member: Member, target: str, changed: str | list[str], held_back: np.ndarray, plan: np.ndarray, scenario: np.ndarray, starts: np.ndarray) -> dict:
    """What can be said about one target of one member. `changed` is
    the input that differs between the plan and the scenario, or the
    inputs that differ at once. `held_back`, `plan` and `scenario` are
    the model's medians, per period. The sign, the reason, the
    history's own estimate of the target under the scenario per period
    with its interval, what each changed input alone does over the
    periods ahead with its interval (`parts`, adding up to the joint
    estimate), how much the changed input explains (the inputs
    together, where several are changed), and the error on the
    held-back periods.

    Several inputs are read as the sum of what each does: the slopes
    of the history's one regression, which holds every input, times
    each input's change, and the interval from the same draws summed
    the same way. The sign is stated for that joint change. An input
    that fails a check (mostly empty, never varied, beyond the
    history, not told apart) is named in the reason."""
    changed = [changed] if isinstance(changed, str) else list(changed)
    h, y = len(member.ahead), member.targets[target]
    deltas = {c: member.scenario[c] - member.plan[c] for c in changed}
    shift = float(np.nanmean(scenario - plan))
    out = {
        "sign": "?", "basis": "", "explains": np.nan, "parts": [], "history_error": np.nan,
        "error": float(np.nanmean(np.abs(held_back - y[-h:]))) if (~np.isnan(y[-h:])).any() else np.nan,
        "history_value": np.full(h, np.nan), "history_low": np.full(h, np.nan), "history_high": np.full(h, np.nan),
    }  # fmt: skip
    # An input that is the same as planned for this member is not changed for it; none changed is no change.
    differs = [c for c in changed if (deltas[c][~np.isnan(deltas[c])] != 0).any()]
    if not differs:
        return out | {"sign": "=", "basis": ("the input is" if len(changed) == 1 else "the inputs are") + " the same as planned and under the scenario for this member: nothing is changed"}
    changed = differs
    one = len(changed) == 1
    name = (lambda c: "the input") if one else (lambda c: f"the input `{c}`")  # noqa: E731
    moved = np.array([float(np.nanmean(deltas[c])) if (~np.isnan(deltas[c])).any() else 0.0 for c in changed])
    # An empty cell is unknown, not zero. An input that is mostly empty is not read: the changed one cannot be
    # answered, another one is left out of what is held, and the reason says so. A row with a gap in an input
    # that is read is left out, so an input kept in spite of its gaps costs rows.
    known = ~np.isnan(y)
    for c in changed:
        if known.any() and np.isnan(member.inputs[c][known]).mean() > MOSTLY_EMPTY:
            return out | {"basis": f"{name(c)} is mostly empty in this member's history"}
    there = known & ~np.isnan(np.column_stack([member.inputs[c] for c in changed])).any(axis=1)
    left_out = [c for c, v in member.inputs.items() if c not in changed and there.any() and np.isnan(v[there]).mean() > MOSTLY_EMPTY]
    names = [c for c in member.inputs if c not in left_out]
    at = [names.index(c) for c in changed]
    note = "; not read, being mostly empty: " + ", ".join(f"`{c}`" for c in left_out) if left_out else ""

    def say(basis: str, **more) -> dict:
        return out | more | {"basis": basis + note}

    for c in changed:
        past = member.inputs[c]
        seen = past[~np.isnan(past)]
        if len(seen) < 2 or np.std(seen) == 0:
            return say(f"{name(c)} never varied in this member's history")
        asked = member.scenario[c][~np.isnan(member.scenario[c])]
        if (asked > seen.max()).any() or (asked < seen.min()).any():
            # Said apart: the input as planned may already lie where the history never was, before anything is changed.
            planned = member.plan[c][~np.isnan(member.plan[c])]
            beyond = (planned > seen.max()).any() or (planned < seen.min()).any()
            what = ("the input as planned" if beyond else "the change") if one else (f"`{c}` as planned" if beyond else f"the change of `{c}`")
            return say(f"{what} goes beyond what the history holds: the input lay between {seen.min():g} and {seen.max():g}")
    if not one:
        # The combination: each input may lie inside its own range while the history never held them together.
        past = np.column_stack([member.inputs[c] for c in changed])
        if beyond_together(past, np.column_stack([member.plan[c] for c in changed])):
            return say("the inputs as planned, together, go beyond what the history holds: no row of the history lies near their combination")
        if beyond_together(past, np.column_stack([member.scenario[c] for c in changed])):
            return say("the change goes beyond what the history holds for the inputs together: no row of the history lies near their combination")
    read = history(member.stamps, y, np.column_stack([member.inputs[c] for c in names]), starts, together=None if one else at)
    out["history_error"] = float(read["residual_sd"])  # what the history leaves of the column: the error its estimate is read against
    for c, j in zip(changed, at):
        if not np.isfinite(read["slope"][j]) or not np.isfinite(read["slope_on_changes"][j]):
            return say("the history is too short to read how the column moved with the input" + ("" if one else f" `{c}`"))
    out["explains"] = float(read["explains"][at[0]]) if one else float(read["explains_together"])
    for c, j in zip(changed, at):
        if read["free"][j] < SEPARABLE:
            return say(f"{name(c)} cannot be told apart from {_tied(member, c, names)}")
    ends = [(1 - INTERVAL) / 2, 1 - (1 - INTERVAL) / 2]
    # The joint change: per draw, the sum over the changed inputs of the draw's slope times the input's change;
    # as the mean change, for the sign, and per period, for the interval of the history's estimate.
    change = np.column_stack([deltas[c] for c in changed])  # (periods, inputs)
    slopes = np.array([read["slope"][j] for j in at])
    draws, draws_c = read["draws"][:, at], read["draws_on_changes"][:, at]
    lo, hi = np.nanquantile(draws @ moved, ends)
    lo_c, hi_c = np.nanquantile(draws_c @ moved, ends)
    # The history's estimate per period: the plan's median plus what the change made there does, by the slopes.
    out["history_value"] = plan + change @ slopes
    per_period = draws @ change.T  # (draws, periods)
    low_p, high_p = np.nanquantile(per_period, ends[0], axis=0), np.nanquantile(per_period, ends[1], axis=0)
    out["history_low"], out["history_high"] = plan + low_p, plan + high_p
    # What each changed input alone does, as the mean over the periods ahead, with its interval from the same draws:
    # the parts add up to the joint estimate, so a user combines the single-lever and the multi-lever readings.
    mean_change = np.nanmean(change, axis=0)  # per input
    out["parts"] = [
        {"input": c, "history_shift": float(slopes[j] * mean_change[j]), "history_low": float(np.nanquantile(draws[:, j] * mean_change[j], ends[0])), "history_high": float(np.nanquantile(draws[:, j] * mean_change[j], ends[1]))}
        for j, c in enumerate(changed)
    ]
    what = "the input" if one else "the inputs together"
    # A sign is for every period ahead or for none: where the history's estimate of the change is off zero upward in
    # some periods and downward in others (two levers moving in different periods, a change that reverses), the mean
    # could net to nothing or point one way while the periods say otherwise; the per-period numbers are served as they are.
    up, down = int((low_p > 0).sum()), int((high_p < 0).sum())
    if up and down:
        return say(f"the history's estimate of the change points both ways over the periods ahead ({up} up, {down} down), so no one direction holds for them all")
    # The history shows the change moving the column: both readings the same way, neither interval over zero.
    shows = (lo > 0 and lo_c > 0) or (hi < 0 and hi_c < 0)
    if shows:
        way = np.sign(slopes @ moved)
        if np.sign(shift) == way and way != 0:
            return say(f"the history shows {what} moving the column, on the rows and on their changes, and the model moves it the same way", sign="+" if way > 0 else "-")
        return say("the model and the history disagree on the direction")
    # One reading off zero and the other not is said as such: it is not the same as neither showing anything.
    on_rows, on_changes = lo > 0 or hi < 0, lo_c > 0 or hi_c < 0
    if on_rows and not on_changes:
        said = f"the history shows no effect of {what} that holds on both readings (the rows show it moving the column, their changes from row to row do not)"
    elif on_changes and not on_rows:
        said = f"the history shows no effect of {what} that holds on both readings (the changes from row to row show it moving the column, the rows do not)"
    elif on_rows and on_changes:
        said = f"the history shows no effect of {what} that holds on both readings (the rows and their changes point opposite ways)"
    else:
        said = f"the history shows no effect of {what} on the column"
    # Still is judged against what the history leaves of the column, not its whole spread: a trending column's spread is
    # mostly its trend, and a shift of several residual spreads would otherwise pass as none.
    left = read["residual_sd"] if np.isfinite(read["residual_sd"]) and read["residual_sd"] > 0 else spread(y)
    if abs(shift) < STILL * left:
        return say(said + ", and the model moves it by less than a tenth of what the history leaves of its spread", sign="0")
    return say(said + ", and the model moves it")


def _tied(member: Member, changed: str, read: list[str]) -> str:
    """What an input that does not stand apart goes with: another input by name where one follows it closely, else the rest together."""
    x = member.inputs[changed]
    best, name = 0.0, ""
    for other in read:
        v = member.inputs[other]
        both = ~np.isnan(x) & ~np.isnan(v)
        if other != changed and both.sum() > 2 and np.std(v[both]) > 0 and np.std(x[both]) > 0:
            r = abs(float(np.corrcoef(x[both], v[both])[0, 1]))
            if r > best:
                best, name = r, other
    return f"the input `{name}`" if best >= TIED else "the other inputs and the calendar's cycles"


CONFIDENCE = {"+": "direction", "-": "direction", "0": "no effect visible", "?": "not answerable", "=": "unchanged"}
TIED = 0.90  # the correlation at which an input is said to follow another by name
EXTRA_ROWS = 8  # the rows the history's regression needs beyond the columns it holds


def assumptions() -> list[dict]:
    """What the sign rests on that the model did not produce, and what
    the caller supplies: the two estimates read one history, so the
    sign is an association in that history, and whether the input
    caused the change rests on nothing in the table having set the
    input and moved the column at once."""
    return [
        {"field": "sign", "value": "no confounding", "basis": "assumed: nothing that set the input also moved the column, beyond the other inputs and the calendar's cycles, which are held. The model and the history's regression read the same rows with the same features, so their agreement is one piece of evidence: the association holds under a linear and under a flexible fit. It is read for the same period; a lag of the input is not held (docs/whatif.md)"},
        {"field": "sign", "value": INTERVAL, "basis": "assumed: the interval of the history's slope that has to lie off zero, on the rows and on their changes (agreed 2026-10-01, docs/whatif.md)"},
        {"field": "sign", "value": STILL, "basis": "assumed: a shift under this share of what the history's regression leaves of the column (its residual spread; the column's own spread where no regression is read) counts as none. At the former threshold, a tenth of the column's whole spread, the model's shift stayed under it on 91% of 17,179 tested series where the history showed no effect; that measures the model's stillness at a threshold, not the threshold. At this one, on the development tables of 2026-10-17, it stayed under on 58% of 71: a shift the lenient threshold had passed as none is a move (docs/whatif.md)"},
        {"field": "basis", "value": SEPARABLE, "basis": "assumed: the share of an input's variation the other inputs and the calendar have to leave for it to be told apart"},
        {"field": "basis", "value": TIED, "basis": "assumed: an input that is not told apart is said to follow another input by name where their correlation is at least this; else the other inputs and the calendar's cycles together"},
        {"field": "basis", "value": MOSTLY_EMPTY, "basis": "assumed: an input empty in more than half of the history's rows is not read; an empty cell is taken as unknown, not as zero"},
        {"field": "basis", "value": EXTRA_ROWS, "basis": "assumed: the history's regression needs this many rows more than the columns it holds (a level, a trend, four for the year's cycle, six for the weekday on a daily or finer table, 23 for the hour on a finer one, and every input): with two inputs 16 rows on a monthly or weekly table, 22 on a daily, 45 on an hourly; fewer is said as too short"},
        {"field": "history_low", "value": INTERVAL, "basis": "assumed: the interval of the history's estimate, from the history drawn again 300 times in runs of neighbouring rows (a day's rows on a table finer than daily, 14 rows on a daily one, 8 on a weekly, 3 on a monthly, and never more than a tenth of the history, so that there are at least ten runs to draw from); a range between two estimates has no stated coverage"},
        {"field": "basis", "value": REACH, "basis": "assumed: a combination of several changed inputs lies beyond what the history holds together where it lies farther from every history row than the distance within which this share of history rows find a nearest neighbour, each input scaled by its spread in the history (before 2026-10-17 the loneliest row set the reach, and one mis-keyed row disarmed the check)"},
        {"field": "basis", "value": BEYOND_SHARE, "basis": "assumed: the plan or the change goes beyond together where more than this share of the periods ahead lie past the reach: one odd period among thirteen does not say so, two do"},
    ]
