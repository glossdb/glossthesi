"""Forecasts of time series: Chronos-2, as its package ships it.

One set-up, chosen on forecasts a quarter and a year ahead
(`docs/forecast.md`):

- The series of a request are forecast together, in groups of at most
  100, so that they inform each other. That is how the published
  results of Chronos-2 are made.
- Covariates are passed where the request has them: their values over
  the history, and over the periods ahead where they are known.
- The quantiles are the model's own. Nothing is recalibrated. A
  client that has learned, from its own record of forecasts and what
  then happened, that a dataset's intervals are too narrow or too
  wide sends a `width`: the distance of every quantile from the median
  is multiplied by it, one factor or one per period ahead. The rule
  for learning it from such a record is `learned_width`.
- A series may have several columns (the sales and the orders of one
  store): they are forecast as one, each column informed by the
  others, and answered each.
- The total of a span (the next 3 periods, the next 12) comes from the
  same forecast. Its median is the sum of the periods' medians. Its
  interval combines the periods' intervals as partly dependent
  (`DEPENDENCE`, per grain): added up as they are they are too wide,
  added as independent too narrow. Week-to-week errors are less alike
  than month-to-month errors, and day-to-day errors less still, so
  weekly and daily totals need less dependence.
- The walk and a backtest are forecasts from past origins. Where the
  actual value has landed, the PIT is read against the model's own
  quantile grid (`GRID`), before any width: the quantile at which the
  actual landed, 0 to 1.

A series is values in the client's time grain. The model is not told
the grain and needs no calendar; only the interval of a total uses
it."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

import numpy as np

MODEL = "amazon/chronos-2"
# The checkpoint's commit on the hub: the weights every measured number describes (docs/forecast.md). A move is a
# change of model and runs the evaluation again; the build bakes this revision and /healthz names it.
REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
GROUP = 100  # series forecast together, at most: the size the model's published results were made with
MIN_HISTORY = 6  # known values a series needs
MAX_HORIZON = 1024
# Between the periods of a span, per grain: the value at which the total's 80% interval covers 0.80 of
# the actual totals. The combination is the sum of equicorrelated normals, so the value reads as a
# correlation; it is fitted to the coverage and so takes up whatever bias the medians carry, and per
# dataset it runs from 0.0 to 1.0 (GK-45): a width for the band, not a measured dependence of the errors. Months and weeks on the development backtests, quarter and year pooled, with a
# bootstrap standard error of 0.02 and 0.04 (`docs/forecast.md`, GK-45). Days and quarters on the held-out
# backtests of the coverage table, the calendar month's and the year's totals, standard error 0.01 and
# 0.06: there is no other daily or quarterly data, so each dataset was also read at the value the others
# give, and they covered 0.81 (GK-48). The answer says where each value comes from (`dependence`).
DEPENDENCE = {"month": 0.43, "week": 0.22, "day": 0.12, "quarter": 0.25}
GRAINS = tuple(DEPENDENCE)
_BASIS = {
    **dict.fromkeys(
        ("month", "week"),
        "fitted: the equicorrelation at which the total's 80% interval covers 0.80 of the actual totals on the development backtests (GK-45): a width for the band, written as a correlation, not a measured dependence between the periods' errors; per dataset it runs from 0.0 to 1.0",
    ),
    **{
        g: f"fitted: the equicorrelation at which the total's 80% interval covers 0.80 of the actual totals on the held-out {w} backtests, the only {w} data, checked by leaving one dataset out (GK-48): a width for the band, written as a correlation, not a measured dependence between the periods' errors"
        for g, w in (("day", "daily"), ("quarter", "quarterly"))
    },
}
# The model's own quantile levels: what it was trained to answer, and the grid a PIT is read against.
GRID = (0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99)


class ForecastError(Exception):
    """A series that cannot be forecast, in words the client can act on."""


def out_of_memory(e: BaseException) -> bool:
    """Whether a failure is the device's memory running out — torch says so in its text on CUDA and on Metal.
    That fails the job, not a series: it reaches the owner, which hands the device's memory back (owner.py)."""
    return isinstance(e, MemoryError) or "out of memory" in str(e).lower()


@dataclass
class Series:
    history: np.ndarray  # (T,) for one column, (columns, T) for several; NaN where a value is missing
    past: dict[str, np.ndarray] = field(default_factory=dict)  # covariates over the history: numbers, or strings for categories
    future: dict[str, np.ndarray] = field(default_factory=dict)  # over the periods ahead, for the covariates known ahead
    width: np.ndarray | float = 1.0  # what the distance of every quantile from the median is multiplied by: one factor, or (horizon,)

    @property
    def columns(self) -> int:
        return self.history.shape[0] if self.history.ndim == 2 else 1

    @property
    def length(self) -> int:
        return self.history.shape[-1]

    def check(self, horizon: int) -> dict[int, ForecastError]:
        """Raises where the series cannot be forecast. Comes back with
        the columns that cannot be, by index: the series is forecast
        for the others, with these in it as they are."""
        short = {}
        for column, values in enumerate(np.atleast_2d(self.history)):
            known = int((~np.isnan(values)).sum())
            if known < MIN_HISTORY:
                short[column] = ForecastError(
                    f"{known} known values of {'column ' + str(column) + ' of the ' if self.columns > 1 else ''}history: a forecast needs {MIN_HISTORY}"
                )
        if len(short) == self.columns:
            raise short[0] if self.columns == 1 else ForecastError(f"no column has {MIN_HISTORY} known values of history")
        for name, values in self.past.items():
            if values.shape[0] != self.length:
                raise ForecastError(f"covariate `{name}`: {values.shape[0]} past values for {self.length} values of history")
        for name, values in self.future.items():
            if name not in self.past:
                raise ForecastError(f"covariate `{name}` has future values and no past ones")
            if values.shape[0] != horizon:
                raise ForecastError(f"covariate `{name}`: {values.shape[0]} future values for a horizon of {horizon}")
        width = np.asarray(self.width, dtype=np.float64)
        if width.ndim == 1 and width.shape[0] != horizon:
            raise ForecastError(f"width: {width.shape[0]} factors for a horizon of {horizon}")
        if not np.isfinite(width).all() or (width <= 0).any():
            raise ForecastError("width: a factor above zero, or one per period ahead")
        return short

    @property
    def schema(self) -> tuple:
        """What series forecast together must share: the model's rule."""
        return self.columns, tuple(sorted(self.past)), tuple(sorted(self.future))


def totals(path: np.ndarray, median: np.ndarray, spans: list[int], grain: str = "month") -> dict[int, np.ndarray]:
    """The quantiles of the total of the first `span` periods, per span:
    `path` is (horizon, levels), `median` (horizon,)."""
    out = {}
    c = DEPENDENCE[grain]
    for span in spans:
        spread = np.sort(path[:span], axis=-1) - median[:span, None]
        added = spread.sum(axis=0)
        out[span] = median[:span].sum() + np.sign(added) * np.sqrt(c * added**2 + (1.0 - c) * (spread**2).sum(axis=0))
    return out


def dependence(grain: str) -> dict:
    """What the interval of a total rests on, for the answer object:
    the dependence used for `grain` and where it comes from."""
    return {"field": "totals", "grain": grain, "dependence": DEPENDENCE[grain], "basis": _BASIS[grain]}


def pit(grid: np.ndarray, actual: float) -> float:
    """The quantile at which `actual` landed in the model's own
    distribution: `grid` holds the quantiles at the levels of `GRID`.
    Linear between two levels; the midpoint of the tied levels where
    the grid holds the actual's value exactly (a zero month of a
    mostly-zero series); 0 below the lowest quantile and 1 above the
    highest. NaN without an actual."""
    if actual is None or not np.isfinite(actual):
        return float("nan")
    g = np.sort(np.asarray(grid, dtype=np.float64))
    levels = np.asarray(GRID)
    if not np.isfinite(g).all():
        return float("nan")
    if actual < g[0]:
        return 0.0
    if actual > g[-1]:
        return 1.0
    tied = np.flatnonzero(g == actual)
    if tied.size:
        return float(levels[tied].mean())
    hi = int(np.searchsorted(g, actual))
    lo = hi - 1
    return float(levels[lo] + (levels[hi] - levels[lo]) * (actual - g[lo]) / (g[hi] - g[lo]))


def learned_width(lower, median, upper, actual, nominal: float = 0.80, prior: int = 30) -> float:
    """The width a dataset's intervals should be sent with, from the
    dataset's record: per point of a past forecast, the interval's
    `lower` and `upper` bound as served at `nominal` (0.1 and 0.9 for
    the 80% interval), its `median`, and the `actual` value that landed.
    It is the factor at which the record's intervals would have held
    their nominal share of the actual values, found by search between
    a quarter and eight times, and shrunk toward 1 by `prior` points'
    worth so that a short record moves it little. Learned per band of
    distance (the next 3 periods, 4 to 6, 7 to 12), it is sent as one
    factor per period ahead. Points with a missing value are left out;
    an empty record gives 1."""
    lower, median, upper, actual = (np.asarray(v, dtype=np.float64).ravel() for v in (lower, median, upper, actual))
    known = ~(np.isnan(lower) | np.isnan(median) | np.isnan(upper) | np.isnan(actual))
    n = int(known.sum())
    if n == 0:
        return 1.0
    lo, m, hi, a = lower[known] - median[known], median[known], upper[known] - median[known], actual[known]

    def coverage(w: float) -> float:
        return float(((m + w * lo <= a) & (a <= m + w * hi)).mean())

    low, high = 0.25, 8.0
    if coverage(high) >= nominal:
        for _ in range(30):
            mid = (low * high) ** 0.5
            low, high = (mid, high) if coverage(mid) < nominal else (low, mid)
    return float((n * high + prior) / (n + prior))


class Chronos2:
    """Loaded on first use, kept."""

    def __init__(self, device: str):
        # On Metal it runs but slower than on the CPU at these sizes: 0.47 s against 0.12 s for a hundred series of sixty
        # periods, a year ahead, on an M5 Pro (2026-10-01). A model this small pays more for the device than it gains.
        self.device = "cpu" if device == "mps" else device
        self._pipeline = None
        self._lock = threading.Lock()

    def pipeline(self):
        with self._lock:
            if self._pipeline is None:
                import torch
                from chronos import BaseChronosPipeline

                self._pipeline = BaseChronosPipeline.from_pretrained(MODEL, revision=REVISION, device_map=self.device, torch_dtype=torch.float32)
            return self._pipeline

    def forecast(
        self, series: list[Series], horizon: int, levels: list[float], spans: list[int] | None = None, grain: str = "month"
    ) -> list[list[dict | ForecastError] | ForecastError]:
        """Per series, per column, its quantiles (horizon, levels) with
        the width applied, its totals per span, and `raw`: the model's
        own quantiles at the levels of `GRID` (horizon, GRID) before
        any width, for the PIT — or why the column cannot be forecast,
        or why the series cannot be. A series that is unfit fails alone,
        and so does a column. Series with the same columns and
        covariates are forecast together, in the order they were
        sent."""
        out: list[list[dict | ForecastError] | ForecastError] = [ForecastError("not forecast")] * len(series)
        groups: dict[tuple, list[int]] = {}
        short: dict[int, dict[int, ForecastError]] = {}
        for i, one in enumerate(series):
            try:
                short[i] = one.check(horizon)
                groups.setdefault(one.schema, []).append(i)
            except ForecastError as e:
                out[i] = e
        asked = sorted({*levels, *GRID})
        at, grid_at, middle = [asked.index(a) for a in levels], [asked.index(a) for a in GRID], asked.index(0.5)
        for mine in groups.values():
            inputs = [
                {"target": series[i].history.astype(np.float32), "past_covariates": dict(series[i].past), "future_covariates": dict(series[i].future)}
                for i in mine
            ]
            try:
                answered, _mean = self.pipeline().predict_quantiles(
                    inputs, prediction_length=horizon, quantile_levels=asked, batch_size=GROUP, cross_learning=True
                )
            except Exception as e:  # the package's own refusals, by their text: the series forecast with these fail, the others stand
                if out_of_memory(e):
                    raise
                for i in mine:
                    out[i] = ForecastError(f"the model could not forecast this series and the {len(mine) - 1} sent with the same covariates: {e}")
                continue
            for i, got in zip(mine, answered):
                paths = np.asarray(got, dtype=np.float64).reshape(series[i].columns, horizon, len(asked))
                out[i] = []
                width = np.broadcast_to(np.asarray(series[i].width, dtype=np.float64), (horizon,))[:, None]
                for column, path in enumerate(paths):
                    if column in short[i]:
                        out[i].append(short[i][column])
                        continue
                    median = path[:, middle]
                    raw = path[:, grid_at]
                    path = median[:, None] + width * (path - median[:, None])
                    out[i].append(
                        {"quantiles": path[:, at], "raw": raw, "totals": {span: total[at] for span, total in totals(path, median, spans or [], grain).items()}}
                    )
        return out


def fetch_checkpoints() -> None:
    """Pull Chronos-2 into the hub cache — the image bakes it."""
    from huggingface_hub import snapshot_download

    snapshot_download(MODEL, revision=REVISION)
